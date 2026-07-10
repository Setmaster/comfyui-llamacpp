"""Coordination tests for runtime leases and release barriers."""

from __future__ import annotations

import threading

import pytest

from runtime.process import StopResult
from runtime.service import (
    ReleaseStatus,
    RuntimeLifecycle,
    RuntimeMode,
    RuntimeReleasePending,
    RuntimeService,
)


class FakeSnapshot:
    def as_dict(self) -> dict[str, object]:
        return {"state": "running"}


class FakeProcessController:
    def __init__(self, *, running: bool = True) -> None:
        self.running = running
        self.owned = running
        self.stop_calls = 0
        self.stop_entered: threading.Event | None = None
        self.stop_gate: threading.Event | None = None
        self.stop_result = StopResult(True, False, 0, 0.01)

    @property
    def is_running(self) -> bool:
        return self.running

    @property
    def has_owned_process(self) -> bool:
        return self.owned

    def stop(self) -> StopResult:
        self.stop_calls += 1
        if self.stop_entered is not None:
            self.stop_entered.set()
        if self.stop_gate is not None:
            assert self.stop_gate.wait(timeout=5)
        if self.stop_result.complete:
            self.running = False
            self.owned = False
        return self.stop_result

    def snapshot(self) -> FakeSnapshot:
        return FakeSnapshot()


class FakeRouterClient:
    def __init__(self, models: list[dict[str, object]]) -> None:
        self._models = models
        self.unload_calls: list[tuple[str, float | None]] = []
        self.failure: BaseException | None = None
        self.terminal_state = "unloaded"

    def list_models(self) -> list[dict[str, object]]:
        return self._models

    def unload_model(self, model_id: str, *, timeout: float | None = None):
        self.unload_calls.append((model_id, timeout))
        if self.failure is not None:
            raise self.failure
        return {"id": model_id, "state": self.terminal_state}


def test_direct_release_stops_only_the_owned_process_and_clears_mode() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()

    result = service.request_release(source="test")

    assert result.status == ReleaseStatus.COMPLETE
    assert result.success is True
    assert process.stop_calls == 1
    assert service.mode == RuntimeMode.NONE
    assert service.is_owned is False
    assert service.diagnostics()["lifecycle"] == RuntimeLifecycle.IDLE.value


def test_direct_release_with_no_remaining_process_clears_stale_owned_mode() -> None:
    process = FakeProcessController(running=False)
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()

    result = service.request_release(source="test")

    assert result.status == ReleaseStatus.NOOP
    assert process.stop_calls == 0
    assert service.mode == RuntimeMode.NONE
    assert service.is_owned is False


def test_attached_runtime_is_never_changed_by_implicit_release() -> None:
    process = FakeProcessController(running=False)
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_attached()

    result = service.request_release(source="comfy_free")

    assert result.status == ReleaseStatus.NOOP
    assert result.mode == RuntimeMode.ATTACHED
    assert result.owned is False
    assert process.stop_calls == 0
    assert service.mode == RuntimeMode.ATTACHED


def test_release_during_generation_is_deferred_until_final_lease_exits() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()

    with service.generation_lease():
        result = service.request_release(source="comfy_free")
        assert result.status == ReleaseStatus.DEFERRED
        assert process.stop_calls == 0
        assert service.release_pending is True
        with pytest.raises(RuntimeReleasePending):
            with service.generation_lease():
                pass

    assert process.stop_calls == 1
    diagnostics = service.diagnostics()
    assert diagnostics["release_pending"] is False
    assert diagnostics["last_release"]["status"] == ReleaseStatus.COMPLETE.value


def test_deferred_release_waits_for_all_concurrent_generation_leases() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    entered = threading.Barrier(3)
    first_gate = threading.Event()
    second_gate = threading.Event()

    def generate(gate: threading.Event) -> None:
        with service.generation_lease():
            entered.wait(timeout=5)
            assert gate.wait(timeout=5)

    first = threading.Thread(target=generate, args=(first_gate,))
    second = threading.Thread(target=generate, args=(second_gate,))
    first.start()
    second.start()
    entered.wait(timeout=5)

    assert service.active_generations == 2
    assert service.request_release(source="comfy_free").status == ReleaseStatus.DEFERRED

    first_gate.set()
    first.join(timeout=5)
    assert not first.is_alive()
    assert service.active_generations == 1
    assert process.stop_calls == 0

    second_gate.set()
    second.join(timeout=5)
    assert not second.is_alive()
    assert service.active_generations == 0
    assert process.stop_calls == 1


def test_concurrent_release_requests_coalesce_onto_one_stop() -> None:
    process = FakeProcessController()
    process.stop_entered = threading.Event()
    process.stop_gate = threading.Event()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    results: dict[str, object] = {}

    def first_release() -> None:
        results["first"] = service.request_release(source="first")

    def waiting_release() -> None:
        results["waiting"] = service.request_release(source="waiting")

    first = threading.Thread(target=first_release)
    first.start()
    assert process.stop_entered.wait(timeout=5)

    immediate = service.request_release(source="immediate", wait_for_coalesced=False)
    waiter = threading.Thread(target=waiting_release)
    waiter.start()
    assert waiter.is_alive()

    process.stop_gate.set()
    first.join(timeout=5)
    waiter.join(timeout=5)

    assert not first.is_alive()
    assert not waiter.is_alive()
    assert immediate.status == ReleaseStatus.COALESCED
    assert results["first"].status == ReleaseStatus.COMPLETE  # type: ignore[union-attr]
    assert results["waiting"].status == ReleaseStatus.COMPLETE  # type: ignore[union-attr]
    assert results["waiting"].coalesced is True  # type: ignore[union-attr]
    assert process.stop_calls == 1


def test_router_release_unloads_each_resident_model_to_terminal_state() -> None:
    process = FakeProcessController()
    client = FakeRouterClient(
        [
            {"id": "loaded-model", "status": {"value": "loaded"}},
            {"id": "sleeping-model", "state": "sleeping"},
            {"id": "cold-model", "state": "unloaded"},
            {"id": "download-only", "state": "downloaded"},
        ]
    )
    service = RuntimeService(process, router_release_timeout=17.0)  # type: ignore[arg-type]
    service.configure_router_owned(client)

    result = service.request_release(source="comfy_free")

    assert result.status == ReleaseStatus.COMPLETE
    assert result.released_models == ("loaded-model", "sleeping-model")
    assert client.unload_calls == [
        ("loaded-model", 17.0),
        ("sleeping-model", 17.0),
    ]
    assert process.stop_calls == 0
    assert service.mode == RuntimeMode.ROUTER
    assert service.diagnostics()["lifecycle"] == RuntimeLifecycle.READY.value


@pytest.mark.parametrize("terminal_state", ["loaded", "failed", "unknown"])
def test_router_non_unloaded_terminal_state_stops_owned_router_as_fallback(
    terminal_state: str,
) -> None:
    process = FakeProcessController()
    client = FakeRouterClient([{"id": "model-a", "state": "loaded"}])
    client.terminal_state = terminal_state
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)

    result = service.request_release(source="comfy_free")

    assert result.status == ReleaseStatus.FALLBACK_COMPLETE
    assert result.fallback_used is True
    assert process.stop_calls == 1
    assert service.mode == RuntimeMode.NONE


def test_router_barrier_failure_stops_owned_router_but_not_an_external_one() -> None:
    owned_process = FakeProcessController()
    client = FakeRouterClient([{"id": "model-a", "state": "loaded"}])
    client.failure = TimeoutError("barrier timed out")
    owned = RuntimeService(owned_process)  # type: ignore[arg-type]
    owned.configure_router_owned(client)

    owned_result = owned.request_release(source="comfy_free")

    assert owned_result.status == ReleaseStatus.FALLBACK_COMPLETE
    assert owned_result.fallback_used is True
    assert owned_process.stop_calls == 1

    external_process = FakeProcessController(running=False)
    external = RuntimeService(external_process)  # type: ignore[arg-type]
    external.configure_attached()
    external_result = external.request_release(source="comfy_free")
    assert external_result.status == ReleaseStatus.NOOP
    assert external_process.stop_calls == 0


def test_router_failure_without_an_owned_process_is_reported_not_hidden() -> None:
    process = FakeProcessController(running=False)
    client = FakeRouterClient([{"id": "model-a", "state": "loaded"}])
    client.failure = TimeoutError("barrier timed out")
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)

    result = service.request_release(source="test")

    assert result.status == ReleaseStatus.FAILED
    assert result.fallback_used is True
    assert result.error is not None
    assert service.diagnostics()["lifecycle"] == RuntimeLifecycle.ERROR.value


def test_release_listeners_receive_terminal_and_noop_results() -> None:
    process = FakeProcessController(running=False)
    service = RuntimeService(process)  # type: ignore[arg-type]
    observed = []
    service.add_release_listener(observed.append)

    noop = service.request_release(source="nothing")
    assert observed == [noop]

    process.running = True
    process.owned = True
    service.configure_direct_owned()
    complete = service.request_release(source="owned")
    assert observed[-1] == complete
