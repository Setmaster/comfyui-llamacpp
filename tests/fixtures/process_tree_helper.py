"""Subprocess fixture for owned process-tree tests."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid-file", required=True)
    parser.add_argument("--ignore-term", action="store_true")
    parser.add_argument("--noisy-lines", type=int, default=0)
    parser.add_argument("--noisy-delay", type=float, default=0.0)
    parser.add_argument("--secret", default="")
    args = parser.parse_args()

    child_program = "\n".join(
        [
            "import signal, time",
            f"ignore = {args.ignore_term!r}",
            "if ignore and hasattr(signal, 'SIGTERM'):",
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)",
            "while True:",
            "    time.sleep(0.1)",
        ]
    )
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", child_program],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if args.ignore_term and hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    Path(args.pid_file).write_text(
        json.dumps({"root": os.getpid(), "child": child.pid}),
        encoding="utf-8",
    )

    for index in range(args.noisy_lines):
        print(f"stdout line {index} {args.secret}", flush=True)
        print(f"stderr line {index} token={args.secret}", file=sys.stderr, flush=True)
        if args.noisy_delay:
            time.sleep(args.noisy_delay)

    while True:
        time.sleep(0.1)


if __name__ == "__main__":
    raise SystemExit(main())
