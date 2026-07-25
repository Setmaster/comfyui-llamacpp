from __future__ import annotations

import json
import unittest
import uuid
from dataclasses import asdict
from unittest.mock import patch

from runtime.client import (
    AuthConfig,
    ConnectionConfig,
    Deadline,
    LlamaServerClient,
    ModelState,
    StreamControl,
    TLSConfig,
)
from runtime.streaming import (
    PROTOCOL_FAILURE_MESSAGE,
    PromptProgress,
    StreamUpdate,
    iter_model_events,
    iter_sse_events,
    stream_chat,
)

_PINNED_IMAGE_UNSUPPORTED_MESSAGE = (
    "image input is not supported - hint: if this is unexpected, you may need to provide the mmproj"
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class FakeResponse:
    def __init__(
        self,
        lines=(),
        *,
        status_code=200,
        payload=None,
        text="",
        before_line=None,
        content_chunks=None,
    ) -> None:
        self.lines = list(lines)
        self.status_code = status_code
        self.payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.headers = {}
        self.before_line = before_line
        self.content_chunks = list(content_chunks) if content_chunks is not None else None
        self.closed = False
        self.iter_lines_decode_unicode: list[bool] = []
        self.iter_content_chunk_sizes: list[int] = []
        self.content_chunks_yielded = 0

    def json(self):
        if self.payload is None:
            raise ValueError("no JSON")
        return self.payload

    def iter_lines(self, decode_unicode=False):
        self.iter_lines_decode_unicode.append(decode_unicode)
        for line in self.lines:
            if self.before_line:
                self.before_line()
            if decode_unicode and isinstance(line, bytes):
                # Match the failure mode requests exposes for
                # text/event-stream without an explicit charset.
                yield line.decode("latin-1")
            else:
                yield line

    def iter_content(self, chunk_size=1):
        self.iter_content_chunk_sizes.append(chunk_size)
        if self.content_chunks is not None:
            chunks = iter(self.content_chunks)
        elif self.lines:

            def line_chunks():
                for line in self.lines:
                    if self.before_line:
                        self.before_line()
                    yield (line if isinstance(line, bytes) else line.encode("utf-8")) + b"\n"

            chunks = line_chunks()
        else:
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
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response

    def close(self) -> None:
        self.closed = True


def data_line(payload) -> str:
    return f"data: {json.dumps(payload)}"


class SSEParserTests(unittest.TestCase):
    def test_comments_multiline_data_and_metadata(self) -> None:
        events = list(
            iter_sse_events(
                [
                    ": ping",
                    "event: update",
                    "id: 42",
                    "retry: 1000",
                    "data: first",
                    "data: second",
                    "",
                ]
            )
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data, "first\nsecond")
        self.assertEqual(events[0].event, "update")
        self.assertEqual(events[0].id, "42")
        self.assertEqual(events[0].retry, 1000)

    def test_pending_event_is_dispatched_at_eof(self) -> None:
        events = list(iter_sse_events([b"data: final"]))
        self.assertEqual(events[0].data, "final")


class ChatStreamingTests(unittest.TestCase):
    def connection(self) -> ConnectionConfig:
        return ConnectionConfig(
            "https://localhost:8080",
            auth=AuthConfig("top-secret"),
            tls=TLSConfig(verify=False),
            connect_timeout=2,
            read_timeout=5,
        )

    def test_complete_stream_collects_content_reasoning_usage_and_finish(self) -> None:
        chunks = []
        lines = [
            data_line(
                {
                    "id": "chat-1",
                    "model": "vlm",
                    "choices": [{"delta": {"reasoning_content": "think ", "content": "Hello "}}],
                }
            ),
            "",
            data_line({"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]}),
            "",
            data_line({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
            "",
            "data: [DONE]",
            "",
        ]
        response = FakeResponse(lines)
        session = FakeSession(response)
        result = stream_chat(
            self.connection(),
            {"messages": [], "stream": True},
            session=session,
            on_chunk=lambda content, reasoning: chunks.append((content, reasoning)),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response, "Hello world")
        self.assertEqual(result.thinking, "think ")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.usage["completion_tokens"], 2)
        self.assertEqual(result.response_id, "chat-1")
        self.assertTrue(result.done_received)
        self.assertFalse(result.partial)
        self.assertEqual(chunks, [("Hello ", "think "), ("world", "")])
        self.assertTrue(response.closed)
        self.assertFalse(session.closed)
        method, url, kwargs = session.calls[0]
        self.assertEqual((method, url), ("POST", "https://localhost:8080/v1/chat/completions"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer top-secret")
        self.assertFalse(kwargs["verify"])
        self.assertTrue(kwargs["stream"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("X-Conversation-Id", kwargs["headers"])
        self.assertNotIn("return_progress", kwargs["json"])
        self.assertNotIn("sse_ping_interval", kwargs["json"])

    def test_complete_stream_preserves_exact_content_and_reasoning_whitespace(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "  leading",
                                    "reasoning_content": "\nthink ",
                                }
                            }
                        ]
                    }
                ),
                "",
                data_line(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "\ntrailing  ",
                                    "reasoning_content": "tail\n",
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ),
                "",
            ]
        )

        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))

        self.assertTrue(result.success)
        self.assertEqual(result.response, "  leading\ntrailing  ")
        self.assertEqual(result.thinking, "\nthink tail\n")
        self.assertFalse(result.partial)

    def test_utf8_sse_bytes_do_not_use_requests_inferred_latin1(self) -> None:
        expected = "café — naïve – it’s"
        payload = {
            "choices": [
                {
                    "delta": {"content": expected},
                    "finish_reason": "stop",
                }
            ]
        }
        response = FakeResponse(
            [
                f"data: {json.dumps(payload, ensure_ascii=False)}".encode(),
                b"",
            ]
        )

        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))

        self.assertTrue(result.success)
        self.assertEqual(result.response, expected)
        self.assertEqual(response.iter_lines_decode_unicode, [])
        self.assertTrue(response.iter_content_chunk_sizes)

    def test_finish_reason_is_terminal_without_done_marker(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {"choices": [{"delta": {"content": "complete"}, "finish_reason": "length"}]}
                ),
                "",
            ]
        )
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertTrue(result.success)
        self.assertFalse(result.done_received)
        self.assertEqual(result.finish_reason, "length")

    def test_done_marker_allows_empty_success(self) -> None:
        response = FakeResponse(["data: [DONE]", ""])
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertTrue(result.success)
        self.assertEqual(result.response, "")

    def test_unterminated_eof_returns_partial_metadata(self) -> None:
        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "partial"}}]}),
                "",
            ]
        )
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertEqual(result.response, "partial")
        self.assertEqual(result.error_type, "incomplete")
        self.assertTrue(response.closed)

    def test_unterminated_partial_stream_preserves_exact_whitespace(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "\n  partial  \n",
                                    "reasoning_content": "  unfinished thought ",
                                }
                            }
                        ]
                    }
                ),
                "",
            ]
        )

        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))

        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertEqual(result.response, "\n  partial  \n")
        self.assertEqual(result.thinking, "  unfinished thought ")
        self.assertEqual(result.error_type, "incomplete")

    def test_server_error_preserves_partial_content_and_redacts_secret(self) -> None:
        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "  partial error\n"}}]}),
                "",
                data_line({"error": {"message": "key top-secret rejected"}}),
                "",
            ]
        )
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertFalse(result.success)
        self.assertTrue(result.partial)
        self.assertEqual(result.response, "  partial error\n")
        self.assertEqual(result.error_type, "server")
        self.assertNotIn("top-secret", result.error_message)

    def test_cancellation_closes_response_and_returns_partial(self) -> None:
        should_cancel = False

        def on_chunk(content, reasoning):
            nonlocal should_cancel
            should_cancel = True

        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "first"}}]}),
                "",
                data_line({"choices": [{"delta": {"content": "second"}}]}),
                "",
            ]
        )
        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            on_chunk=on_chunk,
            cancel=lambda: should_cancel,
        )
        self.assertFalse(result.success)
        self.assertTrue(result.cancelled)
        self.assertTrue(result.partial)
        self.assertEqual(result.response, "first")
        self.assertTrue(response.closed)

    def test_monotonic_overall_deadline_is_checked_between_lines(self) -> None:
        clock = FakeClock()

        def advance() -> None:
            clock.now += 0.6

        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "first"}}]}),
                "",
                data_line({"choices": [{"delta": {"content": "late"}}]}),
                "",
            ],
            before_line=advance,
        )
        deadline = Deadline(1.0, clock.monotonic)
        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            deadline=deadline,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "timeout")
        self.assertTrue(response.closed)

    def test_http_error_is_typed_and_closed(self) -> None:
        response = FakeResponse(
            status_code=401,
            payload={"error": {"message": "invalid top-secret"}},
        )
        result = stream_chat(self.connection(), {"stream": True}, session=FakeSession(response))
        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.error_type, "http")
        self.assertNotIn("top-secret", result.error_message)
        self.assertTrue(response.closed)

    def test_exact_bounded_unsupported_image_http_error_is_safely_classified(self) -> None:
        response = FakeResponse(
            status_code=400,
            payload={
                "error": {
                    "code": 400,
                    "message": _PINNED_IMAGE_UNSUPPORTED_MESSAGE,
                    "type": "invalid_request_error",
                }
            },
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.error_type, "image_unsupported")
        self.assertEqual(result.error_message, "llama-server rejected image input")
        self.assertNotIn("provide the mmproj", result.error_message)
        self.assertTrue(response.closed)

    def test_unmatched_unsupported_image_http_bodies_remain_generic(self) -> None:
        exact_error = {
            "code": 400,
            "message": _PINNED_IMAGE_UNSUPPORTED_MESSAGE,
            "type": "invalid_request_error",
        }
        cases = (
            (
                "wrong status",
                FakeResponse(status_code=500, payload={"error": exact_error}),
            ),
            (
                "wrong code",
                FakeResponse(
                    status_code=400,
                    payload={"error": {**exact_error, "code": 500}},
                ),
            ),
            (
                "wrong type",
                FakeResponse(
                    status_code=400,
                    payload={"error": {**exact_error, "type": "server_error"}},
                ),
            ),
            (
                "wrong message case",
                FakeResponse(
                    status_code=400,
                    payload={
                        "error": {
                            **exact_error,
                            "message": _PINNED_IMAGE_UNSUPPORTED_MESSAGE.capitalize(),
                        }
                    },
                ),
            ),
            (
                "same prefix but different message",
                FakeResponse(
                    status_code=400,
                    payload={
                        "error": {
                            **exact_error,
                            "message": ("image input is not supported: hostile-body-sentinel"),
                        }
                    },
                ),
            ),
            (
                "extra field",
                FakeResponse(
                    status_code=400,
                    payload={"error": {**exact_error, "body": "hostile-body-sentinel"}},
                ),
            ),
            (
                "duplicate key",
                FakeResponse(
                    status_code=400,
                    content_chunks=[
                        (
                            b'{"error":{"code":400,"code":400,'
                            b'"message":"image input is not supported - hint: if this is '
                            b'unexpected, you may need to provide the mmproj",'
                            b'"type":"invalid_request_error"}}'
                        )
                    ],
                ),
            ),
            (
                "invalid utf8",
                FakeResponse(status_code=400, content_chunks=[b"\xffhostile-body-sentinel"]),
            ),
        )

        for label, response in cases:
            with self.subTest(label=label):
                result = stream_chat(
                    self.connection(),
                    {"stream": True},
                    session=FakeSession(response),
                    strict_protocol=True,
                )
                self.assertFalse(result.success)
                self.assertEqual(result.error_type, "http")
                self.assertNotIn("hostile-body-sentinel", result.error_message)
                self.assertTrue(response.closed)

    def test_http_error_body_is_bounded_and_never_exposes_raw_sentinel(self) -> None:
        response = FakeResponse(
            status_code=500,
            content_chunks=[b"raw-http-error-sentinel" * 4096, b"must-not-be-read"],
        )
        session = FakeSession(response)

        result = stream_chat(self.connection(), {"stream": True}, session=session)

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "http")
        self.assertNotIn("raw-http-error-sentinel", result.error_message)
        self.assertEqual(response.content_chunks_yielded, 1)
        self.assertFalse(session.calls[0][2]["allow_redirects"])

    def test_oversized_unsupported_image_shape_is_never_classified_or_exposed(self) -> None:
        response = FakeResponse(
            status_code=400,
            content_chunks=[
                (
                    b'{"error":{"code":400,"message":"image input is not supported: '
                    + (b"oversized-image-error-sentinel" * 4096)
                    + b'","type":"invalid_request_error"}}'
                ),
                b"must-not-be-read",
            ],
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "http")
        self.assertNotIn("oversized-image-error-sentinel", result.error_message)
        self.assertEqual(response.content_chunks_yielded, 1)

    def test_deeply_nested_http_error_body_remains_generic(self) -> None:
        response = FakeResponse(status_code=400, content_chunks=[b'{"error":{}}'])

        with patch("runtime.streaming.json.loads", side_effect=RecursionError):
            result = stream_chat(
                self.connection(),
                {"stream": True},
                session=FakeSession(response),
                strict_protocol=True,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "http")
        self.assertEqual(result.error_message, "HTTP 400: stream request failed")
        self.assertTrue(response.closed)

    def test_strict_chat_404_has_typed_missing_model_context_without_raw_body(self) -> None:
        response = FakeResponse(
            status_code=404,
            payload={"error": {"message": "raw-model-and-secret-sentinel"}},
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.error_type, "model_missing")
        self.assertNotIn("raw-model-and-secret-sentinel", result.error_message)

    def test_legacy_http_image_error_keeps_the_generic_contract(self) -> None:
        response = FakeResponse(
            status_code=400,
            payload={
                "error": {
                    "code": 400,
                    "message": _PINNED_IMAGE_UNSUPPORTED_MESSAGE,
                    "type": "invalid_request_error",
                }
            },
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=False,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "http")
        self.assertEqual(result.error_message, "HTTP 400: stream request failed")

    def test_exact_bounded_unsupported_image_sse_error_is_safely_classified(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "error": {
                            "code": 400,
                            "message": _PINNED_IMAGE_UNSUPPORTED_MESSAGE,
                            "type": "invalid_request_error",
                        }
                    }
                ),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "image_unsupported")
        self.assertEqual(result.error_message, "llama-server rejected image input")
        self.assertNotIn("provide the mmproj", result.error_message)

    def test_legacy_sse_image_error_keeps_the_generic_contract(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "error": {
                            "code": 400,
                            "message": _PINNED_IMAGE_UNSUPPORTED_MESSAGE,
                            "type": "invalid_request_error",
                        }
                    }
                ),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=False,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "server")
        self.assertEqual(result.error_message, "server returned an error")

    def test_unmatched_sse_error_never_exposes_body_text(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "error": {
                            "code": 400,
                            "message": "hostile-stream-error-sentinel",
                            "type": "invalid_request_error",
                        }
                    }
                ),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "server")
        self.assertEqual(result.error_message, "server returned an error")
        self.assertNotIn("hostile-stream-error-sentinel", result.error_message)

    def test_duplicate_sse_error_keys_are_protocol_failure_not_capability(self) -> None:
        response = FakeResponse(
            [
                (
                    'data: {"error":{"code":400,"code":400,'
                    '"message":"image input is not supported - hint: if this is unexpected, '
                    'you may need to provide the mmproj",'
                    '"type":"invalid_request_error"}}'
                ),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "protocol")
        self.assertEqual(result.error_message, PROTOCOL_FAILURE_MESSAGE)

    def test_deeply_nested_sse_json_is_a_bounded_protocol_failure(self) -> None:
        response = FakeResponse(['data: {"error":{}}', ""])

        with patch("runtime.streaming.json.loads", side_effect=RecursionError):
            result = stream_chat(
                self.connection(),
                {"stream": True},
                session=FakeSession(response),
                strict_protocol=True,
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "protocol")
        self.assertEqual(result.error_message, PROTOCOL_FAILURE_MESSAGE)

    def test_strict_wire_line_and_event_limits_cleanup_exact_stream(self) -> None:
        cases = (
            (
                FakeResponse(content_chunks=[b"data: " + (b"x" * 65)]),
                {"max_line_bytes": 32, "max_event_bytes": 256},
            ),
            (
                FakeResponse(
                    content_chunks=[
                        b"data: 1234567890\n",
                        b"data: 1234567890\n",
                        b"\n",
                    ]
                ),
                {"max_line_bytes": 64, "max_event_bytes": 24},
            ),
        )
        for response, limits in cases:
            with self.subTest(limits=limits):
                connection = self.connection()
                deletes = []
                control = StreamControl(
                    connection,
                    uuid.uuid4(),
                    _delete=lambda identity, _timeout, sink=deletes: sink.append(identity) or True,
                )

                result = stream_chat(
                    connection,
                    {"stream": True},
                    session=FakeSession(response),
                    stream_control=control,
                    strict_protocol=True,
                    **limits,
                )

                self.assertFalse(result.success)
                self.assertEqual(result.error_type, "resource")
                self.assertEqual(
                    result.error_message, "stream response exceeded a configured safety limit"
                )
                self.assertEqual(deletes, [control.conversation_id])
                self.assertTrue(result.stream_cleanup.confirmed)
                self.assertTrue(response.closed)

    def test_strict_many_tiny_chunks_bound_response_and_thinking_accumulation(self) -> None:
        for field, limit_name, expected_response, expected_thinking in (
            ("content", "max_response_bytes", "xxx", ""),
            ("reasoning_content", "max_thinking_bytes", "", "xxx"),
        ):
            with self.subTest(field=field):
                lines = []
                for _ in range(4):
                    lines.extend([data_line({"choices": [{"delta": {field: "x"}}]}), ""])
                response = FakeResponse(lines)
                connection = self.connection()
                deletes = []
                control = StreamControl(
                    connection,
                    uuid.uuid4(),
                    _delete=lambda identity, _timeout, sink=deletes: sink.append(identity) or True,
                )

                result = stream_chat(
                    connection,
                    {"stream": True},
                    session=FakeSession(response),
                    stream_control=control,
                    strict_protocol=True,
                    **{limit_name: 3},
                )

                self.assertEqual(result.error_type, "resource")
                self.assertEqual(result.response, expected_response)
                self.assertEqual(result.thinking, expected_thinking)
                self.assertTrue(result.partial)
                self.assertEqual(result.chunks, 4)
                self.assertEqual(deletes, [control.conversation_id])
                self.assertTrue(result.stream_cleanup.confirmed)

    def test_strict_output_limits_count_utf8_bytes_across_chunks(self) -> None:
        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "é"}}]}),
                "",
                data_line({"choices": [{"delta": {"content": "é"}}]}),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
            max_response_bytes=3,
        )

        self.assertEqual(result.error_type, "resource")
        self.assertEqual(result.response, "é")
        self.assertTrue(result.partial)

    def test_strict_stream_fields_reject_objects_and_surrogates_generically(self) -> None:
        malformed_chunks = (
            {"choices": [{"delta": {"content": {"raw-field-sentinel": True}}}]},
            {"choices": [{"delta": {"reasoning_content": ["raw-field-sentinel"]}}]},
            {"model": {"raw-field-sentinel": True}, "choices": []},
            {"id": ["raw-field-sentinel"], "choices": []},
            {"choices": [{"delta": {}, "finish_reason": {"raw-field-sentinel": True}}]},
            {"choices": [{"delta": {"content": "\ud800"}}]},
        )
        for malformed in malformed_chunks:
            with self.subTest(malformed=repr(malformed)):
                response = FakeResponse([data_line(malformed), ""])
                connection = self.connection()
                deletes = []
                control = StreamControl(
                    connection,
                    uuid.uuid4(),
                    _delete=lambda identity, _timeout, sink=deletes: sink.append(identity) or True,
                )

                result = stream_chat(
                    connection,
                    {"stream": True},
                    session=FakeSession(response),
                    stream_control=control,
                    strict_protocol=True,
                )

                self.assertFalse(result.success)
                self.assertEqual(result.error_type, "protocol")
                self.assertEqual(
                    result.error_message, "stream response violated the expected protocol"
                )
                self.assertNotIn("raw-field-sentinel", result.error_message)
                self.assertEqual(deletes, [control.conversation_id])
                self.assertTrue(result.stream_cleanup.confirmed)

    def test_strict_stream_fields_are_individually_bounded_and_accept_null(self) -> None:
        cases = (
            ({"id": "12345", "choices": []}, {"max_id_bytes": 4}),
            ({"model": "12345", "choices": []}, {"max_model_bytes": 4}),
            (
                {"choices": [{"delta": {}, "finish_reason": "12345"}]},
                {"max_finish_reason_bytes": 4},
            ),
        )
        for chunk, limits in cases:
            with self.subTest(limits=limits):
                result = stream_chat(
                    self.connection(),
                    {"stream": True},
                    session=FakeSession(FakeResponse([data_line(chunk), ""])),
                    strict_protocol=True,
                    **limits,
                )
                self.assertEqual(result.error_type, "resource")

        null_chunk = {
            "id": None,
            "model": None,
            "choices": [
                {
                    "delta": {"content": None, "reasoning_content": None},
                    "finish_reason": None,
                }
            ],
        }
        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(FakeResponse([data_line(null_chunk), "", "data: [DONE]", ""])),
            strict_protocol=True,
        )
        self.assertTrue(result.success)

    def test_strict_stream_rejects_model_change_after_partial_content(self) -> None:
        connection = self.connection()
        deletes = []
        control = StreamControl(
            connection,
            uuid.uuid4(),
            _delete=lambda identity, _timeout: deletes.append(identity) or True,
        )
        response = FakeResponse(
            [
                data_line(
                    {
                        "model": "model-b",
                        "choices": [{"delta": {"content": "partial"}}],
                    }
                ),
                "",
                data_line(
                    {
                        "model": "model-a",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                    }
                ),
                "",
            ]
        )

        result = stream_chat(
            connection,
            {"stream": True},
            session=FakeSession(response),
            stream_control=control,
            strict_protocol=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "protocol")
        self.assertEqual(
            result.error_message,
            "stream response violated the expected protocol",
        )
        self.assertEqual(result.response, "partial")
        self.assertTrue(result.partial)
        self.assertEqual(result.model, "model-b")
        self.assertEqual(deletes, [control.conversation_id])
        self.assertTrue(result.stream_cleanup.confirmed)
        self.assertTrue(response.closed)

    def test_strict_stream_rejects_response_id_change(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "id": "chat-b",
                        "choices": [{"delta": {"content": "partial"}}],
                    }
                ),
                "",
                data_line(
                    {
                        "id": "chat-a",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                    }
                ),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_type, "protocol")
        self.assertEqual(
            result.error_message,
            "stream response violated the expected protocol",
        )
        self.assertEqual(result.response_id, "chat-b")
        self.assertEqual(result.response, "partial")
        self.assertTrue(result.partial)
        self.assertTrue(response.closed)

    def test_strict_stream_accepts_repeated_identity_values(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "id": "chat-one",
                        "model": "model-one",
                        "choices": [{"delta": {"content": "answer"}}],
                    }
                ),
                "",
                data_line(
                    {
                        "id": "chat-one",
                        "model": "model-one",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                    }
                ),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response, "answer")
        self.assertEqual(result.response_id, "chat-one")
        self.assertEqual(result.model, "model-one")

    def test_strict_stream_accepts_identity_first_seen_after_missing_values(self) -> None:
        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "answer"}}]}),
                "",
                data_line(
                    {
                        "id": "chat-late",
                        "model": "model-late",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                    }
                ),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            strict_protocol=True,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response_id, "chat-late")
        self.assertEqual(result.model, "model-late")

    def test_legacy_stream_retains_last_identity_value(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "id": "chat-one",
                        "model": "model-one",
                        "choices": [{"delta": {"content": "answer"}}],
                    }
                ),
                "",
                data_line(
                    {
                        "id": "chat-two",
                        "model": "model-two",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                    }
                ),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.response_id, "chat-two")
        self.assertEqual(result.model, "model-two")

    def test_invalid_json_is_generic_and_typed_in_strict_client_path(self) -> None:
        response = FakeResponse(["data: raw-json-sentinel", ""])
        session = FakeSession(response)
        client = LlamaServerClient(self.connection(), session=session)

        result = client.stream_chat({"stream": True})

        self.assertEqual(result.error_type, "protocol")
        self.assertEqual(result.error_message, "stream response violated the expected protocol")
        self.assertNotIn("raw-json-sentinel", result.error_message)
        self.assertTrue(response.iter_content_chunk_sizes)

    def test_rich_update_callback_normalizes_prompt_progress_without_changing_legacy_chunks(
        self,
    ) -> None:
        legacy = []
        updates: list[StreamUpdate] = []
        response = FakeResponse(
            [
                data_line(
                    {
                        "id": "chat-progress",
                        "model": "model-one",
                        "prompt_progress": {
                            "total": 10,
                            "cache": 2,
                            "processed": 5,
                            "time_ms": 15,
                        },
                        "choices": [],
                    }
                ),
                "",
                data_line(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "answer",
                                    "reasoning_content": "thought",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 1,
                            "total_tokens": 11,
                        },
                    }
                ),
                "",
            ]
        )

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            on_chunk=lambda content, thinking: legacy.append((content, thinking)),
            on_update=updates.append,
        )

        self.assertTrue(result.success)
        self.assertEqual(legacy, [("answer", "thought")])
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0].prompt_progress, PromptProgress(10, 2, 5, 15))
        self.assertEqual(updates[0].prompt_progress.percent, 50.0)
        self.assertEqual(updates[1].content, "answer")
        self.assertEqual(updates[1].thinking, "thought")
        self.assertEqual(updates[1].finish_reason, "stop")
        self.assertEqual(result.prompt_progress, PromptProgress(10, 2, 5, 15))

    def test_observability_callback_failure_is_fail_open(self) -> None:
        response = FakeResponse(
            [
                data_line({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
                "",
            ]
        )

        def fail(_update: StreamUpdate) -> None:
            raise RuntimeError("UI callback failed")

        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            on_update=fail,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.response, "ok")

    def test_supported_stream_control_adds_exact_fields_and_deletes_in_finally(self) -> None:
        deletes: list[tuple[uuid.UUID, float]] = []
        connection = self.connection()
        conversation_id = uuid.uuid4()
        control = StreamControl(
            connection,
            conversation_id,
            _delete=lambda identity, timeout: deletes.append((identity, timeout)) or True,
        )
        original_payload = {"messages": [], "stream": True}
        response = FakeResponse(["data: [DONE]", ""])
        session = FakeSession(response)

        result = stream_chat(
            connection,
            original_payload,
            session=session,
            stream_control=control,
        )

        self.assertTrue(result.success)
        self.assertEqual(original_payload, {"messages": [], "stream": True})
        _, _, kwargs = session.calls[0]
        self.assertEqual(kwargs["headers"]["X-Conversation-Id"], str(conversation_id))
        self.assertIs(kwargs["json"]["return_progress"], True)
        self.assertEqual(kwargs["json"]["sse_ping_interval"], 1)
        self.assertEqual(deletes, [(conversation_id, 2.0)])
        self.assertIsNotNone(result.stream_cleanup)
        self.assertEqual(
            result.stream_cleanup.as_dict(),
            {
                "attempts": 1,
                "confirmed": True,
                "failures": 0,
                "last_error_type": None,
            },
        )
        self.assertEqual(asdict(result)["stream_cleanup"]["confirmed"], True)
        self.assertTrue(response.closed)

    def test_conversation_header_cannot_bypass_or_duplicate_the_capability_gate(self) -> None:
        connection = ConnectionConfig(
            "http://localhost:8080",
            default_headers=(("x-conversation-id", "user-supplied"),),
        )
        unsupported_session = FakeSession(FakeResponse(["data: [DONE]", ""]))
        result = stream_chat(
            connection,
            {"stream": True},
            session=unsupported_session,
        )
        self.assertTrue(result.success)
        unsupported_headers = unsupported_session.calls[0][2]["headers"]
        self.assertFalse(any(key.lower() == "x-conversation-id" for key in unsupported_headers))

        control = StreamControl(
            connection,
            uuid.uuid4(),
            _delete=lambda _identity, _timeout: True,
        )
        supported_session = FakeSession(FakeResponse(["data: [DONE]", ""]))
        result = stream_chat(
            connection,
            {"stream": True},
            session=supported_session,
            stream_control=control,
        )
        self.assertTrue(result.success)
        supported_headers = supported_session.calls[0][2]["headers"]
        conversation_headers = [
            (key, value)
            for key, value in supported_headers.items()
            if key.lower() == "x-conversation-id"
        ]
        self.assertEqual(
            conversation_headers,
            [("X-Conversation-Id", str(control.conversation_id))],
        )

    def test_stream_control_cleanup_runs_on_http_error_cancel_timeout_and_base_exception(
        self,
    ) -> None:
        class ComfyInterrupt(BaseException):
            pass

        cases = []
        cases.append((FakeResponse(status_code=500, payload={"error": "bad"}), None, None))

        should_cancel = False

        def mark_cancel(_content: str, _thinking: str) -> None:
            nonlocal should_cancel
            should_cancel = True

        cases.append(
            (
                FakeResponse([data_line({"choices": [{"delta": {"content": "partial"}}]}), ""]),
                mark_cancel,
                lambda: should_cancel,
            )
        )

        clock = FakeClock()

        def advance() -> None:
            clock.now += 2

        timeout_response = FakeResponse([": ping", ""], before_line=advance)

        for index, (response, on_chunk, cancel) in enumerate(cases):
            with self.subTest(case=index):
                deletes = []
                connection = self.connection()
                control = StreamControl(
                    connection,
                    uuid.uuid4(),
                    _delete=lambda identity, timeout, sink=deletes: sink.append(identity) or True,
                )
                stream_chat(
                    connection,
                    {"stream": True},
                    session=FakeSession(response),
                    on_chunk=on_chunk,
                    cancel=cancel,
                    stream_control=control,
                )
                self.assertEqual(deletes, [control.conversation_id])
                self.assertTrue(response.closed)

        timeout_deletes = []
        timeout_control = StreamControl(
            self.connection(),
            uuid.uuid4(),
            _delete=lambda identity, timeout: timeout_deletes.append(identity) or True,
        )
        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(timeout_response),
            deadline=Deadline(1, clock.monotonic),
            stream_control=timeout_control,
        )
        self.assertEqual(result.error_type, "timeout")
        self.assertEqual(timeout_deletes, [timeout_control.conversation_id])
        self.assertTrue(timeout_response.closed)

        interrupt_response = FakeResponse([": ping", ""])
        interrupt_deletes = []
        interrupt_control = StreamControl(
            self.connection(),
            uuid.uuid4(),
            _delete=lambda identity, timeout: interrupt_deletes.append(identity) or True,
        )

        interrupt_checks = 0

        def interrupt() -> bool:
            nonlocal interrupt_checks
            interrupt_checks += 1
            if interrupt_checks > 1:
                raise ComfyInterrupt()
            return False

        with self.assertRaises(ComfyInterrupt):
            stream_chat(
                self.connection(),
                {"stream": True},
                session=FakeSession(interrupt_response),
                cancel=interrupt,
                stream_control=interrupt_control,
            )
        self.assertEqual(interrupt_deletes, [interrupt_control.conversation_id])
        self.assertTrue(interrupt_control.cleanup.confirmed)
        self.assertTrue(interrupt_response.closed)

    def test_stream_control_cleanup_covers_request_and_callback_base_exceptions(self) -> None:
        class ComfyInterrupt(BaseException):
            pass

        class InterruptingSession(FakeSession):
            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                raise ComfyInterrupt()

        def interrupt_chunk(_content: str, _thinking: str) -> None:
            raise ComfyInterrupt()

        def interrupt_update(_update: StreamUpdate) -> None:
            raise ComfyInterrupt()

        scenarios = (
            (InterruptingSession(FakeResponse()), None, None, None),
            (
                FakeSession(
                    FakeResponse([data_line({"choices": [{"delta": {"content": "token"}}]}), ""])
                ),
                interrupt_chunk,
                None,
                "response",
            ),
            (
                FakeSession(
                    FakeResponse([data_line({"choices": [{"delta": {"content": "token"}}]}), ""])
                ),
                None,
                interrupt_update,
                "response",
            ),
        )
        for index, (session, on_chunk, on_update, response_marker) in enumerate(scenarios):
            with self.subTest(scenario=index):
                deletes = []
                connection = self.connection()
                control = StreamControl(
                    connection,
                    uuid.uuid4(),
                    _delete=lambda identity, _timeout, sink=deletes: sink.append(identity) or True,
                )
                with self.assertRaises(ComfyInterrupt):
                    stream_chat(
                        connection,
                        {"stream": True},
                        session=session,
                        on_chunk=on_chunk,
                        on_update=on_update,
                        stream_control=control,
                    )
                self.assertEqual(deletes, [control.conversation_id])
                self.assertTrue(control.cleanup.confirmed)
                if response_marker is not None:
                    self.assertTrue(session.response.closed)

    def test_stream_cleanup_failure_never_masks_the_generation_result(self) -> None:
        response = FakeResponse(["data: [DONE]", ""])
        control = StreamControl(
            self.connection(),
            uuid.uuid4(),
            _delete=lambda _identity, _timeout: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
            stream_control=control,
        )
        self.assertTrue(result.success)
        self.assertEqual(
            result.stream_cleanup.as_dict(),
            {
                "attempts": 1,
                "confirmed": False,
                "failures": 1,
                "last_error_type": "RuntimeError",
            },
        )
        self.assertTrue(response.closed)

    def test_response_close_failure_does_not_mask_the_generation_result(self) -> None:
        class BrokenCloseResponse(FakeResponse):
            def close(self) -> None:
                self.closed = True
                raise RuntimeError("close failed")

        response = BrokenCloseResponse(["data: [DONE]", ""])
        result = stream_chat(
            self.connection(),
            {"stream": True},
            session=FakeSession(response),
        )
        self.assertTrue(result.success)
        self.assertTrue(response.closed)


class ModelEventStreamingTests(unittest.TestCase):
    def test_current_status_change_and_documented_model_status_are_both_typed(self) -> None:
        lines = [
            data_line({"model": "one", "event": "model_status", "data": {"status": "loading"}}),
            "",
            data_line({"model": "one", "event": "status_change", "data": {"status": "loaded"}}),
            "",
        ]
        response = FakeResponse(lines)
        session = FakeSession(response)
        connection = ConnectionConfig("http://localhost:8080", auth=AuthConfig("key"))
        events = list(iter_model_events(connection, session=session, timeout=5))

        self.assertEqual([event.event for event in events], ["model_status", "status_change"])
        self.assertEqual([event.state for event in events], [ModelState.LOADING, ModelState.LOADED])
        self.assertEqual(session.calls[0][2]["headers"]["Authorization"], "Bearer key")
        self.assertTrue(response.closed)

    def test_nonzero_unloaded_exit_is_normalized_as_failed(self) -> None:
        response = FakeResponse(
            [
                data_line(
                    {
                        "model": "one",
                        "event": "status_change",
                        "data": {"status": "unloaded", "exit_code": 9},
                    }
                ),
                "",
            ]
        )
        events = list(
            iter_model_events(
                ConnectionConfig("http://localhost:8080"),
                session=FakeSession(response),
                timeout=5,
            )
        )
        self.assertEqual(events[0].state, ModelState.FAILED)

    def test_model_event_stream_uses_explicit_utf8_decoding(self) -> None:
        payload = {
            "model": "modèle—vision",
            "event": "status_change",
            "data": {"status": "loaded"},
        }
        response = FakeResponse(
            [
                f"data: {json.dumps(payload, ensure_ascii=False)}".encode(),
                b"",
            ]
        )

        events = list(
            iter_model_events(
                ConnectionConfig("http://localhost:8080"),
                session=FakeSession(response),
                timeout=5,
            )
        )

        self.assertEqual(events[0].model, "modèle—vision")
        self.assertEqual(response.iter_lines_decode_unicode, [])
        self.assertTrue(response.iter_content_chunk_sizes)


if __name__ == "__main__":
    unittest.main()
