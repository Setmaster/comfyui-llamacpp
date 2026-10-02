"""Real socket coverage for absolute JSON response deadlines and cleanup."""

from __future__ import annotations

import gzip
import json
import ssl
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

import requests

import runtime.client as client_module
from runtime.client import (
    ConnectionConfig,
    DeadlineExceeded,
    LlamaClientError,
    LlamaServerClient,
    ResponseBodyLimitError,
    ResponseProtocolError,
    TLSConfig,
)

_TLS_FIXTURE = Path(__file__).parent / "fixtures" / "http_deadline_tls.pem"


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.stopping = threading.Event()
        self.slow_started = threading.Event()
        self.connection_closed = threading.Event()
        self.accepted = 0
        self.paths: list[str] = []

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(2)
        self.accepted += 1
        return sock, address


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            self.server.connection_closed.set()

    def _trickle(self, data: bytes, *, interval: float = 0.035) -> None:
        self.server.slow_started.set()
        for value in data:
            self.wfile.write(bytes([value]))
            if self.server.stopping.wait(interval):
                return

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        self.server.paths.append(self.path)
        mode = path.split("/")[1]
        body = b'{"status":"ok"}'
        headers = b""
        status = 500 if mode in {"error", "error_slow"} else 200
        if mode.startswith("error"):
            body = b'{"error":"private-error-sentinel"}'
        elif mode == "utf8":
            body = json.dumps({"build_info": "café 日本"}, ensure_ascii=False).encode("utf-8")
        elif mode == "invalid_utf8":
            body = b'{"build_info":"\xff"}'
        elif mode in {"gzip", "gzip_slow"}:
            body = gzip.compress(b'{"value":"' + b"x" * 100_000 + b'"}')
            headers = b"Content-Encoding: gzip\r\n"
        elif mode == "concurrent" and path.endswith("/props"):
            body = b'{"build_info":"parallel"}'
        elif mode == "stream" and not path.endswith("/health"):
            headers = b"Content-Type: text/event-stream\r\n"
            if path.endswith("/models/sse"):
                body = b'data: {"model":"fixture","event":"loaded"}\n\n'
            else:
                body = (
                    b'data: {"choices":[{"delta":{"content":"hello"},'
                    b'"finish_reason":"stop"}]}\n\n'
                    b"data: [DONE]\n\n"
                )

        try:
            if mode == "headers":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                self._trickle(b"x" * 40)
                return
            if mode == "status":
                self._trickle(b"HTTP/1.1 200 OK\r\n")
                return
            if mode == "chunked":
                headers += b"Transfer-Encoding: chunked\r\n"
            else:
                headers += f"Content-Length: {len(body)}\r\n".encode("ascii")
            self.wfile.write(f"HTTP/1.1 {status} Test\r\n".encode("ascii") + headers + b"\r\n")
            if mode == "chunked":
                # A trickled chunk-size line also blocks the buffered parser.
                self._trickle(b"f;extension=" + b"x" * 40)
            elif mode in {"body", "error_slow", "gzip_slow"} or (
                mode == "concurrent" and path.endswith("/health")
            ):
                self._trickle(body)
            elif mode == "concurrent":
                self._trickle(body, interval=0.01)
            else:
                self.wfile.write(body)
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.do_GET()


@contextmanager
def _server(*, tls=False):
    server = _LoopbackServer()
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(_TLS_FIXTURE)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server
    finally:
        server.stopping.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


class HTTPDeadlineTests(unittest.TestCase):
    def _client(self, server, mode="", *, timeout=2, tls=False):
        scheme = "https" if tls else "http"
        client = LlamaServerClient(
            ConnectionConfig(
                f"{scheme}://127.0.0.1:{server.server_port}/{mode}",
                connect_timeout=2,
                read_timeout=2,
                default_deadline=timeout,
                tls=TLSConfig(verify=str(_TLS_FIXTURE)) if tls else TLSConfig(),
            )
        )
        # Avoid a developer's HTTP proxy changing a disposable loopback test.
        client._session.trust_env = False
        return client

    def test_trickled_status_headers_body_and_chunk_framing_obey_absolute_deadline(self):
        for mode in ("status", "headers", "body", "chunked", "gzip_slow"):
            with self.subTest(mode=mode), _server() as server, self._client(server, mode) as client:
                responses = []
                client._session.hooks["response"].append(
                    lambda response, target=responses, **_: target.append(response)
                )
                started = time.monotonic()
                with self.assertRaises(DeadlineExceeded) as caught:
                    client.health(timeout=0.1)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.35)
                self.assertEqual(caught.exception.endpoint, "/health")
                self.assertTrue(server.connection_closed.wait(0.3), "timed-out socket stayed open")
                for response in responses:
                    self.assertTrue(response.raw.closed)

    def test_trickled_error_body_stops_at_deadline_and_preserves_http_error(self):
        with _server() as server, self._client(server, "error_slow") as client:
            started = time.monotonic()
            with self.assertRaises(LlamaClientError) as caught:
                client.props(timeout=0.1)
            self.assertLess(time.monotonic() - started, 0.35)
            self.assertEqual(caught.exception.status_code, 500)
            self.assertEqual(caught.exception.endpoint, "/props")
            self.assertIsNone(caught.exception.body)
            self.assertNotIn("private-error-sentinel", str(caught.exception))
            self.assertTrue(server.connection_closed.wait(0.3))

    def test_happy_utf8_and_error_responses(self):
        with _server() as server, self._client(server, "utf8") as client:
            self.assertEqual(client.props().build_info, "café 日本")
        with _server() as server, self._client(server, "error") as client:
            with self.assertRaises(LlamaClientError) as caught:
                client.props()
            self.assertEqual(caught.exception.status_code, 500)
            self.assertNotIn("private-error-sentinel", str(caught.exception))

    def test_gzip_decoded_body_limit_and_invalid_utf8_are_preserved(self):
        with (
            _server() as server,
            self._client(server, "gzip") as client,
            mock.patch.object(client_module, "JSON_RESPONSE_MAX_BYTES", 1024),
            self.assertRaises(ResponseBodyLimitError),
        ):
            client.props()
        with (
            _server() as server,
            self._client(server, "invalid_utf8") as client,
            self.assertRaises(ResponseProtocolError),
        ):
            client.props()

    def test_keep_alive_reuse_does_not_retain_the_previous_deadline(self):
        with _server() as server, self._client(server) as client:
            self.assertTrue(client.health(timeout=0.05).ok)
            time.sleep(0.06)
            self.assertTrue(client.health(timeout=0.5).ok)
            self.assertEqual(server.accepted, 1)

    def test_concurrent_request_survives_another_request_deadline(self):
        with _server() as server, self._client(server, "concurrent") as client:
            errors = []

            def slow_request():
                try:
                    client.health(timeout=0.1)
                except Exception as exc:
                    errors.append(exc)

            slow = threading.Thread(target=slow_request)
            slow.start()
            try:
                self.assertTrue(server.slow_started.wait(1))
                self.assertEqual(client.props(timeout=1).build_info, "parallel")
            finally:
                slow.join(timeout=1)
            self.assertFalse(slow.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], DeadlineExceeded)
            self.assertEqual(server.accepted, 2)

    def test_repeated_timeouts_leave_no_threads_or_open_responses(self):
        before = set(threading.enumerate())
        with _server() as server, self._client(server, "body") as client:
            responses = []
            client._session.hooks["response"].append(
                lambda response, **_: responses.append(response)
            )
            for _ in range(3):
                with self.assertRaises(DeadlineExceeded):
                    client.health(timeout=0.1)
            self.assertEqual(len(responses), 3)
            self.assertTrue(all(response.raw.closed for response in responses))
        self.assertEqual(set(threading.enumerate()) - before, set())

    def test_no_deadline_keeps_normal_transport_behavior(self):
        with _server() as server, self._client(server, timeout=None) as client:
            self.assertTrue(client.health().ok)

    def test_owned_session_chat_and_model_streams_retain_their_transport(self):
        with _server() as server, self._client(server, "stream") as client:
            self.assertTrue(client.health(timeout=0.05).ok)
            time.sleep(0.06)
            result = client.stream_chat({"messages": []}, timeout=1)
            self.assertTrue(result.success)
            self.assertEqual(result.response, "hello")
            events = list(client.iter_model_events(timeout=1))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].model, "fixture")

    def test_tls_verification_and_trickled_response_deadlines(self):
        # This PEM contains only a disposable, public loopback-fixture identity.
        for mode in ("utf8", "headers", "body"):
            with (
                self.subTest(mode=mode),
                _server(tls=True) as server,
                self._client(server, mode, tls=True) as client,
            ):
                if mode == "utf8":
                    self.assertEqual(client.props().build_info, "café 日本")
                else:
                    started = time.monotonic()
                    with self.assertRaises(DeadlineExceeded):
                        client.props(timeout=0.1)
                    self.assertLess(time.monotonic() - started, 0.35)
                    self.assertTrue(server.connection_closed.wait(0.3))

    def test_proxy_preserves_requests_routing_and_absolute_deadline(self):
        for mode in ("utf8", "headers", "body"):
            with self.subTest(mode=mode), _server() as server:
                with LlamaServerClient(
                    ConnectionConfig(f"http://fixture.invalid/{mode}")
                ) as client:
                    client._session.trust_env = False
                    client._session.proxies["http"] = f"http://127.0.0.1:{server.server_port}"
                    if mode == "utf8":
                        self.assertEqual(client.props().build_info, "café 日本")
                    else:
                        started = time.monotonic()
                        with self.assertRaises(DeadlineExceeded):
                            client.props(timeout=0.1)
                        self.assertLess(time.monotonic() - started, 0.35)
                        self.assertTrue(server.connection_closed.wait(0.3))
                    self.assertEqual(server.paths, [f"http://fixture.invalid/{mode}/props"])

    def test_injected_session_adapters_and_ownership_are_unchanged(self):
        with _server() as server, requests.Session() as session:
            adapters = dict(session.adapters)
            session.trust_env = False
            client = LlamaServerClient(
                ConnectionConfig(f"http://127.0.0.1:{server.server_port}"), session=session
            )
            self.assertTrue(client.health().ok)
            client.close()
            self.assertEqual(session.adapters, adapters)
            self.assertTrue(client.health().ok)
