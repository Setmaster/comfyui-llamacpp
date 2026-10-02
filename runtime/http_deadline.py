"""Absolute response deadlines for the client-owned requests transport.

Socket inactivity timeouts alone permit a peer to trickle headers or a body
forever. Check the remaining budget before every raw read, including the reads
hidden inside buffered HTTP parsing. No timer or worker can outlive a request.
DNS resolution and connection establishment retain requests' native behavior;
in particular, its connect timeout does not bound the system DNS resolver.
"""

from __future__ import annotations

import io
from contextlib import contextmanager
from contextvars import ContextVar
from http.client import HTTPResponse
from typing import TYPE_CHECKING, Any

from requests.adapters import HTTPAdapter

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .client import Deadline


_ACTIVE_DEADLINE: ContextVar[Deadline | None] = ContextVar("llama_http_deadline", default=None)


@contextmanager
def response_deadline(deadline: Deadline) -> Iterator[None]:
    token = _ACTIVE_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _ACTIVE_DEADLINE.reset(token)


class _DeadlineReader(io.RawIOBase):
    def __init__(self, sock: Any, deadline: Deadline) -> None:
        super().__init__()
        self._sock = sock
        self._deadline = deadline
        self._raw = sock.makefile("rb", buffering=0)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int | None:
        self._deadline.raise_if_expired("HTTP response")
        remaining = self._deadline.remaining
        previous_timeout = self._sock.gettimeout()
        timeout = previous_timeout
        if remaining is not None:
            timeout = remaining if timeout is None else min(timeout, remaining)
        self._sock.settimeout(timeout)
        try:
            result = self._raw.readinto(buffer)
            self._deadline.raise_if_expired("HTTP response")
            return result
        finally:
            # A keep-alive socket may serve a later request with another budget.
            self._sock.settimeout(previous_timeout)

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


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
        if deadline is not None and deadline.remaining is not None:
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
        super().init_poolmanager(*args, **kwargs)
        _configure_pools(self.poolmanager)

    def proxy_manager_for(self, proxy: str, **kwargs: Any) -> Any:
        existing = proxy in self.proxy_manager
        manager = super().proxy_manager_for(proxy, **kwargs)
        if not existing:
            _configure_pools(manager)
        return manager
