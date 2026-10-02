"""Inert real-socket proof for count cancellation, deadlines and sibling isolation."""

from __future__ import annotations

import json
import ssl
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

import runtime.http_deadline as deadline_module
from runtime.client import (
    ConnectionConfig,
    Deadline,
    DeadlineExceeded,
    LlamaServerClient,
    OperationCancelled,
    TLSConfig,
)


class Server(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.started = threading.Event()
        self.stopping = threading.Event()
        self.release = threading.Event()
        self.paths = []

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(2)
        return sock, address


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError, ssl.SSLError):
            pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.paths.append(self.path)
        mode = payload.get("test_mode", "fast")
        body = b'{"input_tokens":17}'
        headers = b"HTTP/1.1 200 OK\r\nContent-Length: 19\r\n\r\n"
        headers = headers.replace(b"19", str(len(body)).encode())
        try:
            if mode == "status":
                data = headers + body
            elif mode == "headers":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Trickle: ")
                data = b"x" * 100
            elif mode == "delayed_headers":
                self.server.started.set()
                self.server.release.wait(2)
                self.wfile.write(headers + body)
                return
            elif mode == "chunked":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                data = b"13;trickle=" + b"x" * 100
            elif mode == "survivor":
                self.wfile.write(headers)
                self.server.release.wait(2)
                self.wfile.write(body)
                return
            else:
                self.wfile.write(headers)
                if mode == "delayed_body":
                    self.server.started.set()
                    self.server.release.wait(2)
                    self.wfile.write(body)
                    return
                if mode == "fast":
                    self.wfile.write(body)
                    return
                data = body
            self.server.started.set()
            for byte in data:
                self.wfile.write(bytes([byte]))
                if self.server.stopping.wait(0.04):
                    return
        except (ConnectionError, TimeoutError, ssl.SSLError):
            self.close_connection = True


@contextmanager
def loopback(*, tls=False):
    server = Server()
    certificate = Path(__file__).parent / "fixtures/http_deadline_tls.pem"
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    client = LlamaServerClient(
        ConnectionConfig(
            f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}",
            default_deadline=2,
            read_timeout=2,
            tls=TLSConfig(verify=str(certificate)) if tls else TLSConfig(),
        )
    )
    client._session.trust_env = False
    readers = []
    initialize = deadline_module._DeadlineReader.__init__

    def capture(reader, *args, **kwargs):
        initialize(reader, *args, **kwargs)
        readers.append(reader)

    try:
        with mock.patch.object(deadline_module._DeadlineReader, "__init__", capture):
            yield server, client, readers
    finally:
        server.stopping.set()
        server.release.set()
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


def run_count(client, mode, result, **kwargs):
    try:
        result.append(client.count_chat_input_tokens({"test_mode": mode, "messages": []}, **kwargs))
    except BaseException as exc:
        result.append(exc)


def assert_closed(readers):
    assert readers
    for reader in readers:
        assert reader.closed
        assert reader._raw.closed
        assert reader._sock.fileno() == -1


@pytest.mark.parametrize(
    "mode", ["status", "headers", "body", "chunked", "delayed_headers", "delayed_body"]
)
@pytest.mark.parametrize("tls", [False, True])
def test_count_cancellation_interrupts_blocking_and_trickled_reads_with_no_monitor_leak(mode, tls):
    cancel = threading.Event()
    baseline = set(threading.enumerate())
    with loopback(tls=tls) as (server, client, readers):
        result = []
        worker = threading.Thread(
            target=run_count, args=(client, mode, result), kwargs={"cancel": cancel.is_set}
        )
        worker.start()
        assert server.started.wait(1)
        started = time.monotonic()
        cancel.set()
        worker.join(0.8)
        assert not worker.is_alive()
        assert time.monotonic() - started < 0.8
        assert len(result) == 1 and isinstance(result[0], OperationCancelled), result
        assert_closed(readers)
        assert not any(
            t.name == "llama-http-cancel" and t not in baseline for t in threading.enumerate()
        )
    assert set(threading.enumerate()) <= baseline


@pytest.mark.parametrize(
    "mode", ["status", "headers", "body", "chunked", "delayed_headers", "delayed_body"]
)
def test_count_deadline_closes_reader_and_cancellation_monitor(mode):
    baseline = set(threading.enumerate())
    with loopback() as (_server, client, readers):
        started = time.monotonic()
        with pytest.raises(DeadlineExceeded):
            client.count_chat_input_tokens({"test_mode": mode}, timeout=0.12, cancel=lambda: False)
        assert time.monotonic() - started < 0.7
        assert_closed(readers)
        assert not any(
            t.name == "llama-http-cancel" and t not in baseline for t in threading.enumerate()
        )
    assert set(threading.enumerate()) <= baseline


def test_cancelled_request_does_not_interrupt_sibling_or_later_keepalive_request():
    cancel = threading.Event()
    baseline = set(threading.enumerate())
    with loopback() as (server, client, readers):
        cancelled, survivor = [], []
        slow = threading.Thread(
            target=run_count, args=(client, "body", cancelled), kwargs={"cancel": cancel.is_set}
        )
        other = threading.Thread(target=run_count, args=(client, "survivor", survivor))
        slow.start()
        assert server.started.wait(1)
        other.start()
        cancel.set()
        slow.join(0.8)
        assert not slow.is_alive()
        assert isinstance(cancelled[0], OperationCancelled)
        server.release.set()
        other.join(1)
        assert not other.is_alive()
        assert survivor[0].input_tokens == 17
        assert client.count_chat_input_tokens({"messages": []}).input_tokens == 17
        assert len(server.paths) == 3
        assert all(path.endswith("?autoload=false") for path in server.paths)
    # The cancelled connection must be gone. Successful keep-alive sockets are
    # owned by urllib3's pool; retaining response readers here can retain that
    # pool through captured tracebacks even after Session.close().
    assert_closed(readers[:1])
    assert all(reader.closed and reader._raw.closed for reader in readers)
    assert set(threading.enumerate()) <= baseline


def test_completed_monitor_cannot_cancel_reused_connection():
    cancel = threading.Event()
    baseline = set(threading.enumerate())
    with loopback() as (server, client, _readers):
        assert client.count_chat_input_tokens({}, cancel=cancel.is_set).input_tokens == 17
        cancel.set()
        result = []
        worker = threading.Thread(target=run_count, args=(client, "survivor", result))
        worker.start()
        server.release.set()
        worker.join(1)
        assert not worker.is_alive()
        assert result[0].input_tokens == 17
    assert set(threading.enumerate()) <= baseline


def test_cancelled_short_body_closes_socket_even_when_transport_accepts_early_eof():
    # urllib3 1.26 accepts short Content-Length bodies. Reproduce that behavior
    # on current urllib3 too, and force cancellation during the blocked body
    # read rather than the checks immediately before or after response headers.
    cancel = threading.Event()
    headers_seen = threading.Event()
    body_read_started = threading.Event()
    baseline = set(threading.enumerate())
    readinto = deadline_module._DeadlineReader.readinto

    def lax_body(response, **_kwargs):
        response.raw.enforce_content_length = False
        headers_seen.set()

    def gated_read(reader, buffer):
        if headers_seen.is_set():
            body_read_started.set()
        return readinto(reader, buffer)

    with (
        loopback() as (server, client, readers),
        mock.patch.object(deadline_module._DeadlineReader, "readinto", gated_read),
    ):
        client._session.hooks["response"].append(lax_body)
        result = []
        worker = threading.Thread(
            target=run_count,
            args=(client, "delayed_body", result),
            kwargs={"cancel": cancel.is_set},
        )
        worker.start()
        assert server.started.wait(1)
        assert body_read_started.wait(1)
        cancel.set()
        worker.join(0.8)
        assert not worker.is_alive()
        assert len(result) == 1 and isinstance(result[0], OperationCancelled), result
        assert_closed(readers)
    assert set(threading.enumerate()) <= baseline


@pytest.mark.parametrize("raise_from_callback", [False, True])
def test_cancel_guard_works_without_absolute_deadline_and_propagates_callback_error(
    raise_from_callback,
):
    cancel = threading.Event()
    baseline = set(threading.enumerate())

    def check():
        if cancel.is_set() and raise_from_callback:
            raise RuntimeError("fixture callback interruption")
        return cancel.is_set()

    with loopback() as (server, client, readers):
        result = []
        worker = threading.Thread(
            target=run_count,
            args=(client, "delayed_body", result),
            kwargs={"cancel": check, "deadline": Deadline(None)},
        )
        worker.start()
        assert server.started.wait(1)
        cancel.set()
        worker.join(0.8)
        assert not worker.is_alive()
        expected = RuntimeError if raise_from_callback else OperationCancelled
        assert len(result) == 1 and isinstance(result[0], expected), result
        if raise_from_callback:
            assert str(result[0]) == "fixture callback interruption"
        assert_closed(readers)
    assert set(threading.enumerate()) <= baseline
