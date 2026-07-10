"""Owned ``llama-server`` process lifecycle primitives.

This module deliberately knows nothing about ComfyUI.  It owns only processes it
started itself and records enough identity information to avoid PID/name based
adoption or termination.
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class ProcessIdentity:
    """PID plus creation time, sufficient to reject ordinary PID reuse."""

    pid: int
    create_time: float


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

    def redact(self, value: str) -> str:
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "<redacted>")
        return self._credential_pattern.sub(r"\1\2<redacted>", text)

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
            return self._identity is not None

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
            if self._process is not None or self._identity is not None:
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
                launch_command = self._platform_launch_command(command)
                process = self._popen_factory(list(launch_command), **kwargs)
                # Keep a handle before identity capture so every post-spawn failure
                # can still terminate and reap the exact process we just created.
                self._process = process

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
                        process.kill()
                        self._returncode = process.wait(timeout=5)
                        self._posix_root_reaped = True
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

        with self._lock:
            process = self._process
            identity = self._identity
            if process is None and identity is None:
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
            if process is None or identity is None:
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
            self._stopping = True
            self._stop_requested = True
            self._state = ProcessLifecycle.STOPPING

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
            remaining = self._remaining_owned_pids(identity)

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
                # Identity capture did not complete, so only the exact Popen is
                # signalable.  Retire any provisional PGID before reaping it.
                self._process_group_id = None
                self._posix_authority_valid = False
                process.kill()
                self._returncode = process.wait(timeout=5)
                self._posix_root_reaped = True

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
            self._process = None
            self._identity = None
            self._process_group_id = None
            self._known_descendants.clear()
            self._descendant_fallback = False
            self._stopped_at = time.time()
        return complete, errors

    def _capture_identity(self, pid: int) -> ProcessIdentity:
        try:
            created = float(self._psutil.Process(pid).create_time())
        except Exception as exc:
            if os.name == "nt":
                raise RuntimeError("failed to capture Windows process identity") from exc
            # The child can exec and exit very quickly.  The launch time still lets
            # us reject older unrelated descendants in fallback scans.
            created = time.time()
        return ProcessIdentity(pid=pid, create_time=created)

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
        for channel, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            if stream is None:
                continue
            thread = threading.Thread(
                target=self._drain_stream,
                args=(channel, stream, redactor),
                name=f"llamacpp-{channel}-{process.pid}",
                daemon=True,
            )
            self._drain_threads.append(thread)
            thread.start()

    def _drain_stream(self, channel: str, stream: Any, redactor: SecretRedactor) -> None:
        pending = ""
        overlap = max(128, redactor.max_secret_length)
        try:
            while True:
                reader = getattr(stream, "read1", stream.read)
                chunk = reader(4096)
                if not chunk:
                    break
                if isinstance(chunk, bytes):
                    pending += chunk.decode("utf-8", errors="replace")
                else:
                    pending += str(chunk)

                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    self._log_tail.append(channel, line)
                if len(pending) > 32 * 1024 + overlap:
                    flush_at = len(pending) - overlap
                    self._log_tail.append(channel, pending[:flush_at])
                    pending = pending[flush_at:]
            if pending:
                self._log_tail.append(channel, pending)
        except Exception as exc:
            self._log_tail.append(channel, f"<log drain failed: {type(exc).__name__}>")
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
        thread = threading.Thread(
            target=self._monitor,
            args=(process, identity),
            name=f"llamacpp-monitor-{identity.pid}",
            daemon=True,
        )
        self._monitor_thread = thread
        thread.start()

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
                if self._identity != identity:
                    return
            self._collect_windows_descendants(identity)
            exited = process.poll() is not None
            if exited:
                return
            time.sleep(0.05)

    def _monitor(self, process: subprocess.Popen[bytes], identity: ProcessIdentity) -> None:
        if os.name != "nt":
            self._monitor_posix(process, identity)
            return

        try:
            returncode = process.wait()
        except Exception as exc:
            with self._lock:
                if self._identity == identity:
                    self._last_error = f"process monitor failed: {type(exc).__name__}"
            return

        with self._lock:
            if self._identity != identity:
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
                self._last_error += f"; descendant cleanup failed ({type(exc).__name__})"

    def _monitor_posix(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
    ) -> None:
        """Observe exit without reaping, clean the anchored group, then reap."""

        try:
            wait_result, already_reaped = self._wait_for_posix_exit(identity)
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
            if already_reaped or not self._posix_group_authority_matches(identity):
                # An external waiter or SIGCHLD policy has already destroyed the
                # group-generation anchor.  Retire the PGID and fail closed.  A
                # cached numeric PID/PGID is not authority to signal anything.
                self._retire_reaped_posix_authority(process, identity)
                remaining = (identity.pid,)
                errors.append(
                    "POSIX group authority was externally reaped; refusing descendant cleanup"
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

    def _wait_for_posix_exit(self, identity: ProcessIdentity) -> tuple[Any | None, bool]:
        """Observe child exit without consuming the zombie group leader."""

        waitid = getattr(os, "waitid", None)
        required = ("P_PID", "WEXITED", "WNOWAIT")
        if waitid is not None and all(hasattr(os, name) for name in required):
            while True:
                try:
                    result = waitid(
                        os.P_PID,
                        identity.pid,
                        os.WEXITED | os.WNOWAIT,
                    )
                    if result is None or int(getattr(result, "si_pid", -1)) != identity.pid:
                        raise RuntimeError("waitid returned an unexpected child identity")
                    return result, False
                except InterruptedError:
                    continue
                except ChildProcessError:
                    # A third-party SIGCHLD policy or waiter consumed it.  From
                    # this point onward the numeric PGID is not signal authority.
                    return None, True
                except OSError as exc:
                    if exc.errno == errno.ECHILD:
                        return None, True
                    if exc.errno in {errno.ENOSYS, errno.EINVAL}:
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

    def _posix_exit_observed_nowait(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
    ) -> bool:
        """Check root exit without reaping; fail closed if another waiter reaped it."""

        waitid = getattr(os, "waitid", None)
        required = ("P_PID", "WEXITED", "WNOWAIT", "WNOHANG")
        if waitid is not None and all(hasattr(os, name) for name in required):
            while True:
                try:
                    result = waitid(
                        os.P_PID,
                        identity.pid,
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
                    self._retire_reaped_posix_authority(process, identity)
                    return False
                except OSError as exc:
                    if exc.errno == errno.ECHILD:
                        self._retire_reaped_posix_authority(process, identity)
                        return False
                    if exc.errno in {errno.ENOSYS, errno.EINVAL}:
                        break
                    raise

        try:
            root = self._psutil.Process(identity.pid)
            if not self._identity_matches(root, identity):
                self._retire_reaped_posix_authority(process, identity)
                return False
            return root.status() == getattr(self._psutil, "STATUS_ZOMBIE", "zombie")
        except self._psutil.AccessDenied:
            return False
        except (ProcessLookupError, self._psutil.NoSuchProcess):
            self._retire_reaped_posix_authority(process, identity)
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
            if not self._posix_group_authority_matches(identity):
                if (
                    self._posix_root_reaped
                    and self._process_group_id is None
                    and not self._posix_cleanup_uncertain
                ):
                    return False, (), errors
                errors.append(
                    "POSIX group authority is unavailable; refusing to signal a numeric PGID"
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
                    if self._posix_group_authority_matches(identity):
                        self._signal_posix_group(signal.SIGKILL, identity)
                    else:
                        raise RuntimeError("POSIX group authority expired before escalation")
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
        pgid = self._process_group_id
        if pgid is None or pgid == os.getpgrp():
            raise RuntimeError("refusing to signal an unowned process group")
        if not self._posix_group_authority_matches(identity):
            raise RuntimeError("refusing to signal a process group after leader authority expired")
        # The original setsid leader is still our live or unreaped-zombie child.
        # POSIX therefore prevents this numeric PGID from being recycled.
        os.killpg(pgid, sig)

    def _posix_group_authority_matches(self, identity: ProcessIdentity) -> bool:
        with self._lock:
            pgid = self._process_group_id
            return bool(
                self._posix_authority_valid
                and pgid is not None
                and pgid != os.getpgrp()
                and self._identity == identity
            )

    def _owned_posix_group_members(self, identity: ProcessIdentity) -> tuple[int, ...]:
        if not self._posix_group_authority_matches(identity):
            return ()
        with self._lock:
            pgid = self._process_group_id
        if pgid is None:
            return ()
        members: list[int] = []
        for proc in self._psutil.process_iter(["pid", "status"]):
            try:
                info = getattr(proc, "info", {}) or {}
                status = info.get("status") or proc.status()
                if status == getattr(self._psutil, "STATUS_ZOMBIE", "zombie"):
                    continue
                if os.getpgid(proc.pid) == pgid:
                    members.append(proc.pid)
            except (ProcessLookupError, self._psutil.NoSuchProcess, self._psutil.AccessDenied):
                continue
        return tuple(sorted(set(members)))

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
            returncode = process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            # The leader was unexpectedly still live.  It has not been reaped,
            # so restoring the same anchored PGID is safe for a later retry.
            with self._lock:
                if self._process is process and self._identity == identity:
                    self._process_group_id = saved_pgid
                    self._posix_authority_valid = saved_authority
            raise
        with self._lock:
            if self._process is process and self._identity == identity:
                self._returncode = returncode
                self._posix_root_reaped = True

    def _retire_reaped_posix_authority(
        self,
        process: subprocess.Popen[bytes],
        identity: ProcessIdentity,
    ) -> None:
        with self._lock:
            if self._process is not process or self._identity != identity:
                return
            self._process_group_id = None
            self._posix_authority_valid = False
            self._posix_cleanup_uncertain = True
        try:
            returncode = process.wait(timeout=0)
        except ChildProcessError:
            returncode = process.returncode
            root_reaped = True
        except subprocess.TimeoutExpired:
            returncode = process.returncode
            root_reaped = False
        else:
            root_reaped = True
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
            else:
                saw_empty = False
            if time.monotonic() >= deadline:
                return remaining
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _remaining_owned_pids(self, identity: ProcessIdentity) -> tuple[int, ...]:
        if os.name == "nt":
            return tuple(
                item.pid
                for item in self._validated_windows_tree(identity)
                if self._identity_alive(item)
            )
        if self._posix_group_authority_matches(identity):
            return self._owned_posix_group_members(identity)
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
