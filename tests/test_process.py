"""Behavior tests for positively owned subprocess trees."""

from __future__ import annotations

import errno
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from runtime.process import (
    BoundedLogTail,
    OwnedProcessController,
    ProcessAlreadyRunning,
    ProcessIdentity,
    ProcessLifecycle,
    SecretRedactor,
    _PosixAuthorityProof,
    _PosixAuthorityState,
)

HELPER = Path(__file__).parent / "fixtures" / "process_tree_helper.py"
SPLIT_SECRET = "SENSITIVE_TOKEN_1234567890"
COMBINED_SPLIT_SECRET = "VERY_LONG_PRIVATE_PREFIX!token=SENSITIVE_SUFFIX"


def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


def _read_pid_file(path: Path) -> dict[str, int]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pid_is_live(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _attach_test_pidfd(
    controller: OwnedProcessController,
    monkeypatch: pytest.MonkeyPatch,
) -> int | None:
    """Attach a harmless descriptor for deterministic mocked pidfd tests."""

    if not sys.platform.startswith("linux"):
        return None
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd
    monkeypatch.setattr(controller, "_signal_linux_pidfd_group", lambda _fd, _sig: False)
    return pidfd


def _close_test_fd(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


class _OneChunkStream:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.closed = False

    def read1(self, _size: int) -> bytes:
        payload, self._payload = self._payload, b""
        return payload

    def read(self, size: int) -> bytes:
        return self.read1(size)

    def close(self) -> None:
        self.closed = True


class _ChunkStream:
    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = list(chunks) + [b""]
        self.closed = False

    def read1(self, _size: int) -> bytes:
        return self._chunks.pop(0)

    def read(self, size: int) -> bytes:
        return self.read1(size)

    def close(self) -> None:
        self.closed = True


def _reconstruct_log_payload(entries: tuple[str, ...]) -> str:
    return "".join(entry.partition("] ")[2] for entry in entries)


def _drain_test_chunks(
    chunks: Sequence[bytes],
    *,
    secrets: Sequence[str] = (),
    max_bytes: int = 64 * 1024,
) -> tuple[str, tuple[str, ...], _ChunkStream]:
    stream = _ChunkStream(chunks)
    redactor = SecretRedactor(secrets)
    tail = BoundedLogTail(max_lines=200, max_bytes=max_bytes, redactor=redactor)
    controller = OwnedProcessController()
    controller._drain_stream("stdout", stream, redactor, tail)
    entries = tail.snapshot()
    return _reconstruct_log_payload(entries), entries, stream


def _start_tree(
    controller: OwnedProcessController,
    pid_file: Path,
    *extra: str,
    secret_values: tuple[str, ...] = (),
) -> dict[str, int]:
    controller.start(
        [sys.executable, "-u", str(HELPER), "--pid-file", str(pid_file), *extra],
        secret_values=secret_values,
    )
    _wait_until(pid_file.exists)
    return _read_pid_file(pid_file)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertion")
def test_start_owns_unique_process_group_and_stops_entire_tree(tmp_path: Path) -> None:
    controller = OwnedProcessController()
    pids = _start_tree(controller, tmp_path / "tree.json")

    try:
        snapshot = controller.snapshot()
        assert snapshot.state == ProcessLifecycle.RUNNING
        assert snapshot.pid is not None
        assert snapshot.process_group_id == snapshot.pid
        assert os.getpgid(pids["root"]) == snapshot.pid
        assert os.getpgid(pids["child"]) == snapshot.pid
        pidfd = controller._posix_pidfd
        if sys.platform.startswith("linux"):
            assert pidfd is not None
            os.fstat(pidfd)

        result = controller.stop(grace_timeout=2.0, kill_timeout=2.0)

        assert result.complete is True
        assert result.remaining_pids == ()
        assert controller.state == ProcessLifecycle.STOPPED
        assert controller.has_owned_process is False
        if pidfd is not None:
            with pytest.raises(OSError):
                os.fstat(pidfd)
        _wait_until(lambda: not _pid_is_live(pids["root"]))
        _wait_until(lambda: not _pid_is_live(pids["child"]))
    finally:
        controller.stop(grace_timeout=0.1, kill_timeout=1.0)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux PDEATHSIG assertion")
def test_abrupt_owner_exit_kills_the_supervised_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "abrupt-tree.json"
    repo_root = Path(__file__).resolve().parents[1]
    script = "\n".join(
        (
            "import os, sys, time",
            "from runtime.process import OwnedProcessController",
            f"pid_file = {str(pid_file)!r}",
            "controller = OwnedProcessController()",
            "controller.start([",
            f"    sys.executable, '-u', {str(HELPER)!r}, '--pid-file', pid_file",
            "])",
            "deadline = time.monotonic() + 5",
            "while not os.path.exists(pid_file) and time.monotonic() < deadline:",
            "    time.sleep(0.01)",
            "if not os.path.exists(pid_file):",
            "    raise SystemExit('target did not start')",
            "print(controller.snapshot().pid, flush=True)",
            "os._exit(0)",
        )
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repo_root)
    owner = subprocess.Popen(
        [sys.executable, "-u", "-c", script],
        cwd=repo_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    supervisor_pid: int | None = None
    pids: dict[str, int] = {}
    try:
        stdout, stderr = owner.communicate(timeout=10)
        assert owner.returncode == 0, stderr
        supervisor_pid = int(stdout.strip())
        pids = _read_pid_file(pid_file)

        _wait_until(lambda: not _pid_is_live(supervisor_pid), timeout=5)
        _wait_until(lambda: not _pid_is_live(pids["root"]), timeout=5)
        _wait_until(lambda: not _pid_is_live(pids["child"]), timeout=5)
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        for pid in (*pids.values(), supervisor_pid):
            if pid is None or not _pid_is_live(pid):
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal escalation assertion")
def test_stop_escalates_when_owned_tree_ignores_sigterm(tmp_path: Path) -> None:
    controller = OwnedProcessController()
    pids = _start_tree(controller, tmp_path / "tree.json", "--ignore-term")

    try:
        result = controller.stop(grace_timeout=0.15, kill_timeout=2.0)

        assert result.complete is True
        assert result.escalated is True
        _wait_until(lambda: not _pid_is_live(pids["root"]))
        _wait_until(lambda: not _pid_is_live(pids["child"]))
    finally:
        controller.stop(grace_timeout=0.1, kill_timeout=1.0)


def test_stdout_and_stderr_are_continuously_drained_bounded_and_redacted(
    tmp_path: Path,
) -> None:
    secret = "fixture-secret-5a8c63"
    controller = OwnedProcessController(max_log_lines=30, max_log_bytes=4_096)
    _start_tree(
        controller,
        tmp_path / "tree.json",
        "--noisy-lines",
        "600",
        "--noisy-delay",
        "0.0005",
        "--secret",
        secret,
        secret_values=(secret,),
    )

    try:

        def bounded_tail_has_both_streams() -> bool:
            tail = controller.snapshot().log_tail
            return (
                len(tail) == 30
                and any(line.startswith("[stdout]") for line in tail)
                and any(line.startswith("[stderr]") for line in tail)
            )

        _wait_until(bounded_tail_has_both_streams)
        tail = controller.snapshot().log_tail

        assert len(tail) <= 30
        assert sum(len(line.encode("utf-8")) for line in tail) <= 4_096
        assert any(line.startswith("[stdout]") for line in tail)
        assert any(line.startswith("[stderr]") for line in tail)
        assert secret not in "\n".join(tail)
        assert "<redacted>" in "\n".join(tail)
    finally:
        assert controller.stop(grace_timeout=2.0, kill_timeout=2.0).complete


def test_old_log_drain_cannot_write_secret_into_new_generation_tail() -> None:
    old_secret = "OLD_TOP_SECRET_7f0d"
    read_started = threading.Event()
    release_read = threading.Event()

    class DelayedStream:
        reads = 0
        closed = False

        def read1(self, _size):
            self.reads += 1
            if self.reads == 1:
                read_started.set()
                assert release_read.wait(timeout=2.0)
                return f"{old_secret}\n".encode()
            return b""

        def read(self, size):
            return self.read1(size)

        def close(self):
            self.closed = True

    stream = DelayedStream()
    process = SimpleNamespace(pid=4242, stdout=stream, stderr=None)
    controller = OwnedProcessController()
    old_redactor = SecretRedactor((old_secret,))
    controller._reset_for_launch(old_redactor)
    old_tail = controller._log_tail
    controller._start_log_drains(process, old_redactor)  # type: ignore[arg-type]
    old_threads = tuple(controller._drain_threads)
    assert read_started.wait(timeout=2.0)

    controller._reset_for_launch(SecretRedactor(("NEW_TOP_SECRET",)))
    controller._log_tail.append("stdout", "new-generation-marker")
    release_read.set()
    for thread in old_threads:
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    old_text = "\n".join(old_tail.snapshot())
    new_text = "\n".join(controller.snapshot().log_tail)
    assert old_secret not in old_text
    assert "<redacted>" in old_text
    assert new_text == "[stdout] new-generation-marker"
    assert old_secret not in new_text
    assert stream.closed is True


@pytest.mark.parametrize("split_offset", range(1, len(SPLIT_SECRET)))
def test_configured_secret_is_never_split_across_long_line_flush(
    split_offset: int,
) -> None:
    payload_size = 36 * 1024
    overlap = 128
    proposed_flush = payload_size - overlap
    secret_start = proposed_flush - split_offset
    payload = (
        "X" * secret_start + SPLIT_SECRET + "Y" * (payload_size - secret_start - len(SPLIT_SECRET))
    )
    stream = _OneChunkStream(payload.encode())
    redactor = SecretRedactor((SPLIT_SECRET,))
    tail = BoundedLogTail(max_lines=100, max_bytes=64 * 1024, redactor=redactor)
    controller = OwnedProcessController()

    controller._drain_stream("stdout", stream, redactor, tail)

    entries = tail.snapshot()
    reconstructed = _reconstruct_log_payload(entries)
    assert SPLIT_SECRET not in reconstructed
    assert SPLIT_SECRET[:split_offset] not in reconstructed
    assert SPLIT_SECRET[split_offset:] not in reconstructed
    assert "<redacted>" in reconstructed
    assert sum(len(entry.encode()) for entry in entries) <= 64 * 1024
    assert stream.closed is True


@pytest.mark.parametrize("split_offset", range(1, len(COMBINED_SPLIT_SECRET)))
def test_exact_secret_wins_over_credential_pattern_at_every_flush_offset(
    split_offset: int,
) -> None:
    payload_size = 36 * 1024
    overlap = max(128, len(COMBINED_SPLIT_SECRET))
    proposed_flush = payload_size - overlap
    secret_start = proposed_flush - split_offset
    payload = (
        "~" * secret_start
        + COMBINED_SPLIT_SECRET
        + "^" * (payload_size - secret_start - len(COMBINED_SPLIT_SECRET))
    )
    stream = _OneChunkStream(payload.encode())
    redactor = SecretRedactor((COMBINED_SPLIT_SECRET,))
    tail = BoundedLogTail(max_lines=100, max_bytes=64 * 1024, redactor=redactor)
    controller = OwnedProcessController()

    controller._drain_stream("stdout", stream, redactor, tail)

    entries = tail.snapshot()
    reconstructed = _reconstruct_log_payload(entries)
    assert COMBINED_SPLIT_SECRET not in reconstructed
    assert "VERY_LONG_PRIVATE_PREFIX!" not in reconstructed
    assert COMBINED_SPLIT_SECRET[:split_offset] not in reconstructed
    assert COMBINED_SPLIT_SECRET[split_offset:] not in reconstructed
    assert "<redacted>" in reconstructed
    assert sum(len(entry.encode()) for entry in entries) <= 64 * 1024
    assert stream.closed is True


def test_long_credential_value_is_stream_redacted_without_unbounded_buffer() -> None:
    class CredentialStream:
        def __init__(self) -> None:
            self._chunks = [
                b"prefix token=" + b"Z" * (36 * 1024),
                b"Z" * (128 * 1024) + b"; safe-suffix",
                b"",
            ]
            self.closed = False

        def read1(self, _size):
            return self._chunks.pop(0)

        def read(self, size):
            return self.read1(size)

        def close(self):
            self.closed = True

    stream = CredentialStream()
    redactor = SecretRedactor()
    tail = BoundedLogTail(max_lines=20, max_bytes=4096, redactor=redactor)
    controller = OwnedProcessController()

    controller._drain_stream("stdout", stream, redactor, tail)

    entries = tail.snapshot()
    reconstructed = _reconstruct_log_payload(entries)
    assert "token=<redacted>" in reconstructed
    assert "Z" not in reconstructed
    assert reconstructed.endswith("; safe-suffix")
    assert sum(len(entry.encode()) for entry in entries) <= 4096
    assert stream.closed is True


def test_ordinary_newline_free_output_remains_bounded() -> None:
    stream = _OneChunkStream(b"A" * (256 * 1024))
    redactor = SecretRedactor()
    tail = BoundedLogTail(max_lines=10, max_bytes=4096, redactor=redactor)
    controller = OwnedProcessController()

    controller._drain_stream("stdout", stream, redactor, tail)

    entries = tail.snapshot()
    assert entries
    assert sum(len(entry.encode()) for entry in entries) <= 4096
    assert set(_reconstruct_log_payload(entries)) == {"A"}
    assert stream.closed is True


@pytest.mark.parametrize(
    ("payload", "raw_values"),
    (
        ("token=\nSECRET\nSAFE\n", ("SECRET",)),
        ("Authorization:\nBearerValue\nSAFE\n", ("BearerValue",)),
        ("api key \nVALUE\nSAFE\n", ("VALUE",)),
        ("Authorization: Bearer TOPSECRET\nSAFE\n", ("Bearer", "TOPSECRET")),
        ("authorization = Basic dXNabc123\r\nSAFE\n", ("Basic", "dXNabc123")),
        ("api-key:APISECRET;SAFE\n", ("APISECRET",)),
        ("api_key\tUNDERSECRET SAFE\n", ("UNDERSECRET",)),
        ("bearer BEARSECRET SAFE\n", ("BEARSECRET",)),
        ("password=\r\n\r\nPASSSECRET;SAFE\n", ("PASSSECRET",)),
    ),
)
def test_streaming_credential_matrix_survives_every_character_chunk_boundary(
    payload: str,
    raw_values: tuple[str, ...],
) -> None:
    chunks = [character.encode() for character in payload]

    reconstructed, entries, stream = _drain_test_chunks(chunks, max_bytes=4096)

    for raw_value in raw_values:
        assert raw_value not in reconstructed
    assert "<redacted>" in reconstructed
    assert "SAFE" in reconstructed
    assert sum(len(entry.encode()) for entry in entries) <= 4096
    assert stream.closed is True


@pytest.mark.parametrize(
    ("payload", "sensitive_fields"),
    (
        (
            'Authorization: Digest username="Mufasa", realm="test", nonce="abc", '
            'uri="/dir/index.html", response="deadbeef", opaque="xyz"\r\nSAFE\n',
            ("Digest", "username", "Mufasa", "realm", "response", "deadbeef", "opaque"),
        ),
        (
            "Authorization: AWS4-HMAC-SHA256 "
            "Credential=AKIAEXAMPLE/20260710/us-east-1/s3/aws4_request, "
            "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
            "Signature=0123456789abcdef\nSAFE\n",
            (
                "AWS4-HMAC-SHA256",
                "AKIAEXAMPLE",
                "Credential=",
                "SignedHeaders=",
                "x-amz-date",
                "Signature=",
                "0123456789abcdef",
            ),
        ),
    ),
)
def test_authorization_suppresses_digest_and_aws_fields_through_line_end(
    payload: str,
    sensitive_fields: tuple[str, ...],
) -> None:
    direct = SecretRedactor().redact(payload)
    reconstructed, entries, stream = _drain_test_chunks(
        [character.encode() for character in payload],
        max_bytes=4096,
    )

    for sensitive in sensitive_fields:
        assert sensitive not in direct
        assert sensitive not in reconstructed
    assert "Authorization:" in direct
    assert "Authorization:" in reconstructed
    assert "<redacted>" in direct
    assert "<redacted>" in reconstructed
    assert direct.endswith("\nSAFE\n")
    assert reconstructed.endswith("SAFE")
    assert sum(len(entry.encode()) for entry in entries) <= 4096
    assert stream.closed is True


def test_streaming_credential_state_bounds_arbitrary_whitespace_before_value() -> None:
    chunks = (
        b"token",
        b"=",
        (b" \t" * (20 * 1024)),
        (b"\r\n\t" * (20 * 1024)),
        b"SECRET;SAFE\n",
    )

    reconstructed, entries, stream = _drain_test_chunks(chunks, max_bytes=4096)

    assert "SECRET" not in reconstructed
    assert "<redacted>" in reconstructed
    assert reconstructed.endswith(";SAFE")
    assert sum(len(entry.encode()) for entry in entries) <= 4096
    assert stream.closed is True


@pytest.mark.parametrize(
    "secret",
    (
        "alpha\nbeta",
        "first\r\n\r\nsecond",
        "PRIVATE_PREFIX!token=\nSENSITIVE_SUFFIX",
        "AUTH_PREFIX!Authorization:\r\nBearer EXACT_VALUE",
    ),
)
def test_configured_multiline_literal_is_redacted_before_line_emission(secret: str) -> None:
    payload = f"BEFORE|{secret}|AFTER\n"
    chunks = [character.encode() for character in payload]

    reconstructed, entries, stream = _drain_test_chunks(
        chunks,
        secrets=(secret,),
        max_bytes=4096,
    )

    assert secret not in reconstructed
    for fragment in (part for part in re.split(r"[\r\n]+", secret) if part):
        assert fragment not in reconstructed
    assert "<redacted>" in reconstructed
    assert "BEFORE|" in reconstructed
    assert "|AFTER" in reconstructed
    assert sum(len(entry.encode()) for entry in entries) <= 4096
    assert stream.closed is True


def test_streaming_redaction_deterministic_chunk_boundary_fuzz() -> None:
    rng = random.Random(0x5AFE_C0DE)
    credential_cases = (
        ("token=\nSECRET\nSAFE\n", ("SECRET",)),
        ("Authorization: Bearer TOPSECRET\r\nSAFE\n", ("Bearer", "TOPSECRET")),
        ("authorization = Basic dXNabc123\nSAFE\n", ("Basic", "dXNabc123")),
        ("api-key\t\r\nVALUE;SAFE\n", ("VALUE",)),
        ("password=\n\nPASSSECRET SAFE\n", ("PASSSECRET",)),
    )

    def random_chunks(payload: bytes, maximum: int = 17) -> list[bytes]:
        chunks: list[bytes] = []
        offset = 0
        while offset < len(payload):
            width = rng.randint(1, maximum)
            chunks.append(payload[offset : offset + width])
            offset += width
        return chunks

    for payload, raw_values in credential_cases:
        encoded = payload.encode()
        for _ in range(25):
            reconstructed, entries, _stream = _drain_test_chunks(
                random_chunks(encoded),
                max_bytes=4096,
            )
            for raw_value in raw_values:
                assert raw_value not in reconstructed
            assert "<redacted>" in reconstructed
            assert "SAFE" in reconstructed
            assert sum(len(entry.encode()) for entry in entries) <= 4096

    literal = "FUZZ_PREFIX\r\n\r\ntoken=FUZZ_SECRET"
    encoded_literal = f"BEFORE|{literal}|AFTER\n".encode()
    for _ in range(50):
        reconstructed, entries, _stream = _drain_test_chunks(
            random_chunks(encoded_literal),
            secrets=(literal,),
            max_bytes=4096,
        )
        assert literal not in reconstructed
        assert "FUZZ_PREFIX" not in reconstructed
        assert "FUZZ_SECRET" not in reconstructed
        assert "<redacted>" in reconstructed
        assert sum(len(entry.encode()) for entry in entries) <= 4096

    whitespace_payload = b"token=" + (b" \t\r\n" * (10 * 1024)) + b"SECRET;SAFE\n"
    reconstructed, entries, _stream = _drain_test_chunks(
        random_chunks(whitespace_payload, maximum=8192),
        max_bytes=4096,
    )
    assert "SECRET" not in reconstructed
    assert "<redacted>" in reconstructed
    assert reconstructed.endswith(";SAFE")
    assert sum(len(entry.encode()) for entry in entries) <= 4096


@pytest.mark.parametrize("symbol", ("é", "€", "😀"))
def test_multibyte_configured_literal_is_redacted_at_every_byte_split(symbol: str) -> None:
    secret = f"TOP{symbol}SECRET123"
    payload = f"BEFORE|{secret}|AFTER\n".encode()

    chunkings = ([payload[:split], payload[split:]] for split in range(1, len(payload)))
    for chunks in chunkings:
        reconstructed, entries, stream = _drain_test_chunks(
            chunks,
            secrets=(secret,),
            max_bytes=4096,
        )
        assert secret not in reconstructed
        assert "TOP" not in reconstructed
        assert "SECRET123" not in reconstructed
        assert symbol not in reconstructed
        assert "<redacted>" in reconstructed
        assert "BEFORE|" in reconstructed
        assert "|AFTER" in reconstructed
        assert sum(len(entry.encode()) for entry in entries) <= 4096
        assert stream.closed is True

    reconstructed, _entries, _stream = _drain_test_chunks(
        [bytes((byte,)) for byte in payload],
        secrets=(secret,),
        max_bytes=4096,
    )
    assert secret not in reconstructed
    assert "TOP" not in reconstructed
    assert "SECRET123" not in reconstructed
    assert "<redacted>" in reconstructed


@pytest.mark.parametrize("credential", ("秘密値", "clé€", "token😀value"))
def test_multibyte_credential_value_is_redacted_at_every_byte_split(
    credential: str,
) -> None:
    payload = f"token={credential};SAFE\n".encode()

    for split in range(1, len(payload)):
        reconstructed, entries, stream = _drain_test_chunks(
            [payload[:split], payload[split:]],
            max_bytes=4096,
        )
        assert credential not in reconstructed
        assert "<redacted>" in reconstructed
        assert reconstructed.endswith(";SAFE")
        assert sum(len(entry.encode()) for entry in entries) <= 4096
        assert stream.closed is True


def test_invalid_utf8_is_replaced_deterministically_without_credential_leak() -> None:
    payload = b"prefix:\xff\xfe token=" + b"\xf0\x28\x8c\x28" + b";SAFE\n"

    reconstructed, entries, stream = _drain_test_chunks(
        [bytes((byte,)) for byte in payload],
        max_bytes=4096,
    )

    assert "prefix:\ufffd\ufffd" in reconstructed
    assert "token=<redacted>" in reconstructed
    assert "\ufffd(" not in reconstructed.partition("token=<redacted>")[2]
    assert reconstructed.endswith(";SAFE")
    assert sum(len(entry.encode()) for entry in entries) <= 4096
    assert stream.closed is True


def test_text_chunks_finalize_and_reset_byte_decoder_deterministically() -> None:
    class MixedStream:
        def __init__(self) -> None:
            self._chunks = [b"prefix:\xc3", "é token=", "SECRET", "\nSAFE\n", ""]
            self.closed = False

        def read1(self, _size):
            return self._chunks.pop(0)

        def read(self, size):
            return self.read1(size)

        def close(self):
            self.closed = True

    stream = MixedStream()
    redactor = SecretRedactor()
    tail = BoundedLogTail(max_lines=20, max_bytes=4096, redactor=redactor)
    controller = OwnedProcessController()

    controller._drain_stream("stdout", stream, redactor, tail)

    reconstructed = _reconstruct_log_payload(tail.snapshot())
    assert "prefix:\ufffdé" in reconstructed
    assert "SECRET" not in reconstructed
    assert "token=<redacted>" in reconstructed
    assert reconstructed.endswith("SAFE")
    assert stream.closed is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX unrelated-process ownership assertion")
def test_stop_never_terminates_an_unrelated_same_command_process(tmp_path: Path) -> None:
    unrelated = subprocess.Popen(
        [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    controller = OwnedProcessController()
    _start_tree(controller, tmp_path / "tree.json")

    try:
        assert controller.stop(grace_timeout=2.0, kill_timeout=2.0).complete
        assert unrelated.poll() is None
    finally:
        controller.stop(grace_timeout=0.1, kill_timeout=1.0)
        if unrelated.poll() is None:
            unrelated.terminate()
            unrelated.wait(timeout=5)


def test_unexpected_exit_retains_diagnostics_until_explicit_cleanup() -> None:
    controller = OwnedProcessController()
    controller.start(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys, time; print('started'); time.sleep(0.15); sys.exit(7)",
        ]
    )

    _wait_until(lambda: controller.state == ProcessLifecycle.RUNTIME_FAILED)
    snapshot = controller.snapshot()

    assert snapshot.returncode == 7
    assert snapshot.last_error is not None
    assert "code 7" in snapshot.last_error
    assert controller.has_owned_process is True
    with pytest.raises(ProcessAlreadyRunning):
        controller.start([sys.executable, "-c", "pass"])

    result = controller.stop(grace_timeout=0.1, kill_timeout=1.0)
    assert result.complete is True
    assert controller.state == ProcessLifecycle.STOPPED


@pytest.mark.skipif(os.name == "nt", reason="POSIX held-leader cleanup assertion")
def test_unexpected_group_leader_exit_cleans_surviving_owned_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = OwnedProcessController()
    pids = _start_tree(controller, tmp_path / "unexpected-tree.json")
    leader_pid = controller.snapshot().pid
    assert leader_pid is not None
    real_killpg = os.killpg
    signal_observations = []

    def observe_killpg(pgid: int, sig: int) -> None:
        signal_observations.append(
            (
                pgid,
                sig,
                psutil.Process(leader_pid).status(),
                controller._process.returncode,  # type: ignore[union-attr]
            )
        )
        real_killpg(pgid, sig)

    # This test inspects the checked pre-6.9 fallback's zombie anchor.
    # Atomic pidfd group signaling is covered independently below.
    monkeypatch.setattr(controller, "_signal_linux_pidfd_group", lambda _fd, _sig: False)
    monkeypatch.setattr(os, "killpg", observe_killpg)

    try:
        os.kill(leader_pid, signal.SIGKILL)

        _wait_until(lambda: controller.state == ProcessLifecycle.RUNTIME_FAILED)
        _wait_until(lambda: not _pid_is_live(pids["root"]))
        _wait_until(lambda: not _pid_is_live(pids["child"]))
        _wait_until(lambda: controller.snapshot().process_group_id is None)
        snapshot = controller.snapshot()

        assert snapshot.returncode == -signal.SIGKILL
        assert snapshot.process_group_id is None
        assert signal_observations
        assert signal_observations[0][2] == psutil.STATUS_ZOMBIE
        assert signal_observations[0][3] is None
        assert controller.has_owned_process is True
        assert controller.stop(grace_timeout=0.1, kill_timeout=1.0).complete is True
    finally:
        controller.stop(grace_timeout=0.1, kill_timeout=1.0)
        for pid in pids.values():
            if not _pid_is_live(pid):
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd assertion")
def test_linux_missing_pidfd_never_signals_reused_numeric_group(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class UnrelatedProcess:
        pid = 4242
        info = {"pid": 4242, "create_time": 20.0, "status": "running"}

        @staticmethod
        def create_time() -> float:
            return 20.0

        @staticmethod
        def status() -> str:
            return "running"

    unrelated = UnrelatedProcess()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"
        current = unrelated

        @classmethod
        def Process(cls, pid: int):
            if pid == unrelated.pid and cls.current is not None:
                return cls.current
            raise MissingProcess(pid)

        @staticmethod
        def process_iter(_attrs):
            return [unrelated]

    original = ProcessIdentity(pid=4242, create_time=10.0)
    owned_root = SimpleNamespace(pid=original.pid, returncode=None)
    killpg_calls = []
    monkeypatch.setattr(os, "getpgid", lambda _pid: original.pid)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    controller = OwnedProcessController(psutil_module=FakePsutil)
    controller._process = owned_root  # type: ignore[assignment]
    controller._identity = original
    controller._process_group_id = original.pid
    controller._posix_authority_valid = True
    waitid_calls = []
    monkeypatch.setattr(os, "waitid", lambda *_args: waitid_calls.append(_args))

    with pytest.raises(RuntimeError, match="indeterminate authority"):
        controller._signal_posix_group(signal.SIGTERM, original)

    assert waitid_calls == []
    assert controller._process_group_id == original.pid
    assert controller._posix_authority_valid is True
    assert controller._posix_cleanup_uncertain is False
    assert killpg_calls == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX exact-anchor assertion")
@pytest.mark.parametrize(
    ("status", "exited"),
    (("running", False), ("zombie", True), ("dead", True)),
)
def test_posix_exact_live_or_waitable_anchor_can_signal_group(
    monkeypatch,
    status: str,
    exited: bool,
) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class Leader:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            return 10.0

        @staticmethod
        def status() -> str:
            return status

    leader = Leader()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        @staticmethod
        def Process(pid: int):
            assert pid == leader.pid
            return leader

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(leader.pid, 10.0)
    controller._process = leader  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = leader.pid
    controller._posix_authority_valid = True
    pidfd = _attach_test_pidfd(controller, monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda _pid: leader.pid)
    monkeypatch.setattr(
        os,
        "waitid",
        lambda *_args: (
            SimpleNamespace(
                si_pid=leader.pid,
                si_code=os.CLD_EXITED,
                si_status=0,
            )
            if exited
            else None
        ),
    )
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    try:
        controller._signal_posix_group(signal.SIGTERM, identity)

        assert killpg_calls == [(leader.pid, signal.SIGTERM)]
        assert controller._process_group_id == leader.pid
        assert controller._posix_authority_valid is True
        assert controller._posix_cleanup_uncertain is False
        assert leader.returncode is None
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd signal assertion")
def test_pidfd_group_signal_einval_fallback_revalidates_before_killpg(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class Leader:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            return 10.0

        @staticmethod
        def status() -> str:
            return "running"

    leader = Leader()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess

        @staticmethod
        def Process(pid):
            assert pid == leader.pid
            return leader

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(leader.pid, 10.0)
    controller._process = leader  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = leader.pid
    controller._posix_authority_valid = True
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd
    monkeypatch.setattr(os, "getpgid", lambda _pid: leader.pid)
    waitid_calls = []

    def fake_waitid(*args):
        waitid_calls.append(args)
        return None

    monkeypatch.setattr(os, "waitid", fake_waitid)
    monkeypatch.setattr(controller, "_signal_linux_pidfd_group", lambda _fd, _sig: False)
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    try:
        controller._signal_posix_group(signal.SIGTERM, identity)

        # Two P_PIDFD proofs before the attempted atomic signal and two fresh
        # proofs after its older-kernel EINVAL fallback.
        assert len(waitid_calls) == 4
        assert all(call[0] == getattr(os, "P_PIDFD", 3) for call in waitid_calls)
        assert killpg_calls == [(leader.pid, signal.SIGTERM)]
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd signal assertion")
def test_pidfd_group_signal_esrch_never_falls_back_to_killpg(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class Leader:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            return 10.0

        @staticmethod
        def status() -> str:
            return "running"

    leader = Leader()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess

        @staticmethod
        def Process(pid):
            assert pid == leader.pid
            return leader

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(leader.pid, 10.0)
    controller._process = leader  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = leader.pid
    controller._posix_authority_valid = True
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd
    monkeypatch.setattr(os, "getpgid", lambda _pid: leader.pid)
    monkeypatch.setattr(os, "waitid", lambda *_args: None)

    def pidfd_esrch(_fd, _sig):
        raise ProcessLookupError(errno.ESRCH, "gone")

    monkeypatch.setattr(controller, "_signal_linux_pidfd_group", pidfd_esrch)
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    try:
        with pytest.raises(ProcessLookupError):
            controller._signal_posix_group(signal.SIGKILL, identity)
        assert killpg_calls == []
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd signal assertion")
def test_pidfd_group_signal_success_never_calls_killpg(monkeypatch) -> None:
    controller = OwnedProcessController()
    process = SimpleNamespace(pid=4242, returncode=None)
    identity = ProcessIdentity(process.pid, 10.0)
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = process.pid
    controller._posix_authority_valid = True
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd
    proof = _PosixAuthorityProof(
        _PosixAuthorityState.VALID,
        "injected exact launch authority",
        process.pid,
    )
    monkeypatch.setattr(controller, "_prove_posix_group_authority", lambda _identity: proof)
    atomic_calls = []
    monkeypatch.setattr(
        controller,
        "_signal_linux_pidfd_group",
        lambda fd, sig: atomic_calls.append((fd, sig)) or True,
    )
    monkeypatch.setattr(os, "killpg", lambda *_args: pytest.fail("killpg must not run"))

    try:
        controller._signal_posix_group(signal.SIGTERM, identity)

        assert atomic_calls == [(pidfd, signal.SIGTERM)]
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd signal assertion")
def test_exact_pidfd_process_signal_uses_zero_flags(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        OwnedProcessController,
        "_send_linux_pidfd_signal",
        staticmethod(lambda fd, sig, flags: calls.append((fd, sig, flags))),
    )

    OwnedProcessController._signal_linux_pidfd_process(41, signal.SIGKILL)

    assert calls == [(41, signal.SIGKILL, 0)]


@pytest.mark.skipif(os.name == "nt", reason="POSIX WSL identity-drift assertion")
def test_posix_exact_child_and_group_override_psutil_create_time_drift(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class Leader:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            return 11.0

        @staticmethod
        def status() -> str:
            return "zombie"

    leader = Leader()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        @staticmethod
        def Process(pid: int):
            assert pid == leader.pid
            return leader

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(leader.pid, 10.0)
    controller._process = leader  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = leader.pid
    controller._posix_authority_valid = True
    controller._posix_pidfd = os.open("/dev/null", os.O_RDONLY)
    monkeypatch.setattr(controller, "_signal_linux_pidfd_group", lambda _fd, _sig: False)
    monkeypatch.setattr(os, "getpgid", lambda _pid: leader.pid)
    monkeypatch.setattr(
        os,
        "waitid",
        lambda *_args: SimpleNamespace(
            si_pid=leader.pid,
            si_code=os.CLD_EXITED,
            si_status=0,
        ),
    )
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    try:
        controller._signal_posix_group(signal.SIGKILL, identity)

        assert killpg_calls == [(leader.pid, signal.SIGKILL)]
        assert controller._posix_authority_valid is True
        assert controller._posix_cleanup_uncertain is False
    finally:
        controller._close_posix_pidfd()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd assertion")
def test_posix_quantized_creation_collision_without_pidfd_is_indeterminate(
    monkeypatch,
) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class ReusedLeader:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            # Deliberately collides with the old identity's clock tick.
            return 10.0

        @staticmethod
        def status() -> str:
            return "running"

    leader = ReusedLeader()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        @staticmethod
        def Process(pid: int):
            assert pid == leader.pid
            return leader

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(leader.pid, 10.0)
    controller._process = leader  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = leader.pid
    controller._posix_authority_valid = True
    monkeypatch.setattr(os, "getpgid", lambda _pid: leader.pid)
    waitid_calls = []
    monkeypatch.setattr(os, "waitid", lambda *_args: waitid_calls.append(_args))
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    with pytest.raises(RuntimeError, match="indeterminate authority"):
        controller._signal_posix_group(signal.SIGTERM, identity)

    assert waitid_calls == []
    assert killpg_calls == []
    assert controller._process_group_id == leader.pid
    assert controller._posix_authority_valid is True
    assert controller._posix_cleanup_uncertain is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd assertion")
def test_pidfd_rejects_same_parent_numeric_pid_reuse(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class ReusedChild:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            return 11.0

        @staticmethod
        def status() -> str:
            return "running"

    child = ReusedChild()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        @staticmethod
        def Process(pid: int):
            assert pid == child.pid
            return child

    controller = OwnedProcessController(psutil_module=FakePsutil)
    original = ProcessIdentity(child.pid, 10.0)
    controller._process = child  # type: ignore[assignment]
    controller._identity = original
    controller._process_group_id = child.pid
    controller._posix_authority_valid = True
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd
    monkeypatch.setattr(os, "getpgid", lambda _pid: child.pid)
    wait_calls = []

    def fake_waitid(idtype, identifier, _options):
        wait_calls.append((idtype, identifier))
        if idtype == getattr(os, "P_PIDFD", 3):
            assert identifier != pidfd
            os.fstat(identifier)
            raise ChildProcessError
        raise AssertionError("Linux authority must never fall back to P_PID")

    monkeypatch.setattr(os, "waitid", fake_waitid)
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    numeric_proof = controller._probe_posix_child_anchor(child.pid, None)
    assert numeric_proof.state.value == "indeterminate"

    with pytest.raises(RuntimeError, match=r"invalid authority .*ECHILD"):
        controller._signal_posix_group(signal.SIGKILL, original)

    assert len(wait_calls) == 1
    assert wait_calls[0][0] == getattr(os, "P_PIDFD", 3)
    assert killpg_calls == []
    assert controller._process_group_id is None
    assert controller._posix_authority_valid is False
    assert controller._posix_cleanup_uncertain is True
    assert controller._posix_pidfd is None
    with pytest.raises(OSError):
        os.fstat(pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd assertion")
def test_invalid_pidfd_reports_ebadf_separately_from_echild(monkeypatch) -> None:
    process = SimpleNamespace(pid=4242, returncode=None)
    identity = ProcessIdentity(process.pid, 10.0)
    controller = OwnedProcessController()
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = process.pid
    controller._posix_authority_valid = True
    invalid_pidfd = os.open("/dev/null", os.O_RDONLY)
    os.close(invalid_pidfd)
    controller._posix_pidfd = invalid_pidfd
    monkeypatch.setattr(os, "killpg", lambda *_args: pytest.fail("killpg must not run"))

    with pytest.raises(RuntimeError, match=r"invalid authority .*EBADF"):
        controller._signal_posix_group(signal.SIGTERM, identity)

    assert controller._process_group_id is None
    assert controller._posix_authority_valid is False
    assert controller._posix_cleanup_uncertain is True
    assert controller._posix_pidfd is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux libc pidfd assertion")
def test_linux_pidfd_open_uses_checked_libc_fallback(monkeypatch) -> None:
    monkeypatch.setattr(os, "pidfd_open", None, raising=False)

    pidfd = OwnedProcessController._open_linux_pidfd(os.getpid())

    assert pidfd is not None
    try:
        os.fstat(pidfd)
        assert os.get_inheritable(pidfd) is False
    finally:
        os.close(pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux syscall assertion")
def test_linux_pidfd_open_uses_checked_syscall_when_libc_symbol_is_missing(
    monkeypatch,
) -> None:
    import ctypes

    opened_fd = os.open("/dev/null", os.O_RDONLY)
    calls = []

    class FakeSyscall:
        restype = None

        def __call__(self, number, pid, flags):
            calls.append((number.value, pid.value, flags.value))
            return opened_fd

    class FakeLibc:
        syscall = FakeSyscall()

        @property
        def pidfd_open(self):
            raise AttributeError("pidfd_open")

    monkeypatch.setattr(os, "pidfd_open", None, raising=False)
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc())

    pidfd = OwnedProcessController._open_linux_pidfd(os.getpid())

    assert pidfd == opened_fd
    try:
        assert calls == [(434, os.getpid(), 0)]
        assert os.get_inheritable(pidfd) is False
        os.fstat(pidfd)
    finally:
        os.close(pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd gate assertion")
def test_unsupported_pidfd_waitid_fails_before_spawn_and_closes_probe(monkeypatch) -> None:
    popen_calls = []

    def forbidden_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        raise AssertionError("unsupported Linux ownership must fail before Popen")

    controller = OwnedProcessController(popen_factory=forbidden_popen)
    probe_pidfd = os.open("/dev/null", os.O_RDONLY)
    monkeypatch.setattr(controller, "_open_linux_pidfd", lambda _pid: probe_pidfd)
    waitid_calls = []

    def unsupported_pidfd_wait(idtype, identifier, _options):
        waitid_calls.append((idtype, identifier))
        raise OSError(errno.EINVAL, "P_PIDFD unsupported")

    monkeypatch.setattr(os, "waitid", unsupported_pidfd_wait)

    with pytest.raises(RuntimeError, match=r"requires functional waitid\(P_PIDFD\)"):
        controller.start([sys.executable, "-c", "pass"])

    assert popen_calls == []
    assert waitid_calls == [(getattr(os, "P_PIDFD", 3), probe_pidfd)]
    assert controller.state == ProcessLifecycle.START_FAILED
    assert controller.has_owned_process is False
    with pytest.raises(OSError):
        os.fstat(probe_pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX pidfd reset assertion")
def test_reset_refuses_to_erase_retained_posix_pidfd_authority() -> None:
    controller = OwnedProcessController()
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd

    with pytest.raises(RuntimeError, match="pidfd authority is retained"):
        controller._reset_for_launch(SecretRedactor())

    assert controller._posix_pidfd == pidfd
    assert controller.has_owned_process is True
    os.fstat(pidfd)
    stop_result = controller.stop(grace_timeout=0.0, kill_timeout=0.0)
    assert stop_result.complete is False
    assert "refusing silent release" in (stop_result.error or "")
    assert controller._posix_pidfd == pidfd
    os.fstat(pidfd)
    controller._close_posix_pidfd()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux monitor pidfd assertion")
def test_monitor_owns_prestart_pidfd_duplicate_and_closes_it(monkeypatch) -> None:
    import runtime.process as process_runtime

    process = SimpleNamespace(pid=4242, returncode=None)
    identity = ProcessIdentity(process.pid, 10.0)
    controller = OwnedProcessController()
    primary_pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = primary_pidfd
    observed_monitor_fds = []

    class ImmediateThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(process_runtime.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(
        controller,
        "_monitor_posix",
        lambda _process, _identity, monitor_pidfd: observed_monitor_fds.append(monitor_pidfd),
    )

    try:
        controller._start_monitor(process, identity)  # type: ignore[arg-type]

        assert len(observed_monitor_fds) == 1
        monitor_pidfd = observed_monitor_fds[0]
        assert monitor_pidfd != primary_pidfd
        with pytest.raises(OSError):
            os.fstat(monitor_pidfd)
        os.fstat(primary_pidfd)
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(primary_pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux monitor pidfd assertion")
def test_monitor_start_failure_closes_prestart_pidfd_duplicate(monkeypatch) -> None:
    import runtime.process as process_runtime

    process = SimpleNamespace(pid=4242, returncode=None)
    identity = ProcessIdentity(process.pid, 10.0)
    controller = OwnedProcessController()
    primary_pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = primary_pidfd
    captured_args = []

    class FailingThread:
        def __init__(self, *, args, **_kwargs):
            captured_args.extend(args)

        @staticmethod
        def start():
            raise RuntimeError("injected thread start failure")

    monkeypatch.setattr(process_runtime.threading, "Thread", FailingThread)

    try:
        with pytest.raises(RuntimeError, match="injected thread start failure"):
            controller._start_monitor(process, identity)  # type: ignore[arg-type]

        monitor_pidfd = captured_args[2]
        assert monitor_pidfd != primary_pidfd
        with pytest.raises(OSError):
            os.fstat(monitor_pidfd)
        os.fstat(primary_pidfd)
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(primary_pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux monitor pidfd assertion")
def test_monitor_constructor_failure_closes_prestart_pidfd_duplicate(monkeypatch) -> None:
    import runtime.process as process_runtime

    process = SimpleNamespace(pid=4242, returncode=None)
    identity = ProcessIdentity(process.pid, 10.0)
    controller = OwnedProcessController()
    primary_pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = primary_pidfd
    captured_args = []

    class FailingThread:
        def __init__(self, *, args, **_kwargs):
            captured_args.extend(args)
            raise RuntimeError("injected thread construction failure")

    monkeypatch.setattr(process_runtime.threading, "Thread", FailingThread)

    try:
        with pytest.raises(RuntimeError, match="injected thread construction failure"):
            controller._start_monitor(process, identity)  # type: ignore[arg-type]

        monitor_pidfd = captured_args[2]
        assert monitor_pidfd != primary_pidfd
        with pytest.raises(OSError):
            os.fstat(monitor_pidfd)
        os.fstat(primary_pidfd)
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(primary_pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd reap assertion")
def test_pidfd_echild_reap_never_waits_on_numeric_pid_and_marks_uncertain(
    monkeypatch,
) -> None:
    class ReapedProcess:
        pid = 4242
        returncode = None
        wait_calls = 0

        def wait(self, *, timeout):
            del timeout
            self.wait_calls += 1
            raise AssertionError("numeric Popen.wait must not run after pidfd ECHILD")

    process = ReapedProcess()
    identity = ProcessIdentity(process.pid, 10.0)
    controller = OwnedProcessController()
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd
    monkeypatch.setattr(os, "waitid", lambda *_args: (_ for _ in ()).throw(ChildProcessError()))

    returncode = controller._consume_posix_exit(
        process,  # type: ignore[arg-type]
        identity,
        0.0,
    )

    assert returncode is None
    assert process.wait_calls == 0
    assert process.returncode == 0
    assert controller._posix_root_reaped is True
    assert controller._posix_cleanup_uncertain is True
    assert "ECHILD" in (controller._last_error or "")
    assert controller._posix_pidfd is None
    with pytest.raises(OSError):
        os.fstat(pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd wait assertion")
def test_linux_monitor_waitid_unsupported_never_falls_back_to_psutil(monkeypatch) -> None:
    class ForbiddenPsutil:
        class AccessDenied(Exception):
            pass

        class NoSuchProcess(Exception):
            pass

        @staticmethod
        def Process(_pid):
            raise AssertionError("Linux monitor must not use psutil after pidfd wait failure")

    controller = OwnedProcessController(psutil_module=ForbiddenPsutil)
    identity = ProcessIdentity(4242, 10.0)
    monitor_pidfd = os.open("/dev/null", os.O_RDONLY)
    monkeypatch.setattr(
        os,
        "waitid",
        lambda *_args: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")),
    )

    try:
        with pytest.raises(RuntimeError, match="refusing numeric fallback"):
            controller._wait_for_posix_exit(identity, monitor_pidfd)
        os.fstat(monitor_pidfd)
    finally:
        os.close(monitor_pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd wait assertion")
def test_linux_nowait_observation_unsupported_never_falls_back_to_psutil(
    monkeypatch,
) -> None:
    class ForbiddenPsutil:
        class AccessDenied(Exception):
            pass

        class NoSuchProcess(Exception):
            pass

        @staticmethod
        def Process(_pid):
            raise AssertionError("Linux observation must not use psutil after pidfd failure")

    process = SimpleNamespace(pid=4242, returncode=None)
    identity = ProcessIdentity(process.pid, 10.0)
    controller = OwnedProcessController(psutil_module=ForbiddenPsutil)
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd
    monkeypatch.setattr(
        os,
        "waitid",
        lambda *_args: (_ for _ in ()).throw(OSError(errno.ENOSYS, "unsupported")),
    )

    try:
        with pytest.raises(RuntimeError, match="refusing numeric fallback"):
            controller._posix_exit_observed_nowait(
                process,  # type: ignore[arg-type]
                identity,
            )
        os.fstat(pidfd)
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(pidfd)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd wait assertion")
def test_missing_waitid_closes_nowait_observation_duplicate(monkeypatch) -> None:
    process = SimpleNamespace(pid=4242, returncode=None)
    identity = ProcessIdentity(process.pid, 10.0)
    controller = OwnedProcessController()
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd
    duplicate_fds = []
    duplicate = controller._duplicate_linux_pidfd

    def capture_duplicate():
        fd = duplicate()
        duplicate_fds.append(fd)
        return fd

    monkeypatch.setattr(controller, "_duplicate_linux_pidfd", capture_duplicate)
    monkeypatch.setattr(os, "waitid", None)

    try:
        with pytest.raises(RuntimeError, match="refusing numeric fallback"):
            controller._posix_exit_observed_nowait(
                process,  # type: ignore[arg-type]
                identity,
            )
        assert len(duplicate_fds) == 1
        with pytest.raises(OSError):
            os.fstat(duplicate_fds[0])
        os.fstat(pidfd)
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX final child-proof assertion")
def test_posix_final_child_recheck_catches_external_reap(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class Leader:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            return 10.0

        @staticmethod
        def status() -> str:
            return "zombie"

    leader = Leader()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        @staticmethod
        def Process(pid: int):
            assert pid == leader.pid
            return leader

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(leader.pid, 10.0)
    controller._process = leader  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = leader.pid
    controller._posix_authority_valid = True
    pidfd = _attach_test_pidfd(controller, monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda _pid: leader.pid)
    waitid_calls = 0

    def reap_between_checks(*_args):
        nonlocal waitid_calls
        waitid_calls += 1
        if waitid_calls == 1:
            return SimpleNamespace(
                si_pid=leader.pid,
                si_code=os.CLD_EXITED,
                si_status=0,
            )
        raise ChildProcessError

    monkeypatch.setattr(os, "waitid", reap_between_checks)
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    with pytest.raises(RuntimeError, match="invalid authority"):
        controller._signal_posix_group(signal.SIGKILL, identity)

    assert waitid_calls == 2
    assert killpg_calls == []
    assert controller._process_group_id is None
    assert controller._posix_authority_valid is False
    assert controller._posix_cleanup_uncertain is True
    assert controller._posix_pidfd is None
    _close_test_fd(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX missing-leader assertion")
def test_posix_missing_psutil_leader_retires_exact_child_authority(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    owned_root = SimpleNamespace(pid=4242, returncode=None)

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        @staticmethod
        def Process(pid: int):
            raise MissingProcess(pid)

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(owned_root.pid, 10.0)
    controller._process = owned_root  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = owned_root.pid
    controller._posix_authority_valid = True
    pidfd = _attach_test_pidfd(controller, monkeypatch)
    monkeypatch.setattr(os, "waitid", lambda *_args: None)
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    with pytest.raises(RuntimeError, match="invalid authority"):
        controller._signal_posix_group(signal.SIGTERM, identity)

    assert killpg_calls == []
    assert controller._process_group_id is None
    assert controller._posix_authority_valid is False
    assert controller._posix_cleanup_uncertain is True
    assert controller._posix_pidfd is None
    _close_test_fd(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX PGID identity assertion")
def test_posix_exact_leader_with_changed_group_is_never_signaled(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class Leader:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            return 10.0

        @staticmethod
        def status() -> str:
            return "running"

    leader = Leader()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        @staticmethod
        def Process(pid: int):
            assert pid == leader.pid
            return leader

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(leader.pid, 10.0)
    controller._process = leader  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = leader.pid
    controller._posix_authority_valid = True
    pidfd = _attach_test_pidfd(controller, monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda _pid: leader.pid + 1)
    monkeypatch.setattr(os, "waitid", lambda *_args: None)
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    with pytest.raises(RuntimeError, match="invalid authority"):
        controller._signal_posix_group(signal.SIGTERM, identity)

    assert killpg_calls == []
    assert controller._process_group_id is None
    assert controller._posix_authority_valid is False
    assert controller._posix_cleanup_uncertain is True
    assert controller._posix_pidfd is None
    _close_test_fd(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX indeterminate-authority assertion")
def test_posix_access_denied_preserves_authority_for_retry(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class Leader:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            return 10.0

        @staticmethod
        def status() -> str:
            return "zombie"

    leader = Leader()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"
        denied = True

        @classmethod
        def Process(cls, pid: int):
            assert pid == leader.pid
            if cls.denied:
                raise DeniedProcess(pid)
            return leader

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(leader.pid, 10.0)
    controller._process = leader  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = leader.pid
    controller._posix_authority_valid = True
    pidfd = _attach_test_pidfd(controller, monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda _pid: leader.pid)
    monkeypatch.setattr(
        os,
        "waitid",
        lambda *_args: SimpleNamespace(
            si_pid=leader.pid,
            si_code=os.CLD_EXITED,
            si_status=0,
        ),
    )
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    with pytest.raises(RuntimeError, match="indeterminate authority"):
        controller._signal_posix_group(signal.SIGTERM, identity)

    assert killpg_calls == []
    assert controller._process_group_id == leader.pid
    assert controller._posix_authority_valid is True
    assert controller._posix_cleanup_uncertain is False
    assert controller._posix_root_reaped is False

    try:
        FakePsutil.denied = False
        controller._signal_posix_group(signal.SIGKILL, identity)
        assert killpg_calls == [(leader.pid, signal.SIGKILL)]
    finally:
        controller._close_posix_pidfd()
        _close_test_fd(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX unsupported-authority assertion")
def test_posix_missing_waitid_preserves_authority_without_signaling(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class Leader:
        pid = 4242
        returncode = None

        @staticmethod
        def create_time() -> float:
            return 10.0

        @staticmethod
        def status() -> str:
            return "running"

    leader = Leader()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        @staticmethod
        def Process(pid: int):
            assert pid == leader.pid
            return leader

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(leader.pid, 10.0)
    controller._process = leader  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = leader.pid
    controller._posix_authority_valid = True
    pidfd = _attach_test_pidfd(controller, monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda _pid: leader.pid)
    monkeypatch.setattr(os, "waitid", None)
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    with pytest.raises(RuntimeError, match="indeterminate authority"):
        controller._signal_posix_group(signal.SIGTERM, identity)

    assert killpg_calls == []
    assert controller._process_group_id == leader.pid
    assert controller._posix_authority_valid is True
    assert controller._posix_cleanup_uncertain is False
    controller._close_posix_pidfd()
    _close_test_fd(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX lost-authority assertion")
def test_externally_reaped_posix_leader_fails_closed_without_group_signal(
    monkeypatch,
) -> None:
    class ExternallyReapedProcess:
        pid = 4242
        returncode = None
        wait_calls = 0

        def wait(self, *, timeout):
            del timeout
            self.wait_calls += 1
            raise ChildProcessError

    controller = OwnedProcessController()
    process = ExternallyReapedProcess()
    identity = ProcessIdentity(process.pid, 10.0)
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = process.pid
    controller._posix_authority_valid = True
    controller._state = ProcessLifecycle.RUNNING
    monkeypatch.setattr(
        controller,
        "_wait_for_posix_exit",
        lambda _identity, _monitor_pidfd: (None, True),
    )
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    controller._monitor_posix(process, identity)  # type: ignore[arg-type]

    snapshot = controller.snapshot()
    assert killpg_calls == []
    assert snapshot.state == ProcessLifecycle.RUNTIME_FAILED
    assert snapshot.process_group_id is None
    assert controller._posix_authority_valid is False
    assert controller._posix_cleanup_uncertain is True
    assert controller._posix_root_reaped is True
    assert process.wait_calls == 0
    assert "refusing descendant cleanup" in (snapshot.last_error or "")


@pytest.mark.skipif(os.name == "nt", reason="POSIX incomplete-cleanup assertion")
def test_incomplete_posix_cleanup_retains_unreaped_group_authority(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class HeldProcess:
        pid = 4242
        returncode = None
        wait_calls = 0

        def wait(self, *, timeout):
            del timeout
            self.wait_calls += 1
            raise AssertionError("incomplete cleanup must not reap the group leader")

        @staticmethod
        def create_time() -> float:
            return 10.0

        @staticmethod
        def status() -> str:
            return "zombie"

    process = HeldProcess()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"
        STATUS_DEAD = "dead"

        @staticmethod
        def Process(pid: int):
            if pid == process.pid:
                return process
            raise MissingProcess(pid)

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(process.pid, 10.0)
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = process.pid
    controller._posix_authority_valid = True
    pidfd = _attach_test_pidfd(controller, monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda _pid: process.pid)
    monkeypatch.setattr(
        os,
        "waitid",
        lambda *_args: SimpleNamespace(
            si_pid=process.pid,
            si_code=os.CLD_EXITED,
            si_status=0,
        ),
    )
    signals = []
    monkeypatch.setattr(
        controller,
        "_signal_posix_group",
        lambda sig, _identity: signals.append(sig),
    )
    monkeypatch.setattr(
        controller,
        "_wait_for_owned_tree",
        lambda _process, _identity, _timeout: (9001,),
    )

    escalated, remaining, errors = controller._stop_posix_group(
        process,  # type: ignore[arg-type]
        identity,
        0.0,
        0.0,
    )

    assert escalated is True
    assert remaining == (9001,)
    assert errors == []
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.wait_calls == 0
    assert controller._process_group_id == process.pid
    assert controller._posix_authority_valid is True
    assert controller._posix_root_reaped is False
    controller._close_posix_pidfd()
    _close_test_fd(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX group inspection assertion")
def test_posix_group_access_denied_is_indeterminate_not_empty(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class HiddenProcess:
        pid = 9001
        info = {}

        @staticmethod
        def status():
            raise DeniedProcess

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"

        @staticmethod
        def process_iter(_attrs):
            return [HiddenProcess()]

    controller = OwnedProcessController(psutil_module=FakePsutil)
    monkeypatch.setattr(os, "getpgid", lambda _pid: 4242)

    inspection = controller._live_posix_group_members(4242)
    waited = controller._wait_for_posix_pgid_empty(4242, 0.0)

    assert inspection.state.value == "indeterminate"
    assert inspection.members == ()
    assert "could not be inspected" in inspection.reason
    assert waited.state.value == "indeterminate"


@pytest.mark.skipif(os.name == "nt", reason="POSIX empty-observation assertion")
def test_zero_timeout_still_requires_two_immediate_empty_observations(monkeypatch) -> None:
    process = SimpleNamespace(pid=4242, returncode=None)
    identity = ProcessIdentity(process.pid, 10.0)
    controller = OwnedProcessController()
    owned_tree_calls = 0

    def empty_owned_tree(_identity):
        nonlocal owned_tree_calls
        owned_tree_calls += 1
        return ()

    monkeypatch.setattr(controller, "_remaining_owned_pids", empty_owned_tree)

    remaining = controller._wait_for_owned_tree(
        process,  # type: ignore[arg-type]
        identity,
        0.0,
    )

    assert remaining == ()
    assert owned_tree_calls == 2

    group_calls = 0

    def empty_group(_pgid):
        nonlocal group_calls
        group_calls += 1
        return SimpleNamespace(
            state=_PosixAuthorityState.VALID,
            members=(),
            reason="injected empty group",
        )

    monkeypatch.setattr(controller, "_live_posix_group_members", empty_group)

    inspection = controller._wait_for_posix_pgid_empty(process.pid, 0.0)

    assert inspection.members == ()
    assert group_calls == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX defensive-stop assertion")
def test_stop_reports_when_cleanup_and_remaining_inspection_both_fail(monkeypatch) -> None:
    process = SimpleNamespace(pid=4242, returncode=None)
    identity = ProcessIdentity(process.pid, 10.0)
    controller = OwnedProcessController()
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = process.pid
    controller._posix_authority_valid = True
    monkeypatch.setattr(
        controller,
        "_stop_posix_group",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("primary cleanup failure")),
    )
    monkeypatch.setattr(
        controller,
        "_remaining_owned_pids",
        lambda _identity: (_ for _ in ()).throw(PermissionError("inspection failure")),
    )

    result = controller.stop(grace_timeout=0.0, kill_timeout=0.0)

    assert result.complete is False
    assert result.remaining_pids == (process.pid,)
    assert "primary cleanup failure" in (result.error or "")
    assert "remaining-process inspection failed" in (result.error or "")
    assert "inspection failure" in (result.error or "")
    assert controller.state == ProcessLifecycle.INCOMPLETE_STOP


@pytest.mark.skipif(os.name == "nt", reason="POSIX non-reaping diagnostic assertion")
def test_posix_passive_status_never_polls_or_reaps_the_owned_process() -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class FakeIdentityProcess:
        pid = 123

        @staticmethod
        def create_time() -> float:
            return 10.0

        @staticmethod
        def status() -> str:
            return "running"

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"

        @staticmethod
        def Process(pid: int):
            assert pid == 123
            return FakeIdentityProcess()

    class NoPollProcess:
        returncode = None

        @staticmethod
        def poll():
            raise AssertionError("passive diagnostics must not reap through Popen.poll")

    controller = OwnedProcessController(psutil_module=FakePsutil)
    controller._process = NoPollProcess()  # type: ignore[assignment]
    controller._identity = ProcessIdentity(pid=123, create_time=10.0)
    controller._state = ProcessLifecycle.RUNNING

    assert controller.is_running is True
    assert controller.snapshot().returncode is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux exact cleanup assertion")
def test_launch_group_mismatch_never_kills_reused_numeric_pid(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class IdentityProbe:
        pid = 4242

        @staticmethod
        def create_time() -> float:
            return 10.0

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess

        @staticmethod
        def Process(pid):
            assert pid == 4242
            return IdentityProbe()

    class ReusedNumericProcess:
        pid = 4242
        returncode = None
        kill_calls = 0
        wait_calls = 0

        def kill(self):
            self.kill_calls += 1
            raise AssertionError("Popen.kill must not target a potentially reused numeric PID")

        def wait(self, *, timeout):
            del timeout
            self.wait_calls += 1
            raise AssertionError("Popen.wait must not target a potentially reused numeric PID")

    process = ReusedNumericProcess()
    controller = OwnedProcessController(
        popen_factory=lambda *_args, **_kwargs: process,  # type: ignore[arg-type]
        psutil_module=FakePsutil,
    )
    pidfd = os.open("/dev/null", os.O_RDONLY)
    monkeypatch.setattr(controller, "_require_linux_pidfd_support", lambda: None)
    monkeypatch.setattr(controller, "_open_linux_pidfd", lambda _pid: pidfd)
    monkeypatch.setattr(os, "getpgid", lambda _pid: process.pid + 1)
    monkeypatch.setattr(os, "getsid", lambda _pid: process.pid + 1)
    monkeypatch.setattr(
        os,
        "killpg",
        lambda *_args: pytest.fail("killpg must not run after launch validation mismatch"),
    )
    exact_signal_calls = []

    def exact_generation_is_gone(fd, sig):
        exact_signal_calls.append((fd, sig))
        raise ProcessLookupError(errno.ESRCH, "original generation exited")

    monkeypatch.setattr(controller, "_signal_linux_pidfd_process", exact_generation_is_gone)
    waitid_calls = []

    def externally_consumed_pidfd(idtype, identifier, _options):
        waitid_calls.append((idtype, identifier))
        assert idtype == getattr(os, "P_PIDFD", 3)
        assert identifier != pidfd
        raise ChildProcessError

    monkeypatch.setattr(os, "waitid", externally_consumed_pidfd)

    with pytest.raises(RuntimeError, match="failed to establish a unique owned process group"):
        controller.start([sys.executable, "-c", "pass"])

    assert exact_signal_calls == [(pidfd, signal.SIGKILL)]
    assert len(waitid_calls) == 1
    assert process.kill_calls == 0
    assert process.wait_calls == 0
    assert process.returncode == 0
    assert controller._posix_root_reaped is True
    assert controller._posix_cleanup_uncertain is True
    assert controller._posix_pidfd is None
    assert controller.state == ProcessLifecycle.INCOMPLETE_STOP
    with pytest.raises(OSError):
        os.fstat(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX launch verification assertion")
def test_posix_launch_query_failure_cleans_the_start_new_session_tree(monkeypatch) -> None:
    controller = OwnedProcessController()

    def fail_getsid(_pid: int) -> int:
        raise OSError("injected getsid failure")

    monkeypatch.setattr(os, "getsid", fail_getsid)

    with pytest.raises(OSError, match="injected getsid failure"):
        controller.start([sys.executable, "-u", "-c", "import time; time.sleep(30)"])

    assert controller.state == ProcessLifecycle.START_FAILED
    assert controller.has_owned_process is False
    assert controller.snapshot().process_group_id is None
    assert controller._posix_pidfd is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX exact-identity launch assertion")
def test_posix_identity_capture_failure_cleans_provisional_group(monkeypatch) -> None:
    controller = OwnedProcessController()

    def fail_capture(_pid: int) -> ProcessIdentity:
        raise RuntimeError("injected identity capture failure")

    monkeypatch.setattr(controller, "_capture_identity", fail_capture)

    with pytest.raises(RuntimeError, match="injected identity capture failure"):
        controller.start([sys.executable, "-u", "-c", "import time; time.sleep(30)"])

    assert controller.state == ProcessLifecycle.START_FAILED
    assert controller.has_owned_process is False
    snapshot = controller.snapshot()
    assert snapshot.process_group_id is None
    assert snapshot.returncode is not None
    assert controller._posix_pidfd is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd retry assertion")
def test_public_stop_retries_retained_provisional_pidfd_cleanup(monkeypatch) -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class ProvisionalProcess:
        pid = 4242
        returncode = None
        wait_calls = 0

        def wait(self, *, timeout):
            assert timeout >= 0
            self.wait_calls += 1
            self.returncode = -signal.SIGKILL
            return self.returncode

    process = ProvisionalProcess()

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"

        @staticmethod
        def process_iter(_attrs):
            return []

    controller = OwnedProcessController(psutil_module=FakePsutil)
    controller._process = process  # type: ignore[assignment]
    controller._process_group_id = process.pid
    controller._posix_authority_valid = True
    pidfd = os.open("/dev/null", os.O_RDONLY)
    controller._posix_pidfd = pidfd
    monkeypatch.setattr(os, "getpgid", lambda _pid: process.pid)
    # /dev/null is only a harmless descriptor for mocked waitid proofs.  Force
    # the checked fallback so host kernel support cannot reinterpret it as a
    # real pidfd during the group-signal syscall.
    monkeypatch.setattr(controller, "_signal_linux_pidfd_group", lambda _fd, _sig: False)
    denied = True

    def fake_waitid(idtype, identifier, _options):
        assert idtype == getattr(os, "P_PIDFD", 3)
        assert identifier != pidfd
        os.fstat(identifier)
        if denied:
            raise PermissionError("transient pidfd inspection failure")
        return SimpleNamespace(
            si_pid=process.pid,
            si_code=os.CLD_KILLED,
            si_status=signal.SIGKILL,
        )

    monkeypatch.setattr(os, "waitid", fake_waitid)
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    first = controller.stop(grace_timeout=0.0, kill_timeout=0.1)
    assert first.complete is False
    assert killpg_calls == []
    assert process.wait_calls == 0
    assert controller.has_owned_process is True
    assert controller._posix_pidfd == pidfd
    os.fstat(pidfd)

    denied = False
    second = controller.stop(grace_timeout=0.0, kill_timeout=0.1)
    assert second.complete is True
    assert killpg_calls == [(process.pid, signal.SIGKILL)]
    assert process.wait_calls == 0
    assert process.returncode == -signal.SIGKILL
    assert controller.has_owned_process is False
    assert controller._posix_pidfd is None
    with pytest.raises(OSError):
        os.fstat(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX provisional fail-closed assertion")
def test_provisional_cleanup_without_launch_pidfd_never_signals_group(monkeypatch) -> None:
    process = SimpleNamespace(pid=4242, returncode=None)
    controller = OwnedProcessController()
    controller._process = process  # type: ignore[assignment]
    controller._process_group_id = process.pid
    controller._posix_authority_valid = True
    killpg_calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    result = controller.stop(grace_timeout=0.0, kill_timeout=0.0)

    assert result.complete is False
    assert killpg_calls == []
    assert controller.has_owned_process is True
    assert controller._process_group_id == process.pid
    assert controller._posix_authority_valid is True


def test_windows_tracker_and_monitor_reject_stale_process_with_same_identity(monkeypatch) -> None:
    import runtime.process as process_runtime

    identity = ProcessIdentity(4242, 10.0)

    class OldProcess:
        pid = identity.pid
        returncode = None
        poll_calls = 0
        wait_calls = 0

        def poll(self):
            self.poll_calls += 1
            raise AssertionError("stale tracker must not poll the old process")

        def wait(self):
            self.wait_calls += 1
            return 7

    old_process = OldProcess()
    new_process = SimpleNamespace(pid=identity.pid, returncode=None)
    controller = OwnedProcessController()
    controller._process = new_process  # type: ignore[assignment]
    controller._identity = identity
    controller._state = ProcessLifecycle.RUNNING
    descendant_calls = []
    monkeypatch.setattr(
        controller,
        "_collect_windows_descendants",
        lambda _identity: descendant_calls.append(_identity),
    )

    controller._track_descendants(old_process, identity)  # type: ignore[arg-type]

    assert descendant_calls == []
    assert old_process.poll_calls == 0

    monkeypatch.setattr(process_runtime.os, "name", "nt")
    controller._monitor(old_process, identity)  # type: ignore[arg-type]

    assert old_process.wait_calls == 1
    assert controller._returncode is None
    assert controller.state == ProcessLifecycle.RUNNING
    assert controller._last_error is None


def test_windows_job_setup_is_fresh_and_degrades_to_validated_fallback() -> None:
    created_jobs: list[object] = []

    class FakeJob:
        def __init__(self) -> None:
            self.assigned: list[object] = []
            self.closed = False

        def assign(self, process: object) -> None:
            self.assigned.append(process)

        def close(self) -> None:
            self.closed = True

    def make_job() -> FakeJob:
        job = FakeJob()
        created_jobs.append(job)
        return job

    process = object()
    controller = OwnedProcessController(windows_job_factory=make_job)
    assert controller.snapshot().descendant_fallback is False
    controller._setup_windows_ownership(process)  # type: ignore[arg-type]

    first = created_jobs[0]
    assert first.assigned == [process]  # type: ignore[attr-defined]
    assert controller.snapshot().windows_job_assigned is True
    assert controller.snapshot().descendant_fallback is False

    controller._reset_for_launch(SecretRedactor())
    assert first.closed is True  # type: ignore[attr-defined]
    assert controller.snapshot().descendant_fallback is False
    controller._setup_windows_ownership(process)  # type: ignore[arg-type]
    assert len(created_jobs) == 2
    assert created_jobs[0] is not created_jobs[1]

    degraded = OwnedProcessController(
        windows_job_factory=lambda: (_ for _ in ()).throw(OSError("unavailable"))
    )
    degraded._setup_windows_ownership(process)  # type: ignore[arg-type]
    snapshot = degraded.snapshot()
    assert snapshot.windows_job_assigned is False
    assert snapshot.descendant_fallback is True
    assert snapshot.last_error is not None
    assert "validated fallback active" in snapshot.last_error


def test_validated_windows_fallback_retains_descendants_after_root_exit() -> None:
    class MissingProcess(Exception):
        pass

    class DeniedProcess(Exception):
        pass

    class FakeProcess:
        def __init__(self, pid: int, created: float) -> None:
            self.pid = pid
            self.created = created
            self.descendants: list[FakeProcess] = []
            self.alive = True

        def create_time(self) -> float:
            return self.created

        def children(self, *, recursive: bool) -> list[FakeProcess]:
            assert recursive is True
            return list(self.descendants)

        def status(self) -> str:
            return "running"

    root = FakeProcess(100, 10.0)
    child = FakeProcess(101, 11.0)
    root.descendants = [child]
    processes = {root.pid: root, child.pid: child}

    class FakePsutil:
        NoSuchProcess = MissingProcess
        AccessDenied = DeniedProcess
        STATUS_ZOMBIE = "zombie"

        @staticmethod
        def Process(pid: int) -> FakeProcess:
            try:
                process = processes[pid]
            except KeyError as exc:
                raise MissingProcess(pid) from exc
            if not process.alive:
                raise MissingProcess(pid)
            return process

    controller = OwnedProcessController(psutil_module=FakePsutil)
    identity = ProcessIdentity(root.pid, root.created)
    controller._identity = identity

    assert {item.pid for item in controller._validated_windows_tree(identity)} == {100, 101}
    root.alive = False

    retained = controller._validated_windows_tree(identity)
    assert tuple(item.pid for item in retained) == (101,)


def test_failed_windows_job_close_remains_retryable() -> None:
    class FlakyJob:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                raise OSError("first close failed")

    controller = OwnedProcessController()
    job = FlakyJob()
    controller._windows_job = job
    controller._windows_job_assigned = True

    with pytest.raises(OSError, match="first close failed"):
        controller._close_windows_job()
    assert controller._windows_job is job
    assert controller.snapshot().windows_job_assigned is True

    controller._close_windows_job()
    assert controller._windows_job is None
    assert controller.snapshot().windows_job_assigned is False


def test_command_redaction_covers_separate_and_inline_credentials() -> None:
    redactor = SecretRedactor(("literal-secret",))

    assert redactor.redact_argv(
        [
            "llama-server",
            "--api-key",
            "literal-secret",
            "--token=another-secret",
            "--model",
            "literal-secret.gguf",
        ]
    ) == (
        "llama-server",
        "--api-key",
        "<redacted>",
        "--token=<redacted>",
        "--model",
        "<redacted>.gguf",
    )
