"""Owned ``llama-server`` process lifecycle primitives.

This module deliberately knows nothing about ComfyUI.  It owns only processes it
started itself and records enough identity information to avoid PID/name based
adoption or termination.
"""

from __future__ import annotations

import codecs
import collections
import errno
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import psutil

LOGGER = logging.getLogger(__name__)


class ProcessLifecycle(str, Enum):
    """Observable lifecycle states for an owned process tree."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    START_FAILED = "start_failed"
    RUNTIME_FAILED = "runtime_failed"
    INCOMPLETE_STOP = "incomplete_stop"


class ProcessAlreadyRunning(RuntimeError):
    """Raised when a second launch is attempted for an active controller."""


class _PosixPidfdInvalid(RuntimeError):
    """Raised when a retained pidfd descriptor itself is no longer usable."""


class _PosixAuthorityState(str, Enum):
    """Confidence that the saved PGID still belongs to this launch."""

    VALID = "valid"
    INVALID = "invalid"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class ProcessIdentity:
    """PID plus creation time, sufficient to reject ordinary PID reuse."""

    pid: int
    create_time: float


@dataclass(frozen=True)
class _PosixAuthorityProof:
    state: _PosixAuthorityState
    reason: str
    pgid: int | None = None


@dataclass(frozen=True)
class _PosixGroupInspection:
    state: _PosixAuthorityState
    members: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class StopResult:
    """Result of a terminate/wait/kill/wait sequence."""

    complete: bool
    escalated: bool
    returncode: int | None
    duration_seconds: float
    remaining_pids: tuple[int, ...] = ()
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "escalated": self.escalated,
            "returncode": self.returncode,
            "duration_seconds": self.duration_seconds,
            "remaining_pids": list(self.remaining_pids),
            "error": self.error,
        }


@dataclass(frozen=True)
class ProcessSnapshot:
    """Secret-free diagnostic snapshot."""

    state: ProcessLifecycle
    pid: int | None
    create_time: float | None
    process_group_id: int | None
    windows_job_assigned: bool
    descendant_fallback: bool
    command: tuple[str, ...]
    cwd: str | None
    started_at: float | None
    stopped_at: float | None
    returncode: int | None
    last_error: str | None
    log_tail: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "pid": self.pid,
            "create_time": self.create_time,
            "process_group_id": self.process_group_id,
            "windows_job_assigned": self.windows_job_assigned,
            "descendant_fallback": self.descendant_fallback,
            "command": list(self.command),
            "cwd": self.cwd,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "returncode": self.returncode,
            "last_error": self.last_error,
            "log_tail": list(self.log_tail),
        }


_SENSITIVE_OPTION_NAMES = frozenset(
    {
        "--api-key",
        "--apikey",
        "--auth",
        "--authorization",
        "--bearer-token",
        "--password",
        "--token",
    }
)


class SecretRedactor:
    """Redact configured values and common credential-shaped log fragments."""

    _authorization_pattern = re.compile(r"(?i)\b(authorization)(\s*[:=]\s*|\s+)([^\r\n]+)")
    _credential_pattern = re.compile(
        r"(?i)\b(authorization|api[-_ ]?key|bearer|password|token)"
        r"(\s*[:=]\s*|\s+)([^\s,;]+)"
    )

    def __init__(self, secret_values: Iterable[str] = ()) -> None:
        self._secrets = tuple(
            sorted(
                {str(value) for value in secret_values if value},
                key=len,
                reverse=True,
            )
        )
        self.max_secret_length = max((len(value) for value in self._secrets), default=0)

    def redact_literals(self, value: str) -> str:
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "<redacted>")
        return text

    def redact(self, value: str) -> str:
        text = self.redact_literals(value)
        text = self._authorization_pattern.sub(r"\1\2<redacted>", text)
        return self._credential_pattern.sub(r"\1\2<redacted>", text)

    def literal_safe_flush_boundary(self, text: str, proposed: int) -> int:
        """Move a raw flush boundary before any configured secret it would split."""

        boundary = max(0, min(int(proposed), len(text)))
        while boundary:
            adjusted = boundary
            for secret in self._secrets:
                start = text.find(secret)
                while start >= 0 and start < boundary:
                    if start + len(secret) > boundary:
                        adjusted = min(adjusted, start)
                    start = text.find(secret, start + 1)
            if adjusted == boundary:
                return boundary
            boundary = adjusted

        # A very large configured secret can begin at offset zero and cross a
        # positive proposed boundary.  Flush through the transitive closure of
        # complete overlapping secrets so the operation still makes progress
        # without ever splitting one.
        end = max((len(secret) for secret in self._secrets if text.startswith(secret)), default=0)
        while end:
            extended = end
            for secret in self._secrets:
                start = text.find(secret)
                while start >= 0 and start < end:
                    if start + len(secret) > end:
                        extended = max(extended, start + len(secret))
                    start = text.find(secret, start + 1)
            if extended == end:
                return min(end, len(text))
            end = extended
        return boundary

    def redact_argv(self, argv: Sequence[str]) -> tuple[str, ...]:
        redacted: list[str] = []
        hide_next = False
        for raw_arg in argv:
            arg = str(raw_arg)
            if hide_next:
                redacted.append("<redacted>")
                hide_next = False
                continue

            option, separator, _value = arg.partition("=")
            if option.lower() in _SENSITIVE_OPTION_NAMES:
                if separator:
                    redacted.append(f"{option}=<redacted>")
                else:
                    redacted.append(option)
                    hide_next = True
                continue
            redacted.append(self.redact(arg))
        return tuple(redacted)


class _StreamingCredentialRedactor:
    """Bounded credential parser whose state survives arbitrary chunk boundaries."""

    _key_pattern = re.compile(r"(?i)\b(authorization|api[-_ ]?key|bearer|password|token)")
    _NORMAL_BUFFER_LIMIT = 32

    def __init__(self) -> None:
        self._buffer = ""
        self._state = "normal"
        self._authorization = False

    def feed(self, value: str, *, final: bool = False) -> str:
        self._buffer += str(value)
        output: list[str] = []

        while self._buffer:
            if self._state == "normal":
                match = self._key_pattern.search(self._buffer)
                if match is None:
                    if final:
                        output.append(self._buffer)
                        self._buffer = ""
                    elif len(self._buffer) > self._NORMAL_BUFFER_LIMIT:
                        output.append(self._buffer[: -self._NORMAL_BUFFER_LIMIT])
                        self._buffer = self._buffer[-self._NORMAL_BUFFER_LIMIT :]
                    break
                if match.start() > 0:
                    output.append(self._buffer[: match.start()])
                    self._buffer = self._buffer[match.start() :]
                    continue

                key_end = match.end()
                if len(self._buffer) == key_end:
                    if final:
                        output.append(self._buffer)
                        self._buffer = ""
                    break
                separator = self._buffer[key_end]
                if not separator.isspace() and separator not in ":=":
                    output.append(self._buffer[0])
                    self._buffer = self._buffer[1:]
                    continue

                output.append(self._buffer[: key_end + 1])
                output.append("<redacted>")
                self._authorization = match.group(1).casefold() == "authorization"
                self._buffer = self._buffer[key_end + 1 :]
                self._state = "await_value"
                continue

            if self._state == "await_value":
                character = self._buffer[0]
                self._buffer = self._buffer[1:]
                if character.isspace() or character in ":=":
                    continue
                if character in ",;":
                    output.append(character)
                    self._state = "normal"
                    self._authorization = False
                    continue
                self._state = "authorization_value" if self._authorization else "value"
                continue

            if self._state == "value":
                character = self._buffer[0]
                self._buffer = self._buffer[1:]
                if character.isspace() or character in ",;":
                    output.append(character)
                    self._state = "normal"
                    self._authorization = False
                continue

            # Authorization schemes and their credentials can contain spaces.
            # Suppress the complete header value through its record delimiter.
            character = self._buffer[0]
            self._buffer = self._buffer[1:]
            if character in "\r\n":
                output.append(character)
                self._state = "normal"
                self._authorization = False

        if final and self._state != "normal":
            # A committed credential prefix already emitted one placeholder;
            # incomplete separator/value bytes remain intentionally suppressed.
            self._buffer = ""
            self._state = "normal"
            self._authorization = False
        return "".join(output)


class BoundedLogTail:
    """Thread-safe bounded process log storage."""

    def __init__(
        self,
        *,
        max_lines: int = 400,
        max_bytes: int = 256 * 1024,
        redactor: SecretRedactor | None = None,
    ) -> None:
        if max_lines < 1 or max_bytes < 1:
            raise ValueError("log bounds must be positive")
        self._max_lines = max_lines
        self._max_bytes = max_bytes
        self._redactor = redactor or SecretRedactor()
        self._entries: collections.deque[tuple[str, int]] = collections.deque()
        self._bytes = 0
        self._lock = threading.RLock()

    def append(self, channel: str, text: str) -> None:
        clean = self._redactor.redact(text).replace("\x00", "")
        if not clean:
            return
        prefix = f"[{channel}] "
        # A child can emit an arbitrarily large line.  Split it before storing so
        # the tail itself remains bounded even when there are no newlines.
        max_payload = max(1, min(16 * 1024, self._max_bytes - len(prefix)))
        parts = [clean[i : i + max_payload] for i in range(0, len(clean), max_payload)]
        with self._lock:
            for part in parts:
                entry = prefix + part.rstrip("\r\n")
                size = len(entry.encode("utf-8", errors="replace"))
                self._entries.append((entry, size))
                self._bytes += size
                while len(self._entries) > self._max_lines or self._bytes > self._max_bytes:
                    _, removed = self._entries.popleft()
                    self._bytes -= removed

    def snapshot(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(entry for entry, _ in self._entries)


class _WindowsJob:
    """Small checked wrapper around a fresh Windows Job Object."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, kernel32: Any, handle: Any) -> None:
        self._kernel32 = kernel32
        self._handle = handle
        self._closed = False

    @classmethod
    def create(cls) -> _WindowsJob:
        if os.name != "nt":
            raise OSError("Windows Job Objects are only available on Windows")

        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = cls._KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle,
            cls._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            set_error = ctypes.WinError(ctypes.get_last_error())
            if not kernel32.CloseHandle(handle):
                close_error = ctypes.WinError(ctypes.get_last_error())
                raise close_error from set_error
            raise set_error
        return cls(kernel32, handle)

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        import ctypes
        from ctypes import wintypes

        process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self, exit_code: int = 1) -> None:
        import ctypes

        if self._closed:
            return
        if not self._kernel32.TerminateJobObject(self._handle, exit_code):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        import ctypes

        if self._closed:
            return
        if not self._kernel32.CloseHandle(self._handle):
            raise ctypes.WinError(ctypes.get_last_error())
        self._closed = True


class OwnedProcessController:
    """Start and deterministically stop one positively owned process tree."""

    def __init__(
        self,
        *,
        max_log_lines: int = 400,
        max_log_bytes: int = 256 * 1024,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        psutil_module: Any = psutil,
        windows_job_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._max_log_lines = max_log_lines
        self._max_log_bytes = max_log_bytes
        self._popen_factory = popen_factory
        self._psutil = psutil_module
        self._windows_job_factory = windows_job_factory or _WindowsJob.create

        self._lock = threading.RLock()
        # A POSIX group ID is safe signal authority only while its original
        # leader remains our unreaped child.  Monitor and stop cleanup share
        # this lock so the group authority is retired before either path reaps.
        self._posix_cleanup_lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._identity: ProcessIdentity | None = None
        self._process_group_id: int | None = None
        self._posix_pidfd: int | None = None
        self._windows_job: Any | None = None
        self._windows_job_assigned = False
        self._descendant_fallback = False
        self._state = ProcessLifecycle.STOPPED
        self._stopping = False
        self._stop_requested = False
        self._command: tuple[str, ...] = ()
        self._cwd: str | None = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._returncode: int | None = None
        self._posix_root_reaped = False
        self._posix_authority_valid = False
        self._posix_cleanup_uncertain = False
        self._last_error: str | None = None
        self._log_tail = BoundedLogTail(
            max_lines=max_log_lines,
            max_bytes=max_log_bytes,
        )
        self._drain_threads: list[threading.Thread] = []
        self._known_descendants: dict[int, ProcessIdentity] = {}
        self._descendant_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            process = self._process
            identity = self._identity
            if process is None or identity is None:
                return False
            if os.name == "nt":
                return process.poll() is None
            # Popen.poll() calls waitpid(WNOHANG) and can reap an exited POSIX
            # group leader.  The unreaped leader is what prevents PGID reuse
            # while descendant cleanup is still in flight.
            return self._identity_alive(identity)

    @property
    def has_owned_process(self) -> bool:
        with self._lock:
            # A post-spawn identity-capture failure can retain the exact Popen
            # and provisional session authority as visible incomplete ownership.
            return (
                self._process is not None
                or self._identity is not None
                or self._posix_pidfd is not None
            )

    @property
    def state(self) -> ProcessLifecycle:
        with self._lock:
            return self._state

    def start(
        self,
        argv: Sequence[str | os.PathLike[str]],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        secret_values: Iterable[str] = (),
        creationflags: int = 0,
    ) -> ProcessSnapshot:
        """Launch a new owned process tree without a shell."""

        command = tuple(os.fspath(arg) for arg in argv)
        if not command:
            raise ValueError("argv must not be empty")

        redactor = SecretRedactor(secret_values)
        with self._lock:
            if (
                self._process is not None
                or self._identity is not None
                or self._posix_pidfd is not None
            ):
                raise ProcessAlreadyRunning(
                    "the previous owned process tree must be released before a new launch"
                )

            self._reset_for_launch(redactor)
            self._state = ProcessLifecycle.STARTING
            self._command = redactor.redact_argv(command)
            self._cwd = str(Path(cwd).resolve()) if cwd is not None else None
            self._started_at = time.time()

            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "bufsize": 0,
                "shell": False,
            }
            if cwd is not None:
                kwargs["cwd"] = os.fspath(cwd)
            if env is not None:
                kwargs["env"] = dict(env)
            if os.name == "nt":
                kwargs["creationflags"] = (
                    creationflags
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            else:
                kwargs["start_new_session"] = True

            try:
                if sys.platform.startswith("linux"):
                    self._require_linux_pidfd_support()
                launch_command = self._platform_launch_command(command)
                process = self._popen_factory(list(launch_command), **kwargs)
                # Keep a handle before identity capture so every post-spawn failure
                # can still terminate and reap the exact process we just created.
                self._process = process
                if os.name != "nt":
                    # Open the stable generation token before any diagnostic
                    # lookup can fail.  A pidfd acquired later could bind a
                    # different same-parent child after numeric PID reuse.
                    self._posix_pidfd = self._open_linux_pidfd(process.pid)

                if os.name == "nt":
                    self._setup_windows_ownership(process)
                    identity = self._capture_identity(process.pid)
                    self._identity = identity
                else:
                    # A successful Popen with ``start_new_session=True`` means
                    # the child called setsid before exec, making its PID the
                    # new SID and PGID.  Establish that exact launch contract as
                    # provisional cleanup authority before diagnostic queries,
                    # so a transient getpgid/getsid failure cannot leak the root.
                    self._process_group_id = process.pid
                    self._posix_authority_valid = True
                    identity = self._capture_identity(process.pid)
                    self._identity = identity
                    pgid = os.getpgid(process.pid)
                    sid = os.getsid(process.pid)
                    if pgid != process.pid or sid != process.pid or pgid == os.getpgrp():
                        self._process_group_id = None
                        self._posix_authority_valid = False
                        self._posix_cleanup_uncertain = True
                        signal_error: Exception | None = None
                        try:
                            if sys.platform.startswith("linux"):
                                pidfd = self._posix_pidfd
                                if pidfd is None:
                                    raise RuntimeError(
                                        "launch-time pidfd is unavailable for exact cleanup"
                                    )
                                self._signal_linux_pidfd_process(pidfd, signal.SIGKILL)
                            else:
                                process.kill()
                        except ProcessLookupError:
                            # The exact pidfd generation has already exited.
                            # Stable consumption below distinguishes ECHILD
                            # without ever targeting a recycled numeric PID.
                            pass
                        except Exception as exc:
                            signal_error = exc
                        self._returncode = self._consume_posix_exit(process, identity, 5.0)
                        if signal_error is not None:
                            raise RuntimeError(
                                "failed to signal the exact mismatched launch generation"
                            ) from signal_error
                        raise RuntimeError("failed to establish a unique owned process group")
                    self._process_group_id = pgid
                    self._posix_authority_valid = True

                self._state = ProcessLifecycle.RUNNING
                self._start_log_drains(process, redactor)
                if self._descendant_fallback:
                    self._start_descendant_tracker(process, identity)
                self._start_monitor(process, identity)
            except Exception as exc:
                error = redactor.redact(f"{type(exc).__name__}: {exc}")
                cleanup_complete, cleanup_errors = self._cleanup_failed_launch()
                if cleanup_errors:
                    error += "; cleanup: " + "; ".join(cleanup_errors)
                self._last_error = error
                self._state = (
                    ProcessLifecycle.START_FAILED
                    if cleanup_complete
                    else ProcessLifecycle.INCOMPLETE_STOP
                )
                raise

            return self.snapshot()

    def stop(
        self,
        *,
        grace_timeout: float = 5.0,
        kill_timeout: float = 3.0,
    ) -> StopResult:
        """Terminate, wait, kill if needed, then wait for the owned tree."""

        if grace_timeout < 0 or kill_timeout < 0:
            raise ValueError("timeouts must not be negative")
        started = time.monotonic()
        provisional_process: subprocess.Popen[bytes] | None = None

        with self._lock:
            process = self._process
            identity = self._identity
            if process is None and identity is None:
                if self._posix_pidfd is not None:
                    self._state = ProcessLifecycle.INCOMPLETE_STOP
                    self._last_error = (
                        "stable POSIX pidfd authority remains without its process record; "
                        "refusing silent release"
                    )
                    return StopResult(
                        False,
                        False,
                        self._returncode,
                        time.monotonic() - started,
                        error=self._last_error,
                    )
                error: str | None = None
                try:
                    self._close_windows_job()
                except Exception as exc:
                    error = f"CloseHandle failed ({type(exc).__name__}: {exc})"
                complete = error is None
                self._state = (
                    ProcessLifecycle.STOPPED if complete else ProcessLifecycle.INCOMPLETE_STOP
                )
                self._last_error = error
                return StopResult(
                    complete,
                    False,
                    self._returncode,
                    time.monotonic() - started,
                    error=error,
                )
            if process is not None and identity is None and os.name != "nt":
                self._stopping = True
                self._stop_requested = True
                self._state = ProcessLifecycle.STOPPING
                provisional_process = process
            elif process is None or identity is None:
                self._state = ProcessLifecycle.INCOMPLETE_STOP
                self._last_error = "owned process identity is incomplete; refusing broad cleanup"
                return StopResult(
                    False,
                    False,
                    (
                        process.poll()
                        if os.name == "nt" and process is not None
                        else self._returncode
                    ),
                    time.monotonic() - started,
                    remaining_pids=(process.pid,) if process is not None else (),
                    error=self._last_error,
                )
            else:
                self._stopping = True
                self._stop_requested = True
                self._state = ProcessLifecycle.STOPPING

        if provisional_process is not None:
            return self._retry_provisional_posix_stop(
                provisional_process,
                started=started,
                timeout=max(grace_timeout + kill_timeout, kill_timeout),
            )

        errors: list[str] = []
        escalated = False
        remaining: tuple[int, ...] = ()
        try:
            if os.name == "nt":
                escalated, remaining, platform_errors = self._stop_windows_tree(
                    process,
                    identity,
                    grace_timeout,
                    kill_timeout,
                )
            else:
                escalated, remaining, platform_errors = self._stop_posix_group(
                    process,
                    identity,
                    grace_timeout,
                    kill_timeout,
                )
            errors.extend(platform_errors)
        except Exception as exc:  # defensive: lifecycle cleanup must report, not hide
            errors.append(f"{type(exc).__name__}: {exc}")
            try:
                remaining = self._remaining_owned_pids(identity)
            except Exception as remaining_exc:
                remaining = (identity.pid,)
                errors.append(
                    "remaining-process inspection failed "
                    f"({type(remaining_exc).__name__}: {remaining_exc})"
                )

        if os.name == "nt" and not remaining and process.poll() is not None:
            try:
                self._close_windows_job()
            except Exception as exc:
                errors.append(f"CloseHandle failed ({type(exc).__name__}: {exc})")

        unclosed_job = (
            os.name == "nt" and self._windows_job_assigned and self._windows_job is not None
        )
        returncode = process.poll() if os.name == "nt" else self._returncode
        root_cleanup_complete = (
            returncode is not None if os.name == "nt" else self._posix_root_reaped
        )
        authority_retired = os.name == "nt" or self._process_group_id is None
        complete = (
            not remaining
            and root_cleanup_complete
            and authority_retired
            and not self._posix_cleanup_uncertain
            and not unclosed_job
        )
        duration = time.monotonic() - started
        result = StopResult(
            complete=complete,
            escalated=escalated,
            returncode=returncode,
            duration_seconds=duration,
            remaining_pids=remaining,
            error="; ".join(errors) or None,
        )

        with self._lock:
            self._returncode = returncode
            self._stopped_at = time.time() if complete else None
            self._stopping = False
            if complete:
                self._state = ProcessLifecycle.STOPPED
                self._close_posix_pidfd()
                self._process = None
                self._identity = None
                self._process_group_id = None
                self._known_descendants.clear()
                self._descendant_fallback = False
                self._last_error = result.error
            else:
                self._state = ProcessLifecycle.INCOMPLETE_STOP
                self._last_error = result.error or (
                    f"owned process tree still alive: {', '.join(map(str, remaining))}"
                )
        return result

    def snapshot(self) -> ProcessSnapshot:
        with self._lock:
            identity = self._identity
            process = self._process
            return ProcessSnapshot(
                state=self._state,
                pid=identity.pid if identity else None,
                create_time=identity.create_time if identity else None,
                process_group_id=self._process_group_id,
                windows_job_assigned=self._windows_job_assigned,
                descendant_fallback=self._descendant_fallback,
                command=self._command,
                cwd=self._cwd,
                started_at=self._started_at,
                stopped_at=self._stopped_at,
                returncode=(
                    process.poll() if os.name == "nt" and process is not None else self._returncode
                ),
                last_error=self._last_error,
                log_tail=self._log_tail.snapshot(),
            )

    def _reset_for_launch(self, redactor: SecretRedactor) -> None:
        if self._posix_pidfd is not None:
            raise RuntimeError("cannot reset while stable POSIX pidfd authority is retained")
        self._process = None
        self._identity = None
        self._process_group_id = None
        self._close_windows_job()
        self._windows_job_assigned = False
        self._descendant_fallback = False
        self._stopping = False
        self._stop_requested = False
        self._stopped_at = None
        self._returncode = None
        self._posix_root_reaped = False
        self._posix_authority_valid = False
        self._posix_cleanup_uncertain = False
        self._last_error = None
        self._drain_threads = []
        self._known_descendants = {}
        self._descendant_thread = None
        self._monitor_thread = None
        self._log_tail = BoundedLogTail(
            max_lines=self._max_log_lines,
            max_bytes=self._max_log_bytes,
            redactor=redactor,
        )

    def _cleanup_failed_launch(self) -> tuple[bool, list[str]]:
        """Best-effort cleanup that never discards a possibly live owned record."""

        process = self._process
        identity = self._identity
        errors: list[str] = []
        if process is None:
            try:
                self._close_windows_job()
            except Exception as exc:
                errors.append(f"job close failed ({type(exc).__name__})")
            return True, errors

        try:
            if os.name == "nt" and self._windows_job_assigned and self._windows_job is not None:
                try:
                    self._windows_job.terminate(1)
                except Exception as exc:
                    errors.append(f"job termination failed ({type(exc).__name__})")
                try:
                    self._close_windows_job()
                except Exception as exc:
                    errors.append(f"job close failed ({type(exc).__name__})")
            elif os.name == "nt" and identity is not None:
                identities = self._validated_windows_tree(identity)
                self._terminate_identities(identities, kill=True)
            elif os.name != "nt" and identity is not None:
                _, _, platform_errors = self._stop_posix_group(
                    process,
                    identity,
                    0.0,
                    5.0,
                )
                errors.extend(platform_errors)
            elif os.name != "nt":
                # Exact identity capture failed after Popen established the new
                # session.  That launch contract remains sufficient to clean the
                # provisional group, but it is not sufficient to enter RUNNING.
                errors.extend(self._cleanup_provisional_posix_launch(process, 5.0))

            if os.name == "nt":
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
        except Exception as exc:
            errors.append(f"root cleanup failed ({type(exc).__name__})")

        remaining = self._remaining_owned_pids(identity) if identity is not None else ()
        unclosed_job = (
            os.name == "nt" and self._windows_job_assigned and self._windows_job is not None
        )
        if os.name == "nt":
            self._returncode = process.poll()
            root_cleanup_complete = self._returncode is not None
        else:
            root_cleanup_complete = self._posix_root_reaped
        complete = (
            root_cleanup_complete
            and self._process_group_id is None
            and not remaining
            and not self._posix_cleanup_uncertain
            and not unclosed_job
        )
        self._stopping = False
        if complete:
            self._close_posix_pidfd()
            self._process = None
            self._identity = None
            self._process_group_id = None
            self._known_descendants.clear()
            self._descendant_fallback = False
            self._stopped_at = time.time()
        return complete, errors

    def _cleanup_provisional_posix_launch(
        self,
        process: subprocess.Popen[bytes],
        timeout: float,
    ) -> list[str]:
        """Clean a start-new-session launch whose exact identity capture failed."""

        errors: list[str] = []
        with self._posix_cleanup_lock:
            with self._lock:
                pgid = self._process_group_id
                provisional = bool(
                    self._process is process
                    and self._identity is None
                    and self._posix_authority_valid
                    and pgid == process.pid
                    and pgid != os.getpgrp()
                )

            if not provisional or pgid is None:
                errors.append("provisional POSIX launch authority is unavailable")
                return errors

            proof = self._prove_provisional_posix_authority(process, pgid)
            if proof.state is not _PosixAuthorityState.VALID:
                errors.append(
                    f"provisional POSIX launch authority is {proof.state.value} ({proof.reason})"
                )
                return errors

            try:
                with self._lock:
                    pidfd = self._posix_pidfd
                atomic_signaled = False
                if pidfd is not None and sys.platform.startswith("linux"):
                    if self._signal_linux_pidfd_group(pidfd, signal.SIGKILL):
                        atomic_signaled = True
                    else:
                        fallback_proof = self._prove_provisional_posix_authority(process, pgid)
                        with self._lock:
                            current_pidfd = self._posix_pidfd
                        if (
                            fallback_proof.state is not _PosixAuthorityState.VALID
                            or fallback_proof.pgid != pgid
                            or current_pidfd != pidfd
                        ):
                            raise RuntimeError(
                                "provisional authority changed after pidfd signal fallback"
                            )
                if not atomic_signaled:
                    os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception as exc:
                errors.append(f"provisional group cleanup failed ({type(exc).__name__}: {exc})")
                return errors

            group_inspection = self._wait_for_posix_pgid_empty(pgid, timeout)
            if group_inspection.state is _PosixAuthorityState.INDETERMINATE:
                errors.append(
                    "provisional group cleanup inspection is indeterminate "
                    f"({group_inspection.reason})"
                )
                return errors
            remaining = group_inspection.members
            if remaining:
                errors.append(
                    "provisional group cleanup incomplete; live PIDs: "
                    + ", ".join(map(str, remaining))
                )
                return errors

            with self._lock:
                if self._process is process and self._identity is None:
                    self._process_group_id = None
                    self._posix_authority_valid = False
            try:
                self._returncode = self._consume_posix_exit(process, None, timeout)
            except subprocess.TimeoutExpired as exc:
                # A missed live root means the provisional group remains the
                # only safe broad authority.  Restore it for a later retry.
                with self._lock:
                    if self._process is process and self._identity is None:
                        self._process_group_id = pgid
                        self._posix_authority_valid = True
                errors.append(f"provisional root reap timed out ({type(exc).__name__}: {exc})")
            except Exception as exc:
                with self._lock:
                    if self._process is process and self._identity is None:
                        self._process_group_id = pgid
                        self._posix_authority_valid = True
                errors.append(f"provisional root reap failed ({type(exc).__name__}: {exc})")
        return errors

    def _retry_provisional_posix_stop(
        self,
        process: subprocess.Popen[bytes],
        *,
        started: float,
        timeout: float,
    ) -> StopResult:
        errors = self._cleanup_provisional_posix_launch(process, timeout)
        with self._lock:
            complete = bool(
                self._process is process
                and self._identity is None
                and self._posix_root_reaped
                and self._process_group_id is None
                and not self._posix_cleanup_uncertain
            )
            self._stopping = False
            self._stopped_at = time.time() if complete else None
            if complete:
                self._state = ProcessLifecycle.STOPPED
                self._close_posix_pidfd()
                self._process = None
                self._last_error = "; ".join(errors) or None
            else:
                self._state = ProcessLifecycle.INCOMPLETE_STOP
                self._last_error = "; ".join(errors) or (
                    "provisional POSIX cleanup remains incomplete"
                )
            return StopResult(
                complete=complete,
                escalated=True,
                returncode=self._returncode,
                duration_seconds=time.monotonic() - started,
                remaining_pids=() if complete else (process.pid,),
                error=self._last_error,
            )

    def _capture_identity(self, pid: int) -> ProcessIdentity:
        try:
            created = float(self._psutil.Process(pid).create_time())
        except Exception as exc:
            raise RuntimeError("failed to capture exact process creation identity") from exc
        return ProcessIdentity(pid=pid, create_time=created)

    @staticmethod
    def _open_linux_pidfd(pid: int) -> int | None:
        """Open a stable Linux process-generation handle, including old Python builds."""

        if not sys.platform.startswith("linux"):
            return None

        fd: int | None = None
        try:
            opener = getattr(os, "pidfd_open", None)
            if opener is not None:
                fd = int(opener(pid, 0))
            else:
                import ctypes

                libc = ctypes.CDLL(None, use_errno=True)
                try:
                    pidfd_open = libc.pidfd_open
                except AttributeError:
                    # Linux assigned pidfd_open syscall 434 consistently on the
                    # mainstream architectures below.  Do not guess on an
                    # unknown ABI: losing the generation handle must fail closed.
                    supported_machines = {
                        "aarch64",
                        "amd64",
                        "arm64",
                        "armv7l",
                        "armv8l",
                        "i386",
                        "i486",
                        "i586",
                        "i686",
                        "ppc64",
                        "ppc64le",
                        "riscv64",
                        "s390x",
                        "x86_64",
                    }
                    if os.uname().machine.lower() not in supported_machines:
                        raise OSError(
                            errno.ENOSYS,
                            "unknown pidfd_open syscall ABI",
                        ) from None
                    syscall = libc.syscall
                    syscall.restype = ctypes.c_long
                    ctypes.set_errno(0)
                    fd = int(
                        syscall(
                            ctypes.c_long(434),
                            ctypes.c_int(int(pid)),
                            ctypes.c_uint(0),
                        )
                    )
                else:
                    pidfd_open.argtypes = [ctypes.c_int, ctypes.c_uint]
                    pidfd_open.restype = ctypes.c_int
                    ctypes.set_errno(0)
                    fd = int(pidfd_open(int(pid), 0))
                if fd < 0:
                    error = ctypes.get_errno() or errno.EIO
                    raise OSError(error, os.strerror(error))
            if fd < 0:
                raise OSError(errno.EBADF, "pidfd_open returned a negative descriptor")
            os.set_inheritable(fd, False)
            os.fstat(fd)
            return fd
        except (AttributeError, OSError) as exc:
            if fd is not None and fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            LOGGER.warning("stable Linux pidfd unavailable (%s)", type(exc).__name__)
            return None

    def _close_posix_pidfd(self, expected_fd: int | None = None) -> None:
        with self._lock:
            if expected_fd is not None and self._posix_pidfd != expected_fd:
                return
            fd, self._posix_pidfd = self._posix_pidfd, None
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise

    def _duplicate_linux_pidfd(self, expected_fd: int | None = None) -> int | None:
        """Duplicate the current pidfd while its controller slot is locked."""

        if not sys.platform.startswith("linux"):
            return None
        with self._lock:
            fd = self._posix_pidfd
            if fd is None or (expected_fd is not None and fd != expected_fd):
                return None
            duplicate = os.dup(fd)
        try:
            os.set_inheritable(duplicate, False)
        except Exception:
            os.close(duplicate)
            raise
        return duplicate

    def _require_linux_pidfd_support(self) -> None:
        """Fail before spawn unless Linux can open and inspect pidfds."""

        if not sys.platform.startswith("linux"):
            return
        waitid = getattr(os, "waitid", None)
        required = ("WEXITED", "WNOWAIT", "WNOHANG")
        if waitid is None or not all(hasattr(os, name) for name in required):
            raise RuntimeError(
                "Linux process ownership requires waitid(P_PIDFD); "
                "upgrade the kernel and Python runtime"
            )

        probe_pidfd = self._open_linux_pidfd(os.getpid())
        if probe_pidfd is None:
            raise RuntimeError(
                "Linux process ownership requires pidfd_open; "
                "upgrade the kernel or libc/Python runtime"
            )
        try:
            try:
                waitid(
                    getattr(os, "P_PIDFD", 3),
                    probe_pidfd,
                    os.WEXITED | os.WNOWAIT | os.WNOHANG,
                )
            except ChildProcessError:
                # A pidfd for this process is intentionally not our child.
                # ECHILD proves that the kernel recognized P_PIDFD semantics.
                return
            except OSError as exc:
                if exc.errno == errno.ECHILD:
                    return
                if exc.errno in {errno.EINVAL, errno.ENOSYS, errno.EBADF}:
                    raise RuntimeError(
                        "Linux process ownership requires functional waitid(P_PIDFD); "
                        "upgrade the kernel and Python runtime"
                    ) from exc
                raise RuntimeError(
                    f"Linux pidfd capability probe failed ({type(exc).__name__}: {exc})"
                ) from exc
        finally:
            os.close(probe_pidfd)

    @staticmethod
    def _platform_launch_command(command: Sequence[str]) -> tuple[str, ...]:
        if os.name != "nt" and sys.platform.startswith("linux"):
            supervisor = Path(__file__).with_name("_posix_supervisor.py")
            return (
                sys.executable,
                "-u",
                str(supervisor),
                str(os.getpid()),
                "--",
                *command,
            )
        return tuple(command)

    def _setup_windows_ownership(self, process: subprocess.Popen[bytes]) -> None:
        job: Any | None = None
        try:
            job = self._windows_job_factory()
            job.assign(process)
            self._windows_job = job
            self._windows_job_assigned = True
            self._descendant_fallback = False
        except Exception as exc:
            self._windows_job_assigned = False
            self._descendant_fallback = True
            self._last_error = (
                f"Windows Job Object unavailable; validated fallback active ({type(exc).__name__})"
            )
            if job is not None:
                try:
                    job.close()
                except Exception as close_exc:
                    # Retain the exact handle so a later stop/start can retry the
                    # checked close instead of silently leaking it.
                    self._windows_job = job
                    self._last_error += f"; handle close failed ({type(close_exc).__name__})"

    def _start_log_drains(
        self,
        process: subprocess.Popen[bytes],
        redactor: SecretRedactor,
    ) -> None:
        # Bind every drain to this launch's immutable tail reference.  A late
        # buffered read from an older process must never append through the
        # controller's mutable next-generation ``self._log_tail`` slot.
        log_tail = self._log_tail
        for channel, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._drain_stream,
                args=(channel, stream, redactor, log_tail),
                name=f"llamacpp-{channel}-{process.pid}",
                daemon=True,
            )
            self._drain_threads.append(thread)
            thread.start()

    def _drain_stream(
        self,
        channel: str,
        stream: Any,
        redactor: SecretRedactor,
        log_tail: BoundedLogTail,
    ) -> None:
        raw_pending = ""
        output_pending = ""
        literal_overlap = max(1, redactor.max_secret_length)
        credential_redactor = _StreamingCredentialRedactor()
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def store_sanitized(text: str) -> None:
            nonlocal output_pending
            output_pending += text
            while "\n" in output_pending:
                line, output_pending = output_pending.split("\n", 1)
                log_tail.append(channel, line)
            while len(output_pending) > 32 * 1024:
                log_tail.append(channel, output_pending[: 32 * 1024])
                output_pending = output_pending[32 * 1024 :]

        def sanitize_raw_prefix(text: str) -> None:
            literal_clean = redactor.redact_literals(text)
            store_sanitized(credential_redactor.feed(literal_clean))

        def flush_ready_raw() -> None:
            nonlocal raw_pending
            while True:
                proposed = max(0, len(raw_pending) - literal_overlap)
                newline = raw_pending.rfind("\n", 0, proposed)
                size_flush_required = len(raw_pending) > 32 * 1024 + literal_overlap
                if newline >= 0:
                    candidate = newline + 1
                elif size_flush_required:
                    candidate = proposed
                else:
                    return

                flush_at = redactor.literal_safe_flush_boundary(raw_pending, candidate)
                if flush_at <= 0:
                    if size_flush_required:
                        raise RuntimeError("unable to make progress at a safe log flush boundary")
                    return
                sanitize_raw_prefix(raw_pending[:flush_at])
                raw_pending = raw_pending[flush_at:]

        try:
            while True:
                reader = getattr(stream, "read1", stream.read)
                chunk = reader(4096)
                if not chunk:
                    raw_pending += decoder.decode(b"", final=True)
                    break
                if isinstance(chunk, bytes):
                    raw_pending += decoder.decode(chunk, final=False)
                else:
                    # A text-producing stream is already decoded.  Finalize any
                    # preceding byte sequence before switching representations,
                    # then reset so later byte chunks remain deterministic.
                    raw_pending += decoder.decode(b"", final=True)
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                    raw_pending += str(chunk)
                flush_ready_raw()

            sanitize_raw_prefix(raw_pending)
            store_sanitized(credential_redactor.feed("", final=True))
            if output_pending:
                log_tail.append(channel, output_pending)
        except Exception as exc:
            log_tail.append(channel, f"<log drain failed: {type(exc).__name__}>")
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _start_monitor(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
    ) -> None:
        monitor_pidfd: int | None = None
        if sys.platform.startswith("linux"):
            monitor_pidfd = self._duplicate_linux_pidfd()
            if monitor_pidfd is None:
                raise RuntimeError("launch-time pidfd is unavailable for the process monitor")
        try:
            thread = threading.Thread(
                target=self._monitor,
                args=(process, identity, monitor_pidfd),
                name=f"llamacpp-monitor-{identity.pid}",
                daemon=True,
            )
            self._monitor_thread = thread
            thread.start()
        except Exception:
            if monitor_pidfd is not None:
                os.close(monitor_pidfd)
            raise

    def _start_descendant_tracker(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
    ) -> None:
        """Continuously retain positively identified descendants while authority exists."""

        thread = threading.Thread(
            target=self._track_descendants,
            args=(process, identity),
            name=f"llamacpp-descendants-{identity.pid}",
            daemon=True,
        )
        self._descendant_thread = thread
        thread.start()

    def _track_descendants(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
    ) -> None:
        while True:
            with self._lock:
                if self._process is not process or self._identity != identity:
                    return
            self._collect_windows_descendants(identity)
            exited = process.poll() is not None
            if exited:
                return
            time.sleep(0.05)

    def _monitor(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
        monitor_pidfd: int | None = None,
    ) -> None:
        if os.name != "nt":
            try:
                self._monitor_posix(process, identity, monitor_pidfd)
            finally:
                if monitor_pidfd is not None:
                    os.close(monitor_pidfd)
            return

        try:
            returncode = process.wait()
        except Exception as exc:
            with self._lock:
                if self._process is process and self._identity == identity:
                    self._last_error = f"process monitor failed: {type(exc).__name__}"
            return

        with self._lock:
            if self._process is not process or self._identity != identity:
                return
            self._returncode = returncode
            if self._stopping:
                return
            self._state = ProcessLifecycle.RUNTIME_FAILED
            self._last_error = f"owned process exited unexpectedly with code {returncode}"

        # A parent can fail while leaving workers alive.  The Job Object or
        # positively retained identities, never a process-name sweep, remain
        # the cleanup authority.
        try:
            if self._windows_job_assigned and self._windows_job is not None:
                self._windows_job.terminate(1)
                self._close_windows_job()
            else:
                self._kill_validated_descendants(identity)
        except Exception as exc:
            with self._lock:
                if self._process is process and self._identity == identity:
                    self._last_error += f"; descendant cleanup failed ({type(exc).__name__})"

    def _monitor_posix(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
        monitor_pidfd: int | None = None,
    ) -> None:
        """Observe exit without reaping, clean the anchored group, then reap."""

        try:
            wait_result, already_reaped = self._wait_for_posix_exit(identity, monitor_pidfd)
            observed_returncode = self._waitid_returncode(wait_result)
        except Exception as exc:
            with self._lock:
                if self._process is process and self._identity == identity:
                    self._state = ProcessLifecycle.RUNTIME_FAILED
                    self._last_error = (
                        f"process monitor failed without reaping ({type(exc).__name__}: {exc})"
                    )
            return

        with self._posix_cleanup_lock:
            with self._lock:
                if self._process is not process or self._identity != identity:
                    return
                if self._stop_requested:
                    # Explicit stop owns the same cleanup lock and will retire
                    # the group authority before it reaps the leader.
                    return
                self._returncode = (
                    observed_returncode if observed_returncode is not None else self._returncode
                )
                self._state = ProcessLifecycle.RUNTIME_FAILED
                suffix = (
                    f" with code {observed_returncode}" if observed_returncode is not None else ""
                )
                self._last_error = f"owned process exited unexpectedly{suffix}"

            errors: list[str] = []
            remaining: tuple[int, ...] = ()
            proof = self._prove_posix_group_authority(identity)
            if already_reaped or proof.state is _PosixAuthorityState.INVALID:
                # An external waiter or SIGCHLD policy has already destroyed the
                # group-generation anchor.  Retire the PGID and fail closed.  A
                # cached numeric PID/PGID is not authority to signal anything.
                self._retire_reaped_posix_authority(
                    process,
                    identity,
                    externally_reaped=already_reaped or "ECHILD" in proof.reason,
                    stable_pidfd=monitor_pidfd,
                )
                remaining = (identity.pid,)
                errors.append(
                    "POSIX group authority is invalid; refusing descendant cleanup "
                    f"({proof.reason})"
                )
            elif proof.state is _PosixAuthorityState.INDETERMINATE:
                # Preserve the unreaped anchor and cached authority so a later
                # explicit stop can retry after a transient inspection failure.
                remaining = (identity.pid,)
                errors.append(
                    "POSIX group authority is indeterminate; retaining it for retry "
                    f"({proof.reason})"
                )
            else:
                signal_failed = False
                try:
                    self._signal_posix_group(signal.SIGKILL, identity)
                except ProcessLookupError:
                    pass
                except Exception as exc:
                    signal_failed = True
                    errors.append(f"group cleanup failed ({type(exc).__name__}: {exc})")
                remaining = (
                    (identity.pid,)
                    if signal_failed
                    else self._wait_for_owned_tree(process, identity, 3.0)
                )
                if not remaining and not errors:
                    try:
                        self._retire_posix_authority_and_reap(process, identity)
                    except Exception as exc:
                        errors.append(f"root reap failed ({type(exc).__name__}: {exc})")

            with self._lock:
                if self._identity != identity:
                    return
                if remaining:
                    errors.append("owned descendants remain: " + ", ".join(map(str, remaining)))
                if errors:
                    self._last_error += "; descendant cleanup incomplete: " + "; ".join(errors)

    def _wait_for_posix_exit(
        self,
        identity: ProcessIdentity,
        monitor_pidfd: int | None = None,
    ) -> tuple[Any | None, bool]:
        """Observe child exit without consuming the zombie group leader."""

        waitid = getattr(os, "waitid", None)
        if sys.platform.startswith("linux") and monitor_pidfd is None:
            raise RuntimeError("launch-time monitor pidfd is unavailable")
        wait_pidfd = monitor_pidfd

        required = ("WEXITED", "WNOWAIT")
        can_wait = bool(
            waitid is not None
            and all(hasattr(os, name) for name in required)
            and (wait_pidfd is not None or hasattr(os, "P_PID"))
        )
        if sys.platform.startswith("linux") and not can_wait:
            raise RuntimeError("pidfd monitor waitid is unavailable; refusing numeric fallback")
        if can_wait and (wait_pidfd is not None or not sys.platform.startswith("linux")):
            while True:
                try:
                    result = waitid(
                        (getattr(os, "P_PIDFD", 3) if wait_pidfd is not None else os.P_PID),
                        wait_pidfd if wait_pidfd is not None else identity.pid,
                        os.WEXITED | os.WNOWAIT,
                    )
                    if result is None or int(getattr(result, "si_pid", -1)) != identity.pid:
                        raise RuntimeError("waitid returned an unexpected child identity")
                    return result, False
                except InterruptedError:
                    continue
                except ChildProcessError:
                    # A third-party SIGCHLD policy or waiter consumed the
                    # exact launch generation.
                    return None, True
                except OSError as exc:
                    if exc.errno == errno.ECHILD:
                        return None, True
                    if exc.errno == errno.EBADF:
                        raise _PosixPidfdInvalid("monitor pidfd became invalid (EBADF)") from exc
                    if exc.errno in {errno.ENOSYS, errno.EINVAL}:
                        if sys.platform.startswith("linux"):
                            raise RuntimeError(
                                "pidfd monitor waitid is unsupported; refusing numeric fallback"
                            ) from exc
                        break
                    raise

        # Portable fail-closed fallback.  Reading psutil status does not reap.
        # If the identity disappears without a zombie observation, assume an
        # external waiter consumed it and retire group authority.
        while True:
            try:
                process = self._psutil.Process(identity.pid)
                if not self._identity_matches(process, identity):
                    return None, True
                if process.status() == getattr(self._psutil, "STATUS_ZOMBIE", "zombie"):
                    return None, False
            except self._psutil.AccessDenied:
                pass
            except (ProcessLookupError, self._psutil.NoSuchProcess):
                return None, True
            time.sleep(0.05)

    @staticmethod
    def _waitid_returncode(result: Any | None) -> int | None:
        if result is None:
            return None
        code = getattr(result, "si_code", None)
        status = getattr(result, "si_status", None)
        if status is None:
            raise RuntimeError("waitid result omitted exit status")
        if code == getattr(os, "CLD_EXITED", object()):
            return int(status)
        if code in {
            getattr(os, "CLD_KILLED", object()),
            getattr(os, "CLD_DUMPED", object()),
        }:
            return -int(status)
        raise RuntimeError(f"waitid returned unsupported exit code {code!r}")

    def _consume_posix_exit(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity | None,
        timeout: float,
        *,
        stable_pidfd: int | None = None,
    ) -> int | None:
        """Consume only the exact launch generation and retire its pidfd."""

        if not sys.platform.startswith("linux"):
            returncode = process.wait(timeout=timeout)
            with self._lock:
                if self._process is process and self._identity == identity:
                    self._returncode = returncode
                    self._posix_root_reaped = True
            return returncode

        with self._lock:
            if self._process is not process or self._identity != identity:
                raise RuntimeError("POSIX process ownership changed before reap")
            pidfd = self._posix_pidfd
        if pidfd is None and stable_pidfd is None:
            raise RuntimeError("launch-time pidfd is unavailable for stable reap")

        waitid = getattr(os, "waitid", None)
        if waitid is None or not all(hasattr(os, name) for name in ("WEXITED", "WNOHANG")):
            raise RuntimeError("pidfd waitid consumption is unavailable")

        close_wait_pidfd = False
        if stable_pidfd is not None:
            wait_pidfd = stable_pidfd
            try:
                os.fstat(wait_pidfd)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    raise _PosixPidfdInvalid("stable reap pidfd became invalid (EBADF)") from exc
                raise
        else:
            try:
                wait_pidfd = self._duplicate_linux_pidfd(pidfd)
            except OSError as exc:
                raise RuntimeError("failed to duplicate reap pidfd") from exc
            if wait_pidfd is None:
                raise RuntimeError("pidfd authority changed before stable reap")
            close_wait_pidfd = True

        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    result = waitid(
                        getattr(os, "P_PIDFD", 3),
                        wait_pidfd,
                        os.WEXITED | os.WNOHANG,
                    )
                except InterruptedError:
                    continue
                except ChildProcessError:
                    result = None
                    externally_reaped = True
                    break
                except OSError as exc:
                    if exc.errno == errno.ECHILD:
                        result = None
                        externally_reaped = True
                        break
                    if exc.errno == errno.EBADF:
                        raise _PosixPidfdInvalid("reap pidfd became invalid (EBADF)") from exc
                    raise RuntimeError(
                        f"pidfd waitid consumption failed ({type(exc).__name__})"
                    ) from exc

                externally_reaped = False
                observed_pid = int(getattr(result, "si_pid", 0)) if result is not None else 0
                if observed_pid:
                    if observed_pid != process.pid:
                        raise RuntimeError("pidfd waitid returned an unexpected child identity")
                    break
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(getattr(process, "args", process.pid), timeout)
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        finally:
            if close_wait_pidfd:
                os.close(wait_pidfd)

        if externally_reaped:
            # Mirror subprocess' ECHILD convention only to prevent a later
            # Popen destructor from issuing waitpid against a recycled PID.
            returncode = self._returncode
            if getattr(process, "returncode", None) is None:
                process.returncode = returncode if returncode is not None else 0
        else:
            returncode = self._waitid_returncode(result)
            process.returncode = returncode

        with self._lock:
            if self._process is process and self._identity == identity:
                if returncode is not None:
                    self._returncode = returncode
                self._posix_root_reaped = True
                if externally_reaped:
                    self._posix_cleanup_uncertain = True
                    diagnostic = "exact pidfd exit status was consumed externally (ECHILD)"
                    if not self._last_error:
                        self._last_error = diagnostic
                    elif diagnostic not in self._last_error:
                        self._last_error += f"; {diagnostic}"
        if pidfd is not None:
            self._close_posix_pidfd(pidfd)
        return returncode

    def _posix_exit_observed_nowait(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
    ) -> bool:
        """Check root exit without reaping; fail closed if another waiter reaped it."""

        waitid = getattr(os, "waitid", None)
        wait_pidfd: int | None = None
        if sys.platform.startswith("linux"):
            try:
                wait_pidfd = self._duplicate_linux_pidfd()
            except OSError as exc:
                raise RuntimeError("failed to duplicate observation pidfd") from exc
        try:
            required = ("WEXITED", "WNOWAIT", "WNOHANG")
            can_wait = bool(
                waitid is not None
                and all(hasattr(os, name) for name in required)
                and (wait_pidfd is not None or hasattr(os, "P_PID"))
            )
            if sys.platform.startswith("linux") and (wait_pidfd is None or not can_wait):
                raise RuntimeError(
                    "pidfd observation waitid is unavailable; refusing numeric fallback"
                )
            if can_wait and (wait_pidfd is not None or not sys.platform.startswith("linux")):
                while True:
                    try:
                        result = waitid(
                            (getattr(os, "P_PIDFD", 3) if wait_pidfd is not None else os.P_PID),
                            wait_pidfd if wait_pidfd is not None else identity.pid,
                            os.WEXITED | os.WNOWAIT | os.WNOHANG,
                        )
                        if result is None or int(getattr(result, "si_pid", 0)) == 0:
                            return False
                        if int(getattr(result, "si_pid", -1)) != identity.pid:
                            raise RuntimeError("waitid returned an unexpected child identity")
                        returncode = self._waitid_returncode(result)
                        with self._lock:
                            if self._process is process and self._identity == identity:
                                self._returncode = returncode
                        return True
                    except InterruptedError:
                        continue
                    except ChildProcessError:
                        self._retire_reaped_posix_authority(
                            process,
                            identity,
                            externally_reaped=True,
                        )
                        return False
                    except OSError as exc:
                        if exc.errno == errno.ECHILD:
                            self._retire_reaped_posix_authority(
                                process,
                                identity,
                                externally_reaped=True,
                            )
                            return False
                        if exc.errno == errno.EBADF:
                            raise _PosixPidfdInvalid(
                                "observation pidfd became invalid (EBADF)"
                            ) from exc
                        if exc.errno in {errno.ENOSYS, errno.EINVAL}:
                            if sys.platform.startswith("linux"):
                                raise RuntimeError(
                                    "pidfd observation waitid is unsupported; "
                                    "refusing numeric fallback"
                                ) from exc
                            break
                        raise
        finally:
            if wait_pidfd is not None:
                os.close(wait_pidfd)

        try:
            root = self._psutil.Process(identity.pid)
            if not self._identity_matches(root, identity):
                self._retire_reaped_posix_authority(
                    process,
                    identity,
                    externally_reaped=True,
                )
                return False
            return root.status() == getattr(self._psutil, "STATUS_ZOMBIE", "zombie")
        except self._psutil.AccessDenied:
            return False
        except (ProcessLookupError, self._psutil.NoSuchProcess):
            self._retire_reaped_posix_authority(
                process,
                identity,
                externally_reaped=True,
            )
            return False

    def _stop_posix_group(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
        grace_timeout: float,
        kill_timeout: float,
    ) -> tuple[bool, tuple[int, ...], list[str]]:
        with self._posix_cleanup_lock:
            errors: list[str] = []
            escalated = False
            proof = self._prove_posix_group_authority(identity)
            if proof.state is not _PosixAuthorityState.VALID:
                if (
                    self._posix_root_reaped
                    and self._process_group_id is None
                    and not self._posix_cleanup_uncertain
                ):
                    return False, (), errors
                errors.append(
                    "POSIX group authority is unavailable; refusing to signal a numeric PGID "
                    f"({proof.state.value}: {proof.reason})"
                )
                remaining = () if self._posix_root_reaped else (identity.pid,)
                return False, remaining, errors

            term_failed = False
            try:
                self._signal_posix_group(signal.SIGTERM, identity)
            except ProcessLookupError:
                pass
            except Exception as exc:
                term_failed = True
                errors.append(f"SIGTERM failed ({type(exc).__name__}: {exc})")

            remaining = self._wait_for_owned_tree(process, identity, grace_timeout)
            if term_failed and not remaining:
                remaining = (identity.pid,)
            if not remaining and not self._posix_exit_observed_nowait(process, identity):
                remaining = (identity.pid,)
            if remaining:
                escalated = True
                kill_failed = False
                try:
                    proof = self._prove_posix_group_authority(identity)
                    if proof.state is _PosixAuthorityState.VALID:
                        self._signal_posix_group(signal.SIGKILL, identity)
                    else:
                        raise RuntimeError(
                            "POSIX group authority is unavailable before escalation "
                            f"({proof.state.value}: {proof.reason})"
                        )
                except ProcessLookupError:
                    pass
                except Exception as exc:
                    kill_failed = True
                    errors.append(f"SIGKILL failed ({type(exc).__name__}: {exc})")
                remaining = (
                    (identity.pid,)
                    if kill_failed
                    else self._wait_for_owned_tree(process, identity, kill_timeout)
                )
                if not remaining and not self._posix_exit_observed_nowait(process, identity):
                    remaining = (identity.pid,)

            if not remaining and not self._posix_root_reaped:
                try:
                    self._retire_posix_authority_and_reap(process, identity)
                except Exception as exc:
                    errors.append(f"root reap failed ({type(exc).__name__}: {exc})")

            return escalated, remaining, errors

    def _signal_posix_group(self, sig: int, identity: ProcessIdentity) -> None:
        with self._posix_cleanup_lock:
            proof = self._prove_posix_group_authority(identity)
            if proof.state is not _PosixAuthorityState.VALID or proof.pgid is None:
                raise RuntimeError(
                    "refusing to signal a process group with "
                    f"{proof.state.value} authority ({proof.reason})"
                )
            with self._lock:
                pidfd = self._posix_pidfd
            if pidfd is not None and sys.platform.startswith("linux"):
                # Linux 6.9 added an atomic process-group signal flag for
                # pidfd_send_signal.  Older kernels return EINVAL, in which
                # case the already-validated killpg fallback remains necessary.
                if self._signal_linux_pidfd_group(pidfd, sig):
                    return

                fallback_proof = self._prove_posix_group_authority(identity)
                with self._lock:
                    current_pidfd = self._posix_pidfd
                if (
                    fallback_proof.state is not _PosixAuthorityState.VALID
                    or fallback_proof.pgid != proof.pgid
                    or current_pidfd != pidfd
                ):
                    raise RuntimeError("POSIX group authority changed after pidfd signal fallback")

            # On older kernels the exact child relationship, creation identity,
            # and current PGID were checked immediately before this call.
            os.killpg(proof.pgid, sig)

    @staticmethod
    def _signal_linux_pidfd_group(pidfd: int, sig: int) -> bool:
        """Atomically signal a pidfd's process group when the kernel supports it."""

        try:
            OwnedProcessController._send_linux_pidfd_signal(pidfd, sig, 1 << 2)
        except OSError as exc:
            if exc.errno in {errno.EINVAL, errno.ENOSYS}:
                return False
            raise
        return True

    @staticmethod
    def _signal_linux_pidfd_process(pidfd: int, sig: int) -> None:
        """Signal only the exact process generation represented by a pidfd."""

        OwnedProcessController._send_linux_pidfd_signal(pidfd, sig, 0)

    @staticmethod
    def _send_linux_pidfd_signal(pidfd: int, sig: int, flags: int) -> None:
        """Invoke pidfd_send_signal through libc or an architecture-checked syscall."""

        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        try:
            sender = libc.pidfd_send_signal
        except AttributeError:
            supported_machines = {
                "aarch64",
                "amd64",
                "arm64",
                "armv7l",
                "armv8l",
                "i386",
                "i486",
                "i586",
                "i686",
                "ppc64",
                "ppc64le",
                "riscv64",
                "s390x",
                "x86_64",
            }
            if os.uname().machine.lower() not in supported_machines:
                raise OSError(errno.ENOSYS, "unknown pidfd_send_signal syscall ABI") from None
            syscall = libc.syscall
            syscall.restype = ctypes.c_long
            ctypes.set_errno(0)
            result = int(
                syscall(
                    ctypes.c_long(424),
                    ctypes.c_int(pidfd),
                    ctypes.c_int(sig),
                    ctypes.c_void_p(),
                    ctypes.c_uint(flags),
                )
            )
        else:
            sender.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            sender.restype = ctypes.c_int
            ctypes.set_errno(0)
            result = int(sender(pidfd, sig, None, flags))

        if result == 0:
            return
        error = ctypes.get_errno() or errno.EIO
        if error == errno.ESRCH:
            raise ProcessLookupError(error, os.strerror(error))
        raise OSError(error, os.strerror(error))

    def _prove_posix_group_authority(
        self,
        identity: ProcessIdentity,
    ) -> _PosixAuthorityProof:
        with self._lock:
            process = self._process
            pgid = self._process_group_id
            pidfd = self._posix_pidfd
            cached_authority = bool(
                self._posix_authority_valid
                and process is not None
                and process.pid == identity.pid
                and pgid is not None
                and pgid != os.getpgrp()
                and self._identity == identity
            )
        if not cached_authority:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INVALID,
                "cached launch authority is unavailable",
            )
        if sys.platform.startswith("linux") and pidfd is None:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INDETERMINATE,
                "launch-time pidfd generation is unavailable",
            )

        # This is a kernel-level child relationship check, not a timestamp
        # heuristic.  A recycled PID is not our child and produces ECHILD.
        child_proof = self._probe_posix_child_anchor(identity.pid, pidfd)
        if child_proof.state is not _PosixAuthorityState.VALID:
            return self._finish_posix_authority_proof(identity, pgid, child_proof)

        identity_proof = self._probe_posix_leader_identity(identity, pgid)
        create_time_drift = bool(
            identity_proof.state is _PosixAuthorityState.INVALID
            and identity_proof.reason == "leader creation identity changed"
            and identity_proof.pgid == pgid
        )
        if identity_proof.state is not _PosixAuthorityState.VALID and not create_time_drift:
            return self._finish_posix_authority_proof(identity, pgid, identity_proof)

        # Narrow the unavoidable check-to-kill interval by proving the kernel
        # child relationship again after psutil and getpgid inspection.
        final_child_proof = self._probe_posix_child_anchor(identity.pid, pidfd)
        if final_child_proof.state is not _PosixAuthorityState.VALID:
            return self._finish_posix_authority_proof(identity, pgid, final_child_proof)

        with self._lock:
            if (
                self._identity != identity
                or self._process_group_id != pgid
                or self._posix_pidfd != pidfd
            ):
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INDETERMINATE,
                    "launch authority changed during validation",
                )

        if create_time_drift:
            # psutil derives wall-clock create_time from platform boot-time
            # data.  WSL can rebase that value while the process remains the
            # same unreaped kernel child.  Two waitid proofs plus current PGID
            # safely override only this wall-clock drift.
            if pidfd is None:
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INDETERMINATE,
                    "create-time drift requires the launch-time pidfd generation",
                )
            return _PosixAuthorityProof(
                _PosixAuthorityState.VALID,
                "stable pidfd generation and process group override create-time drift",
                pgid,
            )

        return _PosixAuthorityProof(
            _PosixAuthorityState.VALID,
            "exact child identity and process group match",
            pgid,
        )

    def _prove_provisional_posix_authority(
        self,
        process: subprocess.Popen[bytes],
        pgid: int,
    ) -> _PosixAuthorityProof:
        with self._lock:
            pidfd = self._posix_pidfd
            cached = bool(
                self._process is process
                and self._identity is None
                and self._posix_authority_valid
                and self._process_group_id == pgid
                and pgid == process.pid
                and pgid != os.getpgrp()
            )
        if not cached:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INVALID,
                "cached provisional authority is unavailable",
            )
        if sys.platform.startswith("linux") and pidfd is None:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INDETERMINATE,
                "launch-time pidfd is unavailable",
            )

        first = self._probe_posix_child_anchor(process.pid, pidfd)
        if first.state is not _PosixAuthorityState.VALID:
            return self._finish_provisional_posix_authority(process, pgid, first)

        try:
            current_pgid = os.getpgid(process.pid)
        except ProcessLookupError:
            proof = _PosixAuthorityProof(
                _PosixAuthorityState.INVALID,
                "provisional leader disappeared",
            )
            return self._finish_provisional_posix_authority(process, pgid, proof)
        except PermissionError as exc:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INDETERMINATE,
                f"provisional PGID access was denied ({type(exc).__name__})",
            )
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                proof = _PosixAuthorityProof(
                    _PosixAuthorityState.INVALID,
                    "provisional leader disappeared",
                )
                return self._finish_provisional_posix_authority(process, pgid, proof)
            return _PosixAuthorityProof(
                _PosixAuthorityState.INDETERMINATE,
                f"provisional PGID inspection failed ({type(exc).__name__})",
            )
        if current_pgid != pgid:
            proof = _PosixAuthorityProof(
                _PosixAuthorityState.INVALID,
                "provisional leader process group changed",
            )
            return self._finish_provisional_posix_authority(process, pgid, proof)

        final = self._probe_posix_child_anchor(process.pid, pidfd)
        if final.state is not _PosixAuthorityState.VALID:
            return self._finish_provisional_posix_authority(process, pgid, final)
        with self._lock:
            if (
                self._process is not process
                or self._identity is not None
                or self._process_group_id != pgid
                or self._posix_pidfd != pidfd
            ):
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INDETERMINATE,
                    "provisional authority changed during validation",
                )
        return _PosixAuthorityProof(
            _PosixAuthorityState.VALID,
            "launch-time pidfd and process group match",
            pgid,
        )

    def _finish_provisional_posix_authority(
        self,
        process: subprocess.Popen[bytes],
        pgid: int,
        proof: _PosixAuthorityProof,
    ) -> _PosixAuthorityProof:
        if proof.state is not _PosixAuthorityState.INVALID:
            return proof
        retired = False
        with self._lock:
            if (
                self._process is process
                and self._identity is None
                and self._process_group_id == pgid
            ):
                self._process_group_id = None
                self._posix_authority_valid = False
                self._posix_cleanup_uncertain = True
                retired = True
        if retired:
            self._close_posix_pidfd()
        return proof

    def _probe_posix_leader_identity(
        self,
        identity: ProcessIdentity,
        pgid: int,
    ) -> _PosixAuthorityProof:
        def read_identity() -> tuple[float, str] | _PosixAuthorityProof:
            try:
                leader = self._psutil.Process(identity.pid)
                if leader.pid != identity.pid:
                    return _PosixAuthorityProof(
                        _PosixAuthorityState.INVALID,
                        "leader PID no longer matches",
                    )
                created = float(leader.create_time())
                status = str(leader.status())
            except (ProcessLookupError, self._psutil.NoSuchProcess):
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INVALID,
                    "leader process is missing",
                )
            except (PermissionError, self._psutil.AccessDenied) as exc:
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INDETERMINATE,
                    f"leader identity access was denied ({type(exc).__name__})",
                )
            except Exception as exc:
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INDETERMINATE,
                    f"leader identity inspection failed ({type(exc).__name__})",
                )

            # Some Linux exits transiently report STATUS_DEAD before becoming a
            # normal waitable zombie.  Status alone is not generation identity;
            # the non-reaping waitid child proof below decides whether this PID
            # still anchors our launch.
            return created, status

        first = read_identity()
        if isinstance(first, _PosixAuthorityProof):
            return first
        first_created, _ = first

        try:
            current_pgid = os.getpgid(identity.pid)
        except ProcessLookupError:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INVALID,
                "leader disappeared while checking its process group",
            )
        except PermissionError as exc:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INDETERMINATE,
                f"leader process-group access was denied ({type(exc).__name__})",
            )
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INVALID,
                    "leader disappeared while checking its process group",
                )
            return _PosixAuthorityProof(
                _PosixAuthorityState.INDETERMINATE,
                f"leader process-group inspection failed ({type(exc).__name__})",
            )
        if current_pgid != pgid:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INVALID,
                "leader process group changed",
            )

        # Re-read after getpgid so a PID replacement cannot satisfy one half of
        # the proof with the old generation and the other half with the new one.
        second = read_identity()
        if isinstance(second, _PosixAuthorityProof):
            return second
        second_created, _ = second
        if (
            abs(first_created - identity.create_time) >= 0.01
            or abs(second_created - identity.create_time) >= 0.01
        ):
            return _PosixAuthorityProof(
                _PosixAuthorityState.INVALID,
                "leader creation identity changed",
                pgid,
            )
        return _PosixAuthorityProof(
            _PosixAuthorityState.VALID,
            "leader creation identity and process group match",
            pgid,
        )

    def _probe_posix_child_anchor(
        self,
        pid: int,
        pidfd: int | None,
    ) -> _PosixAuthorityProof:
        waitid = getattr(os, "waitid", None)
        required = ("P_PID", "WEXITED", "WNOWAIT", "WNOHANG")
        if waitid is None or not all(hasattr(os, name) for name in required):
            return _PosixAuthorityProof(
                _PosixAuthorityState.INDETERMINATE,
                "non-reaping waitid child inspection is unavailable",
            )

        wait_pidfd: int | None = None
        if sys.platform.startswith("linux"):
            if pidfd is None:
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INDETERMINATE,
                    "launch-time pidfd generation is unavailable",
                )
            try:
                wait_pidfd = self._duplicate_linux_pidfd(pidfd)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    return _PosixAuthorityProof(
                        _PosixAuthorityState.INVALID,
                        "stable leader pidfd descriptor is invalid (EBADF)",
                    )
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INDETERMINATE,
                    f"pidfd duplication failed ({type(exc).__name__})",
                )
            if wait_pidfd is None:
                return _PosixAuthorityProof(
                    _PosixAuthorityState.INDETERMINATE,
                    "pidfd authority changed before child inspection",
                )

        try:
            while True:
                try:
                    idtype = getattr(os, "P_PIDFD", 3) if wait_pidfd is not None else os.P_PID
                    identifier = wait_pidfd if wait_pidfd is not None else pid
                    result = waitid(
                        idtype,
                        identifier,
                        os.WEXITED | os.WNOWAIT | os.WNOHANG,
                    )
                    break
                except InterruptedError:
                    continue
                except ChildProcessError:
                    return _PosixAuthorityProof(
                        _PosixAuthorityState.INVALID,
                        "leader is no longer our unreaped child (ECHILD)",
                    )
                except PermissionError as exc:
                    return _PosixAuthorityProof(
                        _PosixAuthorityState.INDETERMINATE,
                        f"child inspection access was denied ({type(exc).__name__})",
                    )
                except OSError as exc:
                    if exc.errno == errno.ECHILD:
                        return _PosixAuthorityProof(
                            _PosixAuthorityState.INVALID,
                            "stable leader generation is no longer waitable (ECHILD)",
                        )
                    if exc.errno == errno.EBADF:
                        return _PosixAuthorityProof(
                            _PosixAuthorityState.INVALID,
                            "stable leader pidfd descriptor is invalid (EBADF)",
                        )
                    return _PosixAuthorityProof(
                        _PosixAuthorityState.INDETERMINATE,
                        f"child inspection failed ({type(exc).__name__})",
                    )
        finally:
            if wait_pidfd is not None:
                os.close(wait_pidfd)

        observed_pid = int(getattr(result, "si_pid", 0)) if result is not None else 0
        if observed_pid == 0:
            return _PosixAuthorityProof(
                _PosixAuthorityState.VALID,
                "leader remains our live child",
            )
        if observed_pid != pid:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INVALID,
                "waitid returned a different child",
            )

        terminal_codes = {
            getattr(os, name)
            for name in ("CLD_EXITED", "CLD_KILLED", "CLD_DUMPED")
            if hasattr(os, name)
        }
        code = getattr(result, "si_code", None)
        if not terminal_codes or code not in terminal_codes:
            return _PosixAuthorityProof(
                _PosixAuthorityState.INDETERMINATE,
                f"waitid returned unsupported child state {code!r}",
            )
        return _PosixAuthorityProof(
            _PosixAuthorityState.VALID,
            "leader remains our unreaped zombie child",
        )

    def _finish_posix_authority_proof(
        self,
        identity: ProcessIdentity,
        pgid: int,
        proof: _PosixAuthorityProof,
    ) -> _PosixAuthorityProof:
        if proof.state is not _PosixAuthorityState.INVALID:
            return proof

        retired = False
        with self._lock:
            if self._identity == identity and self._process_group_id == pgid:
                self._process_group_id = None
                self._posix_authority_valid = False
                self._posix_cleanup_uncertain = True
                retired = True
        if retired:
            self._close_posix_pidfd()
        return proof

    def _live_posix_group_members(self, pgid: int) -> _PosixGroupInspection:
        members: list[int] = []
        denied = False
        try:
            processes = tuple(self._psutil.process_iter(["pid", "status"]))
        except (PermissionError, self._psutil.AccessDenied):
            return _PosixGroupInspection(
                _PosixAuthorityState.INDETERMINATE,
                (),
                "process enumeration access was denied",
            )
        for proc in processes:
            try:
                if os.getpgid(proc.pid) != pgid:
                    continue
                info = getattr(proc, "info", {}) or {}
                status = info.get("status") or proc.status()
                if status == getattr(self._psutil, "STATUS_ZOMBIE", "zombie"):
                    continue
                members.append(proc.pid)
            except (PermissionError, self._psutil.AccessDenied):
                denied = True
            except (ProcessLookupError, self._psutil.NoSuchProcess):
                continue
        unique_members = tuple(sorted(set(members)))
        if denied:
            return _PosixGroupInspection(
                _PosixAuthorityState.INDETERMINATE,
                unique_members,
                "at least one process could not be inspected",
            )
        return _PosixGroupInspection(
            _PosixAuthorityState.VALID,
            unique_members,
            "process-group membership inspection completed",
        )

    def _retire_posix_authority_and_reap(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
    ) -> None:
        with self._lock:
            if self._process is not process or self._identity != identity:
                return
            saved_pgid = self._process_group_id
            saved_authority = self._posix_authority_valid
            self._process_group_id = None
            self._posix_authority_valid = False
        try:
            returncode = self._consume_posix_exit(process, identity, 1.0)
        except _PosixPidfdInvalid:
            with self._lock:
                if self._process is process and self._identity == identity:
                    self._process_group_id = None
                    self._posix_authority_valid = False
                    self._posix_cleanup_uncertain = True
            self._close_posix_pidfd()
            raise
        except subprocess.TimeoutExpired:
            # The leader was unexpectedly still live.  It has not been reaped,
            # so restoring the same anchored PGID is safe for a later retry.
            with self._lock:
                if self._process is process and self._identity == identity:
                    self._process_group_id = saved_pgid
                    self._posix_authority_valid = saved_authority
            raise
        except Exception:
            with self._lock:
                if self._process is process and self._identity == identity:
                    self._process_group_id = saved_pgid
                    self._posix_authority_valid = saved_authority
            raise
        with self._lock:
            if self._process is process and self._identity == identity:
                self._returncode = returncode

    def _retire_reaped_posix_authority(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
        *,
        externally_reaped: bool = False,
        stable_pidfd: int | None = None,
    ) -> None:
        with self._lock:
            if self._process is not process or self._identity != identity:
                return
            self._process_group_id = None
            self._posix_authority_valid = False
            self._posix_cleanup_uncertain = True
            pidfd = self._posix_pidfd

        if pidfd is not None or stable_pidfd is not None:
            try:
                returncode = self._consume_posix_exit(
                    process,
                    identity,
                    0.0,
                    stable_pidfd=stable_pidfd,
                )
            except _PosixPidfdInvalid:
                self._close_posix_pidfd()
                returncode = self._returncode
                root_reaped = False
            except (RuntimeError, subprocess.TimeoutExpired):
                returncode = self._returncode
                root_reaped = False
            else:
                root_reaped = True
        elif externally_reaped:
            returncode = self._returncode
            if getattr(process, "returncode", None) is None:
                process.returncode = returncode if returncode is not None else 0
            root_reaped = True
        else:
            # The exact generation token was lost before its status could be
            # consumed.  Never fall back to Popen.wait() on the numeric PID.
            returncode = self._returncode
            root_reaped = False
        with self._lock:
            if self._process is process and self._identity == identity:
                if returncode is not None:
                    self._returncode = returncode
                self._posix_root_reaped = root_reaped

    def _stop_windows_tree(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
        grace_timeout: float,
        kill_timeout: float,
    ) -> tuple[bool, tuple[int, ...], list[str]]:
        errors: list[str] = []
        escalated = False

        if self._windows_job_assigned and self._windows_job is not None:
            known = self._validated_windows_tree(identity)
            try:
                process.terminate()
            except Exception as exc:
                errors.append(f"root terminate failed ({type(exc).__name__})")
            remaining = self._wait_identities(known, grace_timeout)
            if process.poll() is None or remaining:
                escalated = True
                try:
                    self._windows_job.terminate(1)
                except Exception as exc:
                    errors.append(f"TerminateJobObject failed ({type(exc).__name__}: {exc})")
            try:
                self._close_windows_job()
            except Exception as exc:
                errors.append(f"CloseHandle failed ({type(exc).__name__}: {exc})")
            remaining_identities = self._wait_for_windows_tree(identity, kill_timeout)
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=kill_timeout)
                    escalated = True
                except Exception as exc:
                    errors.append(f"root kill failed ({type(exc).__name__}: {exc})")
            return escalated, tuple(item.pid for item in remaining_identities), errors

        identities = self._validated_windows_tree(identity)
        self._terminate_identities(identities, kill=False)
        remaining = self._wait_for_windows_tree(identity, grace_timeout)
        if remaining:
            escalated = True
            self._terminate_identities(remaining, kill=True)
            remaining = self._wait_for_windows_tree(identity, kill_timeout)
        return escalated, tuple(item.pid for item in remaining), errors

    def _validated_windows_tree(self, identity: ProcessIdentity) -> tuple[ProcessIdentity, ...]:
        self._collect_windows_descendants(identity)
        with self._lock:
            descendants = tuple(self._known_descendants.values())

        identities = [item for item in descendants if self._identity_alive(item)]
        identities.sort(key=lambda item: item.create_time, reverse=True)
        if self._identity_alive(identity):
            identities.append(identity)
        return tuple(identities)

    def _collect_windows_descendants(self, identity: ProcessIdentity) -> None:
        try:
            root = self._psutil.Process(identity.pid)
            if not self._identity_matches(root, identity):
                return
            descendants = root.children(recursive=True)
        except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
            return

        found: dict[int, ProcessIdentity] = {}
        for proc in descendants:
            try:
                created = float(proc.create_time())
                if created + 1.0 < identity.create_time:
                    continue
                # ``children(recursive=True)`` establishes ancestry at collection
                # time.  Creation time rejects stale/reused process objects.
                found[proc.pid] = ProcessIdentity(proc.pid, created)
            except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                continue
        with self._lock:
            if self._identity == identity:
                self._known_descendants.update(found)

    def _kill_validated_descendants(self, identity: ProcessIdentity) -> None:
        identities = self._validated_windows_tree(identity)
        descendants = tuple(item for item in identities if item.pid != identity.pid)
        self._terminate_identities(descendants, kill=True)
        self._wait_identities(descendants, 3.0)

    def _terminate_identities(
        self,
        identities: Iterable[ProcessIdentity],
        *,
        kill: bool,
    ) -> None:
        for identity in identities:
            try:
                proc = self._psutil.Process(identity.pid)
                if not self._identity_matches(proc, identity):
                    continue
                if kill:
                    proc.kill()
                else:
                    proc.terminate()
            except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                continue

    def _wait_identities(
        self,
        identities: Iterable[ProcessIdentity],
        timeout: float,
    ) -> tuple[ProcessIdentity, ...]:
        pending = tuple(identities)
        deadline = time.monotonic() + timeout
        while pending and time.monotonic() < deadline:
            pending = tuple(item for item in pending if self._identity_alive(item))
            if pending:
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return tuple(item for item in pending if self._identity_alive(item))

    def _wait_for_windows_tree(
        self,
        identity: ProcessIdentity,
        timeout: float,
    ) -> tuple[ProcessIdentity, ...]:
        deadline = time.monotonic() + timeout
        remaining = self._validated_windows_tree(identity)
        while remaining and time.monotonic() < deadline:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            remaining = self._validated_windows_tree(identity)
        return remaining

    def _wait_for_owned_tree(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
        timeout: float,
    ) -> tuple[int, ...]:
        deadline = time.monotonic() + timeout
        saw_empty = False
        while True:
            remaining = self._remaining_owned_pids(identity)
            if not remaining:
                if saw_empty:
                    return ()
                saw_empty = True
                if time.monotonic() >= deadline:
                    continue
            else:
                saw_empty = False
            if time.monotonic() >= deadline:
                return remaining
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _wait_for_posix_pgid_empty(
        self,
        pgid: int,
        timeout: float,
    ) -> _PosixGroupInspection:
        deadline = time.monotonic() + timeout
        saw_empty = False
        while True:
            inspection = self._live_posix_group_members(pgid)
            if inspection.state is _PosixAuthorityState.INDETERMINATE:
                saw_empty = False
                if time.monotonic() >= deadline:
                    return inspection
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                continue
            remaining = inspection.members
            if not remaining:
                if saw_empty:
                    return inspection
                saw_empty = True
                if time.monotonic() >= deadline:
                    continue
            else:
                saw_empty = False
            if time.monotonic() >= deadline:
                return inspection
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _remaining_owned_pids(self, identity: ProcessIdentity) -> tuple[int, ...]:
        if os.name == "nt":
            return tuple(
                item.pid
                for item in self._validated_windows_tree(identity)
                if self._identity_alive(item)
            )
        proof = self._prove_posix_group_authority(identity)
        if proof.state is _PosixAuthorityState.VALID and proof.pgid is not None:
            inspection = self._live_posix_group_members(proof.pgid)
            if inspection.state is _PosixAuthorityState.VALID:
                return inspection.members
            return (identity.pid,)
        if proof.state is _PosixAuthorityState.INDETERMINATE:
            # This is a retryable diagnostic sentinel.  The anchor remains
            # unreaped and broad authority remains cached but cannot be used.
            return (identity.pid,)
        if self._posix_cleanup_uncertain:
            # Diagnostic sentinel only.  This PID is not signal authority and
            # may already be absent; it keeps cleanup visibly incomplete.
            return (identity.pid,)
        if self._identity_alive(identity):
            return (identity.pid,)
        return ()

    def _identity_alive(self, identity: ProcessIdentity) -> bool:
        try:
            proc = self._psutil.Process(identity.pid)
            if not self._identity_matches(proc, identity):
                return False
            return proc.status() != getattr(self._psutil, "STATUS_ZOMBIE", "zombie")
        except (self._psutil.NoSuchProcess, self._psutil.AccessDenied):
            return False

    @staticmethod
    def _identity_matches(process: Any, identity: ProcessIdentity) -> bool:
        try:
            return (
                process.pid == identity.pid
                and abs(float(process.create_time()) - identity.create_time) < 0.01
            )
        except Exception:
            return False

    def _close_windows_job(self) -> None:
        job = self._windows_job
        if job is not None:
            job.close()
            self._windows_job = None
            self._windows_job_assigned = False


__all__ = [
    "BoundedLogTail",
    "OwnedProcessController",
    "ProcessAlreadyRunning",
    "ProcessIdentity",
    "ProcessLifecycle",
    "ProcessSnapshot",
    "SecretRedactor",
    "StopResult",
]
