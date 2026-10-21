"""Real response cancellation, independent of upstream DELETE support."""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import pytest

import runtime.http_deadline as transport
from runtime.client import ConnectionConfig, LlamaServerClient, StreamControl
from runtime.streaming import iter_model_events, stream_chat


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.do_GET()

    def do_GET(self):
        if self.path.endswith("/health"):
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        mode = self.path.split("/")[1]
        try:
            if mode == "headers":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Pending: ")
            else:
                self.send_response(503 if mode == "error_body" else 200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                if mode in {"partial", "complete"}:
                    event = (
                        {"model": "fixture", "event": "loaded"}
                        if self.path.endswith("/models/sse")
                        else {"choices": [{"delta": {"content": "hello"}}]}
                    )
                    body = b"data: " + json.dumps(event).encode() + b"\n\n"
                    if mode == "complete" and not self.path.endswith("/models/sse"):
                        body += b"data: [DONE]\n\n"
                    self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
                elif mode == "chunk":
                    # HTTP framing can block before an SSE line is available.
                    self.wfile.write(b"f;extension=unfinished")
                if mode == "complete":
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    return
            self.wfile.flush()
            self.server.waiting.set()
            self.server.stopping.wait(3)
        except (ConnectionError, OSError):
            pass
        self.close_connection = True


class _Server(ThreadingHTTPServer):
    daemon_threads = False

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(1)
        return sock, address


@contextmanager
def _fixture(mode):
    before = set(threading.enumerate())
    server = _Server(("127.0.0.1", 0), _Handler)
    server.waiting = threading.Event()
    server.stopping = threading.Event()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    client = LlamaServerClient(
        ConnectionConfig(f"http://127.0.0.1:{server.server_port}/{mode}", read_timeout=2)
    )
    client._session.trust_env = False
    readers = []
    initialize = transport._DeadlineReader.__init__

    def capture(reader, *args, **kwargs):
        # Attachment can cancel and close the raw reader before __init__ returns.
        # Retain that reader too so cleanup evidence survives interrupted setup.
        readers.append(reader)
        initialize(reader, *args, **kwargs)

    try:
        with (
            mock.patch.object(transport._DeadlineReader, "__init__", capture),
            mock.patch("requests.sessions.get_environ_proxies", return_value={}),
        ):
            yield server, client, readers
    finally:
        client.close()
        server.stopping.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert set(threading.enumerate()) == before
    assert readers and all(reader.closed for reader in readers)


@pytest.mark.parametrize("mode", ["headers", "body", "chunk", "error_body"])
@pytest.mark.parametrize("operation", ["chat", "models"])
@pytest.mark.parametrize("standalone", [False, True])
def test_cancel_stalled_headers_body_and_http_framing(mode, operation, standalone):
    with _fixture(mode) as (server, client, _):
        cancel = threading.Event()

        def trigger():
            assert server.waiting.wait(2)
            cancel.set()

        worker = threading.Thread(target=trigger)
        worker.start()
        started = time.monotonic()
        if operation == "chat":
            result = (
                stream_chat(
                    client.connection, {}, timeout=2, chunk_timeout=1.5, cancel=cancel.is_set
                )
                if standalone
                else client.stream_chat({}, timeout=2, chunk_timeout=1.5, cancel=cancel.is_set)
            )
            assert result.cancelled and result.error_type == "cancelled"
            assert not result.success
        else:
            events = (
                iter_model_events(client.connection, timeout=2, cancel=cancel.is_set)
                if standalone
                else client.iter_model_events(timeout=2, cancel=cancel.is_set)
            )
            assert list(events) == []
        elapsed = time.monotonic() - started
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert elapsed < 0.75, elapsed
        assert transport._ACTIVE_DEADLINE.get() is None
        assert transport._ACTIVE_CANCELLATION.get() is None


def test_cancel_preserves_partial_text_and_exact_delete_cleanup():
    with _fixture("partial") as (_, client, _):
        cancel = threading.Event()
        partial_seen = threading.Event()
        control = StreamControl(client.connection, uuid.uuid4())

        def trigger():
            assert partial_seen.wait(2)
            cancel.wait(0.05)
            cancel.set()

        worker = threading.Thread(target=trigger)
        worker.start()
        started = time.monotonic()
        with mock.patch.object(StreamControl, "delete", return_value=True) as delete:
            result = client.stream_chat(
                {},
                timeout=2,
                chunk_timeout=1.5,
                cancel=cancel.is_set,
                on_update=lambda _: partial_seen.set(),
                stream_control=control,
            )
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert time.monotonic() - started < 0.75
        assert result.cancelled and result.partial
        assert result.response == "hello"
        delete.assert_called_once_with()


def test_model_generator_context_is_reset_across_yields_and_close_joins_monitor():
    with _fixture("partial") as (_, client, _):
        cancel = threading.Event()
        events = client.iter_model_events(timeout=2, cancel=cancel.is_set)
        assert next(events).model == "fixture"
        assert transport._ACTIVE_DEADLINE.get() is None
        assert transport._ACTIVE_CANCELLATION.get() is None
        # A sibling request in this thread must not borrow the stream's monitor.
        assert client.health(timeout=1).ok
        cancel.set()
        assert list(events) == []
        assert client.health(timeout=1).ok


def test_abandoned_model_generator_closes_response_and_monitor():
    with _fixture("partial") as (_, client, _):
        events = client.iter_model_events(timeout=2, cancel=lambda: False)
        assert next(events).model == "fixture"
        events.close()
        assert client.health(timeout=1).ok


def test_completed_stream_monitor_cannot_cancel_later_keepalive_request():
    with _fixture("complete") as (_, client, _):
        cancel = threading.Event()
        assert client.stream_chat({}, timeout=1, cancel=cancel.is_set).success
        cancel.set()
        assert client.health(timeout=1).ok


@pytest.mark.parametrize("mode", ["body", "error_body"])
def test_cancellation_callback_base_exception_closes_response_and_deletes_stream(mode):
    class Interrupt(BaseException):
        pass

    with _fixture(mode) as (server, client, _):
        interrupt = threading.Event()

        def check():
            if interrupt.is_set():
                raise Interrupt()
            return False

        def trigger():
            assert server.waiting.wait(2)
            interrupt.set()

        worker = threading.Thread(target=trigger)
        worker.start()
        control = StreamControl(client.connection, uuid.uuid4())
        with mock.patch.object(StreamControl, "delete", return_value=True) as delete:
            with pytest.raises(Interrupt):
                client.stream_chat({}, timeout=2, cancel=check, stream_control=control)
        worker.join(timeout=2)
        assert not worker.is_alive()
        delete.assert_called_once_with()


@pytest.mark.parametrize("operation", ["chat", "models"])
@pytest.mark.parametrize("interrupt_callback", [False, True])
def test_attach_time_cancellation_retains_closed_reader_evidence(operation, interrupt_callback):
    class Interrupt(BaseException):
        pass

    with _fixture("body") as (_, client, readers):
        cancel = threading.Event()

        def check():
            if interrupt_callback and cancel.is_set():
                raise Interrupt()
            return cancel.is_set()

        attach = transport._ResponseCancellation.attach

        def cancel_on_attach(monitor, sock):
            # Force cancellation after raw-reader creation but before attachment
            # completes, independently of HTTP/server thread scheduling.
            cancel.set()
            return attach(monitor, sock)

        def execute():
            if operation == "chat":
                result = client.stream_chat({}, timeout=2, cancel=check)
                assert result.cancelled and result.error_type == "cancelled"
            else:
                assert list(client.iter_model_events(timeout=2, cancel=check)) == []

        with mock.patch.object(transport._ResponseCancellation, "attach", cancel_on_attach):
            if interrupt_callback:
                with pytest.raises(Interrupt):
                    execute()
            else:
                execute()
        assert len(readers) == 1
        assert readers[0].closed
        assert readers[0]._raw.closed
