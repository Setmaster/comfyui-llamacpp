"""Absolute response deadlines for the client-owned requests transport.

Socket inactivity timeouts alone permit a peer to trickle headers or a body
forever. Check the remaining budget before every raw read, including the reads
hidden inside buffered HTTP parsing. Optional cancellation monitors shut down
only the active response socket and are joined before the request returns.
DNS resolution and connection establishment retain requests' native behavior;
in particular, its connect timeout does not bound the system DNS resolver.
"""

from __future__ import annotations

import io
import socket
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from http.client import HTTPResponse
from typing import TYPE_CHECKING, Any

from requests.adapters import HTTPAdapter
from requests.exceptions import Timeout

try:
    from urllib3.util.ssltransport import SSLTransport
except ImportError:  # Older urllib3 has no TLS-in-TLS proxy transport.
    SSLTransport = None

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from .client import Deadline


_ACTIVE_DEADLINE: ContextVar[Deadline | None] = ContextVar("llama_http_deadline", default=None)
_ACTIVE_CANCELLATION: ContextVar[_ResponseCancellation | None] = ContextVar(
    "llama_http_cancellation", default=None
)


class _ResponseCancellation:
    """A nonblocking predicate and one borrowed socket, never a shared session.

    Detach occurs before the response releases its connection to the pool. The
    same lock guards detach and shutdown, preventing a completed request's
    monitor from touching a later user of a keep-alive connection.
    """

    def __init__(self, cancel: Callable[[], bool]) -> None:
        self._cancel = cancel
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()
        self._socket: Any = None
        self._failure: BaseException | None = None
        self._thread: threading.Thread | None = None

    def _poll(self) -> None:
        with self._poll_lock:
            if self._failure is None:
                try:
                    if self._cancel():
                        from .client import OperationCancelled

                        self._failure = OperationCancelled("HTTP response cancelled")
                except BaseException as exc:
                    self._failure = exc

    def check(self) -> None:
        self._poll()
        if self._failure is not None:
            raise self._failure

    def attach(self, sock: Any) -> None:
        self.check()
        with self._lock:
            self._socket = sock
            if self._thread is None:
                self._thread = threading.Thread(target=self._watch, name="llama-http-cancel")
                self._thread.start()

    def detach(self, sock: Any) -> None:
        with self._lock:
            if self._socket is sock:
                self._socket = None

    def _watch(self) -> None:
        while not self._stop.wait(0.025):
            self._poll()
            if self._failure is not None:
                with self._lock:
                    sock = self._socket
                    # TLS-in-TLS wraps a real outer socket. Shutdown must wake
                    # the blocked read without attempting TLS close traffic.
                    while SSLTransport is not None and isinstance(sock, SSLTransport):
                        sock = sock.socket
                    if sock is not None:
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        finally:
                            # Older urllib3 may accept a short body as EOF and
                            # pool the connection before the caller checks the
                            # cancellation flag. Close this exact socket too;
                            # makefile references defer final descriptor close
                            # until the response reader unwinds.
                            sock.close()
                return

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            self._socket = None


@contextmanager
def response_deadline(
    deadline: Deadline, cancel: Callable[[], bool] | None = None
) -> Iterator[_ResponseCancellation | None]:
    token = _ACTIVE_DEADLINE.set(deadline)
    cancellation = _ResponseCancellation(cancel) if cancel is not None else None
    cancellation_token = _ACTIVE_CANCELLATION.set(cancellation)
    try:
        if cancellation is not None:
            cancellation.check()
        yield cancellation
    finally:
        if cancellation is not None:
            cancellation.close()
        _ACTIVE_CANCELLATION.reset(cancellation_token)
        _ACTIVE_DEADLINE.reset(token)


class _DeadlineReader(io.RawIOBase):
    def __init__(self, sock: Any, deadline: Deadline) -> None:
        super().__init__()
        self._sock = sock
        self._deadline = deadline
        self._cancellation = _ACTIVE_CANCELLATION.get()
        self._raw = sock.makefile("rb", buffering=0)
        if self._cancellation is not None:
            try:
                self._cancellation.attach(sock)
            except BaseException:
                self._raw.close()
                raise

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int | None:
        underlying = None
        if SSLTransport is not None and isinstance(self._sock, SSLTransport):
            # TLS-in-TLS may recv many outer records for one inner TLS read.
            # Guard those calls too, only while this connection is reading.
            # Restore before closing the makefile or returning to the pool so
            # urllib3's socket refcounts and later request budgets stay intact.
            underlying = self._sock.socket
            self._sock.socket = _DeadlineTLSIO(underlying, self._deadline)
        try:
            return _deadline_io(self._sock, self._deadline, lambda: self._raw.readinto(buffer))
        finally:
            if underlying is not None:
                self._sock.socket = underlying

    def close(self) -> None:
        try:
            if self._cancellation is not None:
                self._cancellation.detach(self._sock)
            self._raw.close()
        finally:
            super().close()


def _deadline_io(sock: Any, deadline: Deadline, operation: Callable[[], Any]) -> Any:
    deadline.raise_if_expired("HTTP response")
    remaining = deadline.remaining
    previous_timeout = sock.gettimeout()
    timeout = previous_timeout
    if remaining is not None:
        timeout = remaining if timeout is None else min(timeout, remaining)
    sock.settimeout(timeout)
    try:
        result = operation()
        deadline.raise_if_expired("HTTP response")
        return result
    finally:
        # A keep-alive socket may serve a later request with another budget.
        sock.settimeout(previous_timeout)


class _DeadlineTLSIO:
    def __init__(self, sock: Any, deadline: Deadline) -> None:
        self._sock = sock
        self._deadline = deadline

    def recv(self, *args: Any, **kwargs: Any) -> Any:
        return _deadline_io(self._sock, self._deadline, lambda: self._sock.recv(*args, **kwargs))

    def sendall(self, *args: Any, **kwargs: Any) -> Any:
        return _deadline_io(self._sock, self._deadline, lambda: self._sock.sendall(*args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


class _DeadlineSocket:
    def __init__(self, sock: Any, deadline: Deadline) -> None:
        self._sock = sock
        self._deadline = deadline

    def makefile(self, mode: str) -> io.BufferedReader:
        assert mode == "rb"
        return io.BufferedReader(_DeadlineReader(self._sock, self._deadline))


class _DeadlineResponse(HTTPResponse):
    def __init__(self, sock: Any, *args: Any, **kwargs: Any) -> None:
        deadline = _ACTIVE_DEADLINE.get()
        if deadline is not None and (
            deadline.remaining is not None or _ACTIVE_CANCELLATION.get() is not None
        ):
            sock = _DeadlineSocket(sock, deadline)
        super().__init__(sock, *args, **kwargs)


def _configure_pools(manager: Any) -> None:
    # Copy the manager's mapping: urllib3's default mapping is shared globally.
    # Subclass its existing connections to retain HTTPS and proxy behavior.
    manager.pool_classes_by_scheme = {
        scheme: type(
            f"Deadline{pool.__name__}",
            (pool,),
            {
                "ConnectionCls": type(
                    f"Deadline{pool.ConnectionCls.__name__}",
                    (pool.ConnectionCls,),
                    {"response_class": _DeadlineResponse},
                )
            },
        )
        for scheme, pool in manager.pool_classes_by_scheme.items()
    }


class DeadlineHTTPAdapter(HTTPAdapter):
    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        self._proxy_lock = threading.Lock()
        super().init_poolmanager(*args, **kwargs)
        _configure_pools(self.poolmanager)

    def proxy_manager_for(self, proxy: str, **kwargs: Any) -> Any:
        # requests publishes a new manager before we can configure it. Hold a
        # deadline-bounded lock through retrieval too, so another first-use
        # request cannot create an unguarded pool in that publication window.
        deadline = _ACTIVE_DEADLINE.get()
        remaining = deadline.remaining if deadline is not None else None
        acquired = (
            self._proxy_lock.acquire()
            if remaining is None
            else self._proxy_lock.acquire(timeout=min(remaining, threading.TIMEOUT_MAX))
        )
        if not acquired:
            raise Timeout("HTTP proxy transport deadline exceeded")
        try:
            if deadline is not None and deadline.expired:
                raise Timeout("HTTP proxy transport deadline exceeded")
            existing = proxy in self.proxy_manager
            manager = super().proxy_manager_for(proxy, **kwargs)
            if not existing:
                try:
                    _configure_pools(manager)
                except BaseException:
                    self.proxy_manager.pop(proxy, None)
                    manager.clear()
                    raise
            if deadline is not None and deadline.expired:
                raise Timeout("HTTP proxy transport deadline exceeded")
            return manager
        finally:
            self._proxy_lock.release()
