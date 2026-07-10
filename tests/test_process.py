"""Behavior tests for positively owned subprocess trees."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from runtime.process import (
    OwnedProcessController,
    ProcessAlreadyRunning,
    ProcessIdentity,
    ProcessLifecycle,
    SecretRedactor,
)

HELPER = Path(__file__).parent / "fixtures" / "process_tree_helper.py"


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

        result = controller.stop(grace_timeout=2.0, kill_timeout=2.0)

        assert result.complete is True
        assert result.remaining_pids == ()
        assert controller.state == ProcessLifecycle.STOPPED
        assert controller.has_owned_process is False
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX PGID reuse assertion")
def test_reused_posix_group_id_is_never_signaled_after_leader_reap(monkeypatch) -> None:
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

        @staticmethod
        def Process(pid: int):
            if pid == unrelated.pid:
                return unrelated
            raise MissingProcess(pid)

        @staticmethod
        def process_iter(_attrs):
            return [unrelated]

    controller = OwnedProcessController(psutil_module=FakePsutil)
    original = ProcessIdentity(pid=4242, create_time=10.0)
    controller._identity = original
    controller._process_group_id = original.pid
    killpg_calls = []
    monkeypatch.setattr(os, "getpgid", lambda _pid: original.pid)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    with pytest.raises(RuntimeError, match="leader authority expired"):
        controller._signal_posix_group(signal.SIGTERM, original)

    assert killpg_calls == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX lost-authority assertion")
def test_externally_reaped_posix_leader_fails_closed_without_group_signal(
    monkeypatch,
) -> None:
    class ExternallyReapedProcess:
        pid = 4242
        returncode = None

        @staticmethod
        def wait(*, timeout):
            del timeout
            raise ChildProcessError

    controller = OwnedProcessController()
    process = ExternallyReapedProcess()
    identity = ProcessIdentity(process.pid, 10.0)
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = process.pid
    controller._posix_authority_valid = True
    controller._state = ProcessLifecycle.RUNNING
    monkeypatch.setattr(controller, "_wait_for_posix_exit", lambda _identity: (None, True))
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
    assert "refusing descendant cleanup" in (snapshot.last_error or "")


@pytest.mark.skipif(os.name == "nt", reason="POSIX incomplete-cleanup assertion")
def test_incomplete_posix_cleanup_retains_unreaped_group_authority(monkeypatch) -> None:
    class HeldProcess:
        pid = 4242
        returncode = None
        wait_calls = 0

        def wait(self, *, timeout):
            del timeout
            self.wait_calls += 1
            raise AssertionError("incomplete cleanup must not reap the group leader")

    controller = OwnedProcessController()
    process = HeldProcess()
    identity = ProcessIdentity(process.pid, 10.0)
    controller._process = process  # type: ignore[assignment]
    controller._identity = identity
    controller._process_group_id = process.pid
    controller._posix_authority_valid = True
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
