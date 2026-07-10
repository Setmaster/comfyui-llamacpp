"""Linux owner-death supervisor for one uniquely owned process group.

This is an internal executable helper, not a public import surface.  The
controller launches it as the process-group leader.  It remains alive while the
real server runs, inherits the server's output, and uses PR_SET_PDEATHSIG to
kill only its own group if the owning Comfy process disappears abruptly.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from collections.abc import Sequence

_PR_SET_PDEATHSIG = 1
_OWNER_DEATH_SIGNAL = signal.SIGUSR1


def _kill_owned_group(_signum: int, _frame: object) -> None:
    signal.signal(_OWNER_DEATH_SIGNAL, signal.SIG_IGN)
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    finally:
        os._exit(128 + int(_OWNER_DEATH_SIGNAL))


def _arm_parent_death_signal(expected_parent: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(_PR_SET_PDEATHSIG, int(_OWNER_DEATH_SIGNAL), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    # The owner can exit between our spawn and prctl call.  The kernel cannot
    # deliver a signal retroactively, so close that race explicitly.
    if os.getppid() != expected_parent:
        _kill_owned_group(int(_OWNER_DEATH_SIGNAL), None)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < 3 or arguments[1] != "--":
        raise SystemExit("usage: _posix_supervisor.py OWNER_PID -- COMMAND [ARG ...]")
    expected_parent = int(arguments[0])
    command = arguments[2:]

    signal.signal(_OWNER_DEATH_SIGNAL, _kill_owned_group)
    _arm_parent_death_signal(expected_parent)
    child = subprocess.Popen(command, close_fds=True)
    try:
        return child.wait()
    finally:
        if child.poll() is None:
            try:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
