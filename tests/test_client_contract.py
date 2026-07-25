from __future__ import annotations

import json
import unittest
import uuid
from unittest import mock

import runtime.client as client_module
from runtime.client import (
    AmbiguousModelError,
    AuthConfig,
    ConnectionConfig,
    DeadlineExceeded,
    LlamaClientError,
    LlamaServerClient,
    ModelNotFoundError,
    ModelState,
    ResponseBodyLimitError,
    ResponseProtocolError,
    RouterModel,
    StreamControlSupport,
    TLSConfig,
    clear_stream_control_cache,
    parse_router_model,
    redact_secrets,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.now += duration


class FakeResponse:
    def __init__(
        self,
        status_code=200,
        payload=None,
        *,
        text=None,
        headers=None,
        content_chunks=None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = json.dumps(payload) if text is None and payload is not None else (text or "")
        self.headers = headers or {}
        self.content_chunks = list(content_chunks) if content_chunks is not None else None
        self.iter_content_calls: list[int] = []
        self.content_chunks_yielded = 0
        self.closed = False

    def json(self):
        if self.payload is None:
            raise ValueError("no JSON")
        return self.payload

    def iter_content(self, chunk_size=1):
        self.iter_content_calls.append(chunk_size)
        chunks = self.content_chunks
        if chunks is None:
            encoded = self.text.encode("utf-8")
            chunks = [
                encoded[index : index + chunk_size] for index in range(0, len(encoded), chunk_size)
            ]
        for chunk in chunks:
            self.content_chunks_yielded += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def model_payload(state: str, *, failed=False, aliases=None, exit_code=None):
    status = {"value": state, "failed": failed}
    if exit_code is not None:
        status["exit_code"] = exit_code
    return {
        "data": [
            {
                "id": "vision-model",
                "aliases": aliases or ["vlm"],
                "status": status,
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
            }
        ]
    }


class ConnectionContractTests(unittest.TestCase):
    def test_connection_normalizes_url_auth_tls_and_hides_secret(self) -> None:
        config = ConnectionConfig(
            "https://localhost:8443/api/",
            auth=AuthConfig("super-secret"),
            tls=TLSConfig(verify="ca.pem", cert=("client.pem", "client.key")),
            default_headers=(("User-Agent", "tests"),),
        )
        self.assertEqual(config.base_url, "https://localhost:8443/api")
        self.assertEqual(config.url("/props"), "https://localhost:8443/api/props")
        self.assertEqual(config.request_headers()["Authorization"], "Bearer super-secret")
        self.assertNotIn("super-secret", repr(config))
        self.assertNotIn("super-secret", redact_secrets("Bearer super-secret", config.secrets))
        self.assertNotIn("super-secret", config.fingerprint)
        self.assertEqual(len(config.fingerprint), 64)

    def test_url_credentials_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ConnectionConfig("http://user:password@localhost:8080")

    def test_malformed_or_non_connectable_url_hosts_are_rejected(self) -> None:
        for value in (
            "http://::1:8080",
            "http://*:8080",
            "http://localhost:not-a-port",
            "http://localhost:99999",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ConnectionConfig(value)

        ipv6 = ConnectionConfig("http://[::1]:8080/")
        self.assertEqual(ipv6.base_url, "http://[::1]:8080")

    def test_default_auth_headers_are_hidden_and_managed_auth_wins(self) -> None:
        config = ConnectionConfig(
            default_headers=(("authorization", "Bearer stale-token"),),
            auth=AuthConfig("current-token"),
        )
        headers = config.request_headers()
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers["Authorization"], "Bearer current-token")
        self.assertNotIn(
            "stale-token",
            redact_secrets("stale-token current-token", config.secrets),
        )
        self.assertNotIn("stale-token", repr(config))


class TypedClientTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_stream_control_cache()

    def make_client(self, session: FakeSession, *, api_key="secret"):
        clock = FakeClock()
        connection = ConnectionConfig(
            "https://127.0.0.1:8080/",
            auth=AuthConfig(api_key),
            tls=TLSConfig(verify=False),
            connect_timeout=3,
            read_timeout=7,
            default_deadline=30,
        )
        return LlamaServerClient(
            connection,
            session=session,
            clock=clock.monotonic,
            sleeper=clock.sleep,
        ), clock

    def test_health_and_router_identity_use_auth_and_close_responses(self) -> None:
        health_response = FakeResponse(200, {"status": "ok"})
        props_response = FakeResponse(
            200, {"role": "router", "build_info": "b9957", "is_sleeping": False}
        )
        session = FakeSession(health_response, props_response)
        client, _ = self.make_client(session)

        health = client.health()
        props = client.props()

        self.assertTrue(health.ok)
        self.assertTrue(props.is_router)
        self.assertEqual(props.build_info, "b9957")
        self.assertTrue(health_response.closed)
        self.assertTrue(props_response.closed)
        for _, _, kwargs in session.calls:
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
            self.assertFalse(kwargs["verify"])
            self.assertTrue(kwargs["stream"])
            self.assertFalse(kwargs["allow_redirects"])

    def test_health_503_is_typed_loading_not_transport_failure(self) -> None:
        session = FakeSession(FakeResponse(503, {"error": {"message": "Loading model"}}))
        client, _ = self.make_client(session)
        result = client.health()
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Loading model")

    def test_props_model_and_autoload_are_query_parameters(self) -> None:
        session = FakeSession(FakeResponse(200, {"model_path": "m.gguf"}))
        client, _ = self.make_client(session)
        client.props("model/id", autoload=False)
        _, _, kwargs = session.calls[0]
        self.assertEqual(kwargs["params"], {"model": "model/id", "autoload": "false"})

    def test_passive_props_is_an_exact_no_autoload_wrapper(self) -> None:
        session = FakeSession(FakeResponse(200, {"model_path": "m.gguf"}))
        client, _ = self.make_client(session)
        result = client.passive_props("model/id")
        self.assertEqual(result.model_path, "m.gguf")
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("GET", "https://127.0.0.1:8080/props"))
        self.assertEqual(kwargs["params"], {"model": "model/id", "autoload": "false"})

    def test_props_accept_only_literal_json_booleans(self) -> None:
        payload = {
            "is_sleeping": "false",
            "modalities": {
                "text": True,
                "audio": False,
                "vision": "false",
                "tools": 1,
            },
        }
        client, _ = self.make_client(FakeSession(FakeResponse(200, payload)))

        props = client.props()

        self.assertFalse(props.is_sleeping)
        self.assertEqual(props.modalities, {"text": True, "audio": False})
        self.assertEqual(props.raw["is_sleeping"], "false")
        self.assertEqual(props.raw["modalities"]["vision"], "false")

    def test_router_state_parser_normalizes_failure_and_modalities(self) -> None:
        model = parse_router_model(model_payload("unloaded", failed=True, exit_code=9)["data"][0])
        self.assertEqual(model.state, ModelState.FAILED)
        self.assertTrue(model.failed)
        self.assertEqual(model.exit_code, 9)
        self.assertEqual(model.input_modalities, ("text", "image"))

    def test_malformed_model_entry_is_not_silently_dropped(self) -> None:
        session = FakeSession(FakeResponse(200, {"data": ["not-an-object"]}))
        client, _ = self.make_client(session)
        with self.assertRaises(LlamaClientError):
            client.models()

    def test_models_can_request_current_router_catalog_reload(self) -> None:
        session = FakeSession(
            FakeResponse(200, model_payload("unloaded")),
            FakeResponse(200, model_payload("unloaded")),
        )
        client, _ = self.make_client(session)

        client.models()
        client.list_models(reload=True)

        self.assertNotIn("params", session.calls[0][2])
        self.assertEqual(session.calls[1][2]["params"], {"reload": "1"})

    def test_exact_id_and_alias_resolution_never_guess(self) -> None:
        models = (
            RouterModel("alpha", ModelState.UNLOADED, aliases=("a",)),
            RouterModel("beta", ModelState.UNLOADED, aliases=("b",)),
        )
        self.assertEqual(LlamaServerClient.find_model("alpha", models).id, "alpha")
        self.assertEqual(LlamaServerClient.find_model("b", models).id, "beta")
        with self.assertRaises(ModelNotFoundError):
            LlamaServerClient.find_model("ALPHA", models)
        with self.assertRaises(AmbiguousModelError):
            LlamaServerClient.find_model(
                "shared",
                (
                    RouterModel("one", ModelState.UNLOADED, aliases=("shared",)),
                    RouterModel("two", ModelState.UNLOADED, aliases=("shared",)),
                ),
            )

    def test_tokenize_current_contract(self) -> None:
        response = FakeResponse(200, {"tokens": [{"id": 1, "piece": "hi"}]})
        session = FakeSession(response)
        client, _ = self.make_client(session)
        result = client.tokenize("hi", model="vision-model", with_pieces=True)
        self.assertEqual(result.count, 1)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://127.0.0.1:8080/tokenize"))
        self.assertEqual(kwargs["json"]["model"], "vision-model")
        self.assertTrue(kwargs["json"]["with_pieces"])

    def test_direct_load_acceptance_api_uses_only_current_endpoint(self) -> None:
        session = FakeSession(FakeResponse(200, {"success": True}))
        client, _ = self.make_client(session)
        self.assertTrue(client.request_load("vision-model")["success"])
        self.assertEqual(session.calls[0][1], "https://127.0.0.1:8080/models/load")

    def test_load_200_is_acceptance_then_polls_to_loaded(self) -> None:
        responses = (
            FakeResponse(200, model_payload("unloaded")),
            FakeResponse(200, {"success": True}),
            FakeResponse(200, model_payload("loading")),
            FakeResponse(200, model_payload("loaded")),
        )
        session = FakeSession(*responses)
        client, _ = self.make_client(session)
        result = client.load_model("vlm", poll_interval=0)

        self.assertTrue(result.accepted)
        self.assertTrue(result.success)
        self.assertEqual(result.model.id, "vision-model")
        self.assertEqual(result.model.state, ModelState.LOADED)
        methods_and_paths = [(method, url.rsplit("/", 1)[-1]) for method, url, _ in session.calls]
        self.assertEqual(
            methods_and_paths,
            [("GET", "models"), ("POST", "load"), ("GET", "models"), ("GET", "models")],
        )
        self.assertEqual(session.calls[1][1], "https://127.0.0.1:8080/models/load")
        self.assertFalse(any(method == "DELETE" for method, _, _ in session.calls))
        self.assertFalse(
            any(method == "POST" and url.endswith("/models") for method, url, _ in session.calls)
        )

    def test_unload_waits_for_child_exit_state(self) -> None:
        session = FakeSession(
            FakeResponse(200, model_payload("loaded")),
            FakeResponse(200, {"success": True}),
            FakeResponse(200, model_payload("loaded")),
            FakeResponse(200, model_payload("unloaded")),
        )
        client, _ = self.make_client(session)
        result = client.unload_model("vision-model", poll_interval=0)
        self.assertTrue(result.accepted)
        self.assertEqual(result.model.state, ModelState.UNLOADED)
        self.assertEqual(session.calls[1][1], "https://127.0.0.1:8080/models/unload")

    def test_idempotent_loaded_and_unloaded_models_skip_mutation(self) -> None:
        loaded_session = FakeSession(FakeResponse(200, model_payload("sleeping")))
        loaded_client, _ = self.make_client(loaded_session)
        loaded = loaded_client.load_model("vision-model")
        self.assertFalse(loaded.accepted)
        self.assertEqual(len(loaded_session.calls), 1)

        unloaded_session = FakeSession(FakeResponse(200, model_payload("unloaded")))
        unloaded_client, _ = self.make_client(unloaded_session)
        unloaded = unloaded_client.unload_model("vision-model")
        self.assertFalse(unloaded.accepted)
        self.assertEqual(len(unloaded_session.calls), 1)

    def test_polling_obeys_one_monotonic_deadline(self) -> None:
        session = FakeSession(
            FakeResponse(200, model_payload("unloaded")),
            FakeResponse(200, {"success": True}),
            FakeResponse(200, model_payload("loading")),
            FakeResponse(200, model_payload("loading")),
        )
        client, clock = self.make_client(session)
        with self.assertRaises(DeadlineExceeded):
            client.load_model("vision-model", timeout=1, poll_interval=0.6)
        self.assertAlmostEqual(clock.now, 1.0)

    def test_http_errors_are_redacted_and_response_is_closed(self) -> None:
        response = FakeResponse(401, {"error": {"message": "bad secret"}})
        session = FakeSession(response)
        client, _ = self.make_client(session)
        with self.assertRaises(LlamaClientError) as caught:
            client.props()
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsNone(caught.exception.body)
        self.assertTrue(response.closed)

    def test_redirects_are_disabled_for_credential_bearing_requests(self) -> None:
        response = FakeResponse(302, text="cross-origin-sentinel")
        session = FakeSession(response)
        client, _ = self.make_client(session)

        with self.assertRaises(LlamaClientError) as caught:
            client.props()

        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][2]["allow_redirects"])
        self.assertNotIn("cross-origin-sentinel", str(caught.exception))

    def test_success_json_body_is_bounded_before_materialization(self) -> None:
        response = FakeResponse(
            200,
            content_chunks=[b'{"value":"', b"x" * 40, b'"}'],
        )
        client, _ = self.make_client(FakeSession(response))

        with (
            mock.patch.object(client_module, "JSON_RESPONSE_MAX_BYTES", 24),
            self.assertRaises(ResponseBodyLimitError) as caught,
        ):
            client.props()

        self.assertEqual(caught.exception.endpoint, "/props")
        self.assertEqual(response.content_chunks_yielded, 2)
        self.assertTrue(response.closed)

    def test_malformed_json_and_error_bodies_never_escape_raw_sentinels(self) -> None:
        malformed = FakeResponse(200, text="malformed-json-sentinel")
        client, _ = self.make_client(FakeSession(malformed))
        with self.assertRaises(ResponseProtocolError) as malformed_error:
            client.props()
        self.assertNotIn("malformed-json-sentinel", str(malformed_error.exception))

        error = FakeResponse(
            500,
            content_chunks=[b"private-error-sentinel" * 8, b"must-not-be-read"],
        )
        client, _ = self.make_client(FakeSession(error))
        with (
            mock.patch.object(client_module, "ERROR_RESPONSE_MAX_BYTES", 16),
            self.assertRaises(LlamaClientError) as http_error,
        ):
            client.props()
        self.assertNotIn("private-error-sentinel", str(http_error.exception))
        self.assertIsNone(http_error.exception.body)
        self.assertEqual(error.content_chunks_yielded, 1)

    def test_recursive_json_is_a_sanitized_protocol_error(self) -> None:
        response = FakeResponse(200, text='{"modalities":{}}')
        client, _ = self.make_client(FakeSession(response))

        with (
            mock.patch.object(
                client_module.json,
                "loads",
                side_effect=RecursionError("private-recursion-sentinel"),
            ),
            self.assertRaises(ResponseProtocolError) as caught,
        ):
            client.props()

        self.assertEqual(caught.exception.endpoint, "/props")
        self.assertNotIn("private-recursion-sentinel", str(caught.exception))
        self.assertTrue(response.closed)

    def test_stream_control_probe_requires_list_and_caches_by_fingerprint(self) -> None:
        first = FakeResponse(200, [])
        second = FakeResponse(200, [])
        session = FakeSession(first, second)
        client, clock = self.make_client(session)

        initial = client.probe_stream_control()
        cached = client.probe_stream_control()

        self.assertEqual(initial.support, StreamControlSupport.SUPPORTED)
        self.assertIs(cached, initial)
        self.assertEqual(len(session.calls), 1)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://127.0.0.1:8080/v1/streams/lookup"))
        probe_id = kwargs["json"]["conversation_ids"][0]
        self.assertEqual(str(uuid.UUID(probe_id)), probe_id)
        self.assertTrue(first.closed)

        clock.now = 61.0
        refreshed = client.probe_stream_control()
        self.assertEqual(refreshed.support, StreamControlSupport.SUPPORTED)
        self.assertEqual(len(session.calls), 2)

    def test_stream_control_probe_distinguishes_unsupported_unknown_and_auth(self) -> None:
        unsupported_client, _ = self.make_client(FakeSession(FakeResponse(404, {})))
        unsupported = unsupported_client.probe_stream_control()
        self.assertEqual(unsupported.support, StreamControlSupport.UNSUPPORTED)
        self.assertEqual(unsupported.reason, "http_404")

        clear_stream_control_cache()
        malformed_client, _ = self.make_client(FakeSession(FakeResponse(200, {"data": []})))
        malformed = malformed_client.probe_stream_control()
        self.assertEqual(malformed.support, StreamControlSupport.UNKNOWN)
        self.assertEqual(malformed.reason, "invalid_response")

        clear_stream_control_cache()
        unknown_client, _ = self.make_client(
            FakeSession(FakeResponse(500, {"error": {"message": "temporary"}}))
        )
        unknown = unknown_client.probe_stream_control()
        self.assertEqual(unknown.support, StreamControlSupport.UNKNOWN)
        self.assertEqual(unknown.reason, "http_500")

        clear_stream_control_cache()
        auth_response = FakeResponse(401, {"error": {"message": "bad secret"}})
        auth_client, _ = self.make_client(FakeSession(auth_response))
        with self.assertRaises(LlamaClientError) as caught:
            auth_client.probe_stream_control()
        self.assertEqual(caught.exception.status_code, 401)
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(auth_response.closed)

    def test_stream_control_probe_cache_is_bounded(self) -> None:
        for index in range(client_module.STREAM_CONTROL_CACHE_MAX_ENTRIES + 5):
            connection = ConnectionConfig(f"http://127.0.0.1:{8000 + index}")
            client = LlamaServerClient(
                connection,
                session=FakeSession(FakeResponse(200, [])),
            )
            self.assertTrue(client.probe_stream_control().supported)
        self.assertLessEqual(
            len(client_module._STREAM_CONTROL_CACHE),
            client_module.STREAM_CONTROL_CACHE_MAX_ENTRIES,
        )

    def test_positive_probe_prepares_uuid4_control_but_unknown_does_not(self) -> None:
        supported_client, _ = self.make_client(FakeSession(FakeResponse(200, [])))
        probe, control = supported_client.prepare_stream_control()
        self.assertTrue(probe.supported)
        self.assertIsNotNone(control)
        self.assertEqual(control.conversation_id.version, 4)
        self.assertNotIn("secret", repr(control))

        clear_stream_control_cache()
        unknown_client, _ = self.make_client(FakeSession(FakeResponse(200, {})))
        probe, control = unknown_client.prepare_stream_control()
        self.assertEqual(probe.support, StreamControlSupport.UNKNOWN)
        self.assertIsNone(control)

    def test_delete_stream_is_exact_idempotent_contract_and_rejects_strings(self) -> None:
        response = FakeResponse(204)
        session = FakeSession(response)
        client, _ = self.make_client(session)
        conversation_id = uuid.uuid4()

        self.assertTrue(client.delete_stream(conversation_id))
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "DELETE")
        self.assertEqual(url, f"https://127.0.0.1:8080/v1/stream/{conversation_id}")
        self.assertNotIn("json", kwargs)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertTrue(response.closed)

        with self.assertRaises(TypeError):
            client.delete_stream(str(conversation_id))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            client.delete_stream(uuid.uuid1())

    def test_stream_control_cleanup_outcome_is_redacted_and_accumulates_retries(self) -> None:
        calls = 0
        client, _ = self.make_client(FakeSession(FakeResponse(200, [])))
        _, control = client.prepare_stream_control()
        self.assertIsNotNone(control)

        def delete(_identity, _timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("secret backend URL")
            return True

        object.__setattr__(control, "_delete", delete)
        with self.assertRaises(RuntimeError):
            control.delete()
        self.assertTrue(control.delete())
        self.assertEqual(
            control.cleanup.as_dict(),
            {
                "attempts": 2,
                "confirmed": True,
                "failures": 1,
                "last_error_type": "RuntimeError",
            },
        )
        self.assertNotIn("secret", repr(control.cleanup))


if __name__ == "__main__":
    unittest.main()
