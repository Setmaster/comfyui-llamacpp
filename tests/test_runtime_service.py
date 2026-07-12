"""Coordination tests for runtime leases and release barriers."""

from __future__ import annotations

import threading
import time

import pytest

from runtime.process import StopResult
from runtime.service import (
    ReleaseScope,
    ReleaseStatus,
    ReleaseWaitCancelled,
    ReleaseWaitTimeout,
    RuntimeLifecycle,
    RuntimeMode,
    RuntimeOperationBusy,
    RuntimeReleasePending,
    RuntimeService,
)


def _wait_for_event(event: threading.Event, *, timeout: float) -> bool:
    """Wait for the predicate, tolerating an early condition wake-up."""

    deadline = time.monotonic() + timeout
    while not event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        event.wait(timeout=remaining)
    return True


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
        self.stop_gate_timeout: float | None = 5
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
            if self.stop_gate_timeout is None:
                while not self.stop_gate.is_set():
                    self.stop_gate.wait()
            else:
                assert _wait_for_event(self.stop_gate, timeout=self.stop_gate_timeout)
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
        self.return_model_id: str | None = None
        self.unload_entered: threading.Event | None = None
        self.unload_gate: threading.Event | None = None

    def list_models(self) -> list[dict[str, object]]:
        return self._models

    def unload_model(self, model_id: str, *, timeout: float | None = None):
        self.unload_calls.append((model_id, timeout))
        if self.unload_entered is not None:
            self.unload_entered.set()
        if self.unload_gate is not None:
            while not self.unload_gate.is_set():
                self.unload_gate.wait()
        if self.failure is not None:
            raise self.failure
        return {"id": self.return_model_id or model_id, "state": self.terminal_state}


def test_direct_release_stops_only_the_owned_process_and_clears_mode() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()

    result = service.request_release(source="test")

    assert result.status == ReleaseStatus.COMPLETE
    assert result.success is True
    assert process.stop_calls == 1
    assert service.mode == RuntimeMode.NONE
    assert result.runtime_epoch == 1
    assert service.runtime_epoch == 2
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


def test_deferred_attached_global_noop_clears_pending_and_keeps_operation_identity() -> None:
    process = FakeProcessController(running=False)
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_attached()
    lease = service.begin_generation()

    deferred = service.request_release(source="comfy_free")
    assert deferred.status == ReleaseStatus.DEFERRED
    assert service.release_pending is True

    service.finish_generation(lease)
    diagnostics = service.diagnostics()
    terminal = diagnostics["last_release"]
    assert terminal["status"] == ReleaseStatus.NOOP.value
    assert terminal["operation_id"] == deferred.operation_id
    assert terminal["source"] == "comfy_free"
    assert diagnostics["release_pending"] is False
    assert diagnostics["lifecycle"] == RuntimeLifecycle.READY.value

    next_lease = service.begin_generation()
    service.finish_generation(next_lease)


def test_release_during_generation_is_deferred_until_final_lease_exits() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()

    with service.generation_lease():
        result = service.request_release(source="comfy_free")
        assert result.status == ReleaseStatus.DEFERRED
        assert result.success is True
        assert result.accepted is True
        assert result.terminal is False
        assert result.as_dict()["accepted"] is True
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

    class InstrumentedOperationLock:
        def __init__(self) -> None:
            self._lock = threading.RLock()
            self.waiter_attempted = threading.Event()

        def __enter__(self):
            if threading.current_thread().name == "waiting-release":
                # request_release reaches operation acquisition only after its
                # read-only release-generation snapshot.  This removes scheduler
                # luck from the coalesced-wait assertion below.
                self.waiter_attempted.set()
            self._lock.acquire()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self._lock.release()

    operation_lock = InstrumentedOperationLock()
    service._operation_lock = operation_lock  # type: ignore[assignment]
    results: dict[str, object] = {}

    def first_release() -> None:
        results["first"] = service.request_release(source="first")

    def waiting_release() -> None:
        results["waiting"] = service.request_release(source="waiting")

    first = threading.Thread(target=first_release)
    first.start()
    assert process.stop_entered.wait(timeout=5)

    immediate = service.request_release(source="immediate", wait_for_coalesced=False)
    waiter = threading.Thread(target=waiting_release, name="waiting-release")
    waiter.start()
    assert operation_lock.waiter_attempted.wait(timeout=5)
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


def test_global_release_remains_coalescible_through_listener_transition() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    listener_entered = threading.Event()
    listener_gate = threading.Event()
    results: dict[str, object] = {}

    class InstrumentedOperationLock:
        def __init__(self) -> None:
            self._lock = threading.RLock()
            self.waiter_attempted = threading.Event()

        def __enter__(self):
            if threading.current_thread().name == "listener-window-waiter":
                self.waiter_attempted.set()
            self._lock.acquire()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self._lock.release()

    operation_lock = InstrumentedOperationLock()
    service._operation_lock = operation_lock  # type: ignore[assignment]

    def listener(_result) -> None:
        listener_entered.set()
        assert _wait_for_event(listener_gate, timeout=5)

    def first_release() -> None:
        results["first"] = service.request_release(source="first")

    def waiting_release() -> None:
        results["waiting"] = service.request_release(source="waiting")

    service.add_release_listener(listener)
    first = threading.Thread(target=first_release)
    first.start()
    assert listener_entered.wait(timeout=5)

    immediate = service.request_release(source="immediate", wait_for_coalesced=False)
    waiter = threading.Thread(target=waiting_release, name="listener-window-waiter")
    waiter.start()
    assert operation_lock.waiter_attempted.wait(timeout=5)
    assert waiter.is_alive()

    listener_gate.set()
    first.join(timeout=5)
    waiter.join(timeout=5)

    assert not first.is_alive()
    assert not waiter.is_alive()
    assert immediate.status == ReleaseStatus.COALESCED
    assert immediate.operation_id == results["first"].operation_id  # type: ignore[union-attr]
    assert results["first"].status == ReleaseStatus.COMPLETE  # type: ignore[union-attr]
    assert results["waiting"].status == ReleaseStatus.COMPLETE  # type: ignore[union-attr]
    assert results["waiting"].coalesced is True  # type: ignore[union-attr]
    assert results["waiting"].operation_id == immediate.operation_id  # type: ignore[union-attr]
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


@pytest.mark.parametrize("terminal_state", ["loaded", "unknown"])
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


def test_router_failed_terminal_state_is_released_without_stopping_router() -> None:
    process = FakeProcessController()
    client = FakeRouterClient([{"id": "model-a", "state": "loaded"}])
    client.terminal_state = "failed"
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)

    result = service.request_release(source="comfy_free")

    assert result.status == ReleaseStatus.COMPLETE
    assert result.released_models == ("model-a",)
    assert result.released_model_states == (("model-a", "failed"),)
    assert result.as_dict()["released_model_states"] == [{"id": "model-a", "state": "failed"}]
    assert result.as_dict()["released_model_diagnostics"] == [
        {"id": "model-a", "state": "failed", "raw": {}}
    ]
    assert process.stop_calls == 0
    assert process.is_running is True
    assert service.mode == RuntimeMode.ROUTER


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


def test_router_release_failure_and_diagnostics_drop_upstream_response_material() -> None:
    secret = "RAW_SERVER_BODY_SECRET"
    process = FakeProcessController(running=False)
    client = FakeRouterClient(
        [
            {
                "id": "model-a",
                "state": "loaded",
                "raw": {"response_body": secret},
                "arbitrary": secret,
            }
        ]
    )
    client.failure = RuntimeError(secret)
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)

    result = service.request_release(source="test")

    assert result.status == ReleaseStatus.FAILED
    assert result.error == "router barrier failed (RuntimeError)"
    assert secret not in str(result.as_dict())
    assert secret not in str(service.diagnostics())


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


def test_release_listener_reentry_uses_a_finite_ordered_nonrecursive_drain() -> None:
    service = RuntimeService(FakeProcessController(running=False))  # type: ignore[arg-type]
    callback_count = 0
    callback_depth = 0
    maximum_depth = 0
    observed_sources: list[str] = []

    def reentrant_listener(_result) -> None:
        nonlocal callback_count, callback_depth, maximum_depth
        callback_count += 1
        callback_depth += 1
        maximum_depth = max(maximum_depth, callback_depth)
        try:
            if callback_count < 32:
                service.request_release(source=f"nested-{callback_count}")
        finally:
            callback_depth -= 1

    service.add_release_listener(reentrant_listener)
    service.add_release_listener(lambda result: observed_sources.append(result.source))

    service.request_release(source="root")

    assert callback_count == 32
    assert maximum_depth == 1
    assert observed_sources == ["root", *(f"nested-{index}" for index in range(1, 32))]


def test_diagnostics_remains_available_during_serialized_operation() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    operation_entered = threading.Event()
    operation_gate = threading.Event()
    diagnostics_complete = threading.Event()
    observed: dict[str, object] = {}

    def hold_operation() -> None:
        with service.serialized_operation():
            operation_entered.set()
            assert operation_gate.wait(timeout=5)

    def read_diagnostics() -> None:
        observed.update(service.diagnostics())
        diagnostics_complete.set()

    holder = threading.Thread(target=hold_operation)
    reader = threading.Thread(target=read_diagnostics)
    holder.start()
    assert operation_entered.wait(timeout=5)
    reader.start()
    status_available = diagnostics_complete.wait(timeout=0.5)

    operation_gate.set()
    holder.join(timeout=5)
    reader.join(timeout=5)

    assert status_available is True
    assert not holder.is_alive()
    assert not reader.is_alive()
    assert observed["lifecycle"] == RuntimeLifecycle.READY.value
    assert observed["process"] == {"state": "running"}


def test_diagnostics_observes_release_while_stop_barrier_is_blocked() -> None:
    process = FakeProcessController()
    process.stop_entered = threading.Event()
    process.stop_gate = threading.Event()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    diagnostics_complete = threading.Event()
    observed: dict[str, object] = {}
    released: dict[str, object] = {}

    def release() -> None:
        released["result"] = service.request_release(source="test")

    def read_diagnostics() -> None:
        observed.update(service.diagnostics())
        diagnostics_complete.set()

    releaser = threading.Thread(target=release)
    reader = threading.Thread(target=read_diagnostics)
    releaser.start()
    assert process.stop_entered.wait(timeout=5)
    reader.start()
    status_available = diagnostics_complete.wait(timeout=0.5)

    process.stop_gate.set()
    releaser.join(timeout=5)
    reader.join(timeout=5)

    assert status_available is True
    assert not releaser.is_alive()
    assert not reader.is_alive()
    assert observed["lifecycle"] == RuntimeLifecycle.RELEASING.value
    assert observed["release_in_progress"] is True
    assert observed["process"] == {"state": "running"}
    assert released["result"].status == ReleaseStatus.COMPLETE  # type: ignore[union-attr]


def test_scoped_direct_release_waits_for_every_admitted_direct_lease() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()

    releasing = service.begin_generation(release_after=True, source="first")
    concurrent = service.begin_generation(source="second")
    handle = releasing.release_handle
    assert handle is not None
    assert handle.done is False

    assert service.finish_generation(releasing) is handle
    assert process.stop_calls == 0
    assert handle.done is False
    with pytest.raises(RuntimeReleasePending, match="direct runtime release"):
        service.begin_generation()

    service.finish_generation(concurrent)
    result = handle.wait(timeout=2)

    assert result.status == ReleaseStatus.COMPLETE
    assert result.scope == ReleaseScope.DIRECT_RUNTIME
    assert result.operation_id == handle.operation_id
    assert result.request_id == handle.request_id
    assert process.stop_calls == 1
    assert service.mode == RuntimeMode.NONE


def test_resolve_and_begin_direct_generation_skips_router_resolution() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()

    def unexpected_resolver(_model: str) -> str:
        raise AssertionError("direct generation must not resolve a router model")

    exact_model, lease = service.resolve_and_begin_generation(
        requested_model="  direct/model.gguf  ",
        resolve_router_model=unexpected_resolver,
    )

    assert exact_model == "direct/model.gguf"
    assert lease.mode == RuntimeMode.DIRECT
    assert lease.model_id is None
    assert lease.runtime_epoch == service.runtime_epoch
    assert service.active_generations == 1
    service.finish_generation(lease)


def test_resolve_and_begin_generation_rejects_unowned_runtime_before_resolution() -> None:
    unconfigured = RuntimeService(FakeProcessController(running=False))  # type: ignore[arg-type]
    attached = RuntimeService(FakeProcessController(running=False))  # type: ignore[arg-type]
    attached.configure_attached()

    for service in (unconfigured, attached):
        resolver_called = False

        def unexpected_resolver(model: str) -> str:
            nonlocal resolver_called
            resolver_called = True
            return model

        with pytest.raises(RuntimeError, match="owned direct or router runtime"):
            service.resolve_and_begin_generation(
                requested_model="model.gguf",
                resolve_router_model=unexpected_resolver,
            )
        assert resolver_called is False
        assert service.active_generations == 0


def test_router_resolution_and_lease_admission_are_one_runtime_operation() -> None:
    process = FakeProcessController()
    client = FakeRouterClient([{"id": "canonical/model.gguf", "state": "loaded"}])
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    resolver_entered = threading.Event()
    resolver_gate = threading.Event()
    results: dict[str, object] = {}

    def resolve(model: str) -> str:
        assert model == "friendly-name"
        resolver_entered.set()
        assert _wait_for_event(resolver_gate, timeout=5)
        return "canonical/model.gguf"

    def admit() -> None:
        results["admission"] = service.resolve_and_begin_generation(
            requested_model="friendly-name",
            resolve_router_model=resolve,
            release_after=True,
            source="canonical_generate",
        )

    def reconfigure() -> None:
        try:
            service.configure_direct_owned()
        except BaseException as exc:
            results["reconfigure_error"] = exc

    admission = threading.Thread(target=admit)
    admission.start()
    assert resolver_entered.wait(timeout=5)

    diagnostics: dict[str, object] = {}
    diagnostics_complete = threading.Event()

    def read_diagnostics() -> None:
        diagnostics.update(service.diagnostics())
        diagnostics_complete.set()

    reader = threading.Thread(target=read_diagnostics)
    reader.start()
    assert diagnostics_complete.wait(timeout=0.5)
    reader.join(timeout=2)
    assert reader.is_alive() is False
    assert diagnostics["lifecycle"] == RuntimeLifecycle.READY.value

    reconfiguration = threading.Thread(target=reconfigure)
    reconfiguration.start()
    reconfiguration.join(timeout=0.05)
    assert reconfiguration.is_alive()

    resolver_gate.set()
    admission.join(timeout=5)
    reconfiguration.join(timeout=5)
    assert admission.is_alive() is False
    assert reconfiguration.is_alive() is False

    exact_model, lease = results["admission"]  # type: ignore[misc]
    assert exact_model == "canonical/model.gguf"
    assert lease.mode == RuntimeMode.ROUTER
    assert lease.model_id == exact_model
    assert lease.runtime_epoch == service.runtime_epoch
    assert isinstance(results["reconfigure_error"], RuntimeError)
    assert "active generation" in str(results["reconfigure_error"])
    assert service.mode == RuntimeMode.ROUTER

    handle = service.finish_generation(lease)
    assert handle is not None
    result = handle.wait(timeout=2)
    assert result.status == ReleaseStatus.COMPLETE
    assert result.target_model == exact_model
    assert client.unload_calls == [("canonical/model.gguf", 60.0)]


def test_router_resolution_epoch_change_rejects_admission_without_a_lease() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(FakeRouterClient([]))

    def stale_resolution(_model: str) -> str:
        with service._state_lock:
            service._runtime_epoch += 1
        return "canonical/model.gguf"

    with pytest.raises(RuntimeError, match="runtime changed"):
        service.resolve_and_begin_generation(
            requested_model="friendly-name",
            resolve_router_model=stale_resolution,
        )

    assert service.active_generations == 0
    assert service.diagnostics()["active_router_generations"] == {}


def test_router_resolver_error_or_empty_identity_never_admits_a_lease() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(FakeRouterClient([]))

    def failed_resolution(_model: str) -> str:
        raise LookupError("catalog unavailable")

    with pytest.raises(LookupError, match="catalog unavailable"):
        service.resolve_and_begin_generation(
            requested_model="friendly-name",
            resolve_router_model=failed_resolution,
        )
    with pytest.raises(ValueError, match="no exact model ID"):
        service.resolve_and_begin_generation(
            requested_model="friendly-name",
            resolve_router_model=lambda _model: "  ",
        )
    with pytest.raises(ValueError, match="exact model selection"):
        service.resolve_and_begin_generation(
            requested_model="  ",
            resolve_router_model=lambda model: model,
        )

    active = service.begin_generation(model_id="other/model.gguf")
    assert service.request_release(source="global").status == ReleaseStatus.DEFERRED
    resolver_called = False

    def resolver_during_release(model: str) -> str:
        nonlocal resolver_called
        resolver_called = True
        return model

    with pytest.raises(RuntimeReleasePending, match="release is pending"):
        service.resolve_and_begin_generation(
            requested_model="friendly-name",
            resolve_router_model=resolver_during_release,
        )
    assert resolver_called is False
    service.finish_generation(active)

    assert service.active_generations == 0
    assert service.diagnostics()["active_router_generations"] == {}


@pytest.mark.parametrize(
    "resolved_model",
    (
        "x" * 4097,
        "é" * 2049,
        "model-\ud800",
    ),
)
def test_router_resolution_validates_exact_identity_before_lease_admission(
    resolved_model: str,
) -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(FakeRouterClient([]))

    with pytest.raises(ValueError, match="exact model ID"):
        service.resolve_and_begin_generation(
            requested_model="friendly-name",
            resolve_router_model=lambda _model: resolved_model,
            release_after=True,
        )

    diagnostics = service.diagnostics()
    assert service.active_generations == 0
    assert diagnostics["active_router_generations"] == {}
    assert diagnostics["scoped_releases"] == []


def test_dormant_direct_release_handles_coalesce_before_either_lease_exits() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()

    first = service.begin_generation(release_after=True, source="first")
    second = service.begin_generation(release_after=True, source="second")
    first_handle = first.release_handle
    second_handle = second.release_handle
    assert first_handle is not None
    assert second_handle is not None
    assert first_handle.operation_id == second_handle.operation_id
    assert first_handle.request_id != second_handle.request_id
    assert first_handle.coalesced is False
    assert second_handle.coalesced is True

    service.finish_generation(first)
    assert process.stop_calls == 0
    service.finish_generation(second)

    first_result = first_handle.wait(timeout=2)
    second_result = second_handle.wait(timeout=2)
    assert first_result.status == second_result.status == ReleaseStatus.COMPLETE
    assert first_result.coalesced is False
    assert second_result.coalesced is True
    assert process.stop_calls == 1


def test_release_handle_timeout_and_cancel_abandon_only_the_waiter() -> None:
    process = FakeProcessController()
    process.stop_entered = threading.Event()
    process.stop_gate = threading.Event()
    process.stop_gate_timeout = None
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    lease = service.begin_generation(release_after=True)
    handle = lease.release_handle
    assert handle is not None

    service.finish_generation(lease)
    assert process.stop_entered.wait(timeout=2)

    with pytest.raises(ReleaseWaitTimeout, match="continues"):
        handle.wait(timeout=0.01)
    with pytest.raises(ReleaseWaitCancelled, match="continues"):
        handle.wait(timeout=1, cancel=lambda: True)
    assert handle.done is False
    assert process.stop_calls == 1

    process.stop_gate.set()
    assert handle.wait(timeout=2).status == ReleaseStatus.COMPLETE
    assert handle.done is True
    assert process.stop_calls == 1


def test_explicit_mutation_sees_running_scoped_release_as_busy() -> None:
    process = FakeProcessController()
    process.stop_entered = threading.Event()
    process.stop_gate = threading.Event()
    process.stop_gate_timeout = None
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    lease = service.begin_generation(release_after=True)
    handle = lease.release_handle
    assert handle is not None

    service.finish_generation(lease)
    assert process.stop_entered.wait(timeout=2)
    assert service.active_generations == 0

    with pytest.raises(RuntimeOperationBusy, match="runtime release"):
        with service.serialized_operation(require_idle=True, operation="test mutation"):
            pass

    process.stop_gate.set()
    assert handle.wait(timeout=2).status == ReleaseStatus.COMPLETE


def test_scoped_handle_signals_only_after_release_listener_transition() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    listener_entered = threading.Event()
    listener_gate = threading.Event()
    mutation_entered = threading.Event()

    def listener(result) -> None:
        assert result.scope == ReleaseScope.DIRECT_RUNTIME
        listener_entered.set()
        assert _wait_for_event(listener_gate, timeout=5)

    service.add_release_listener(listener)
    lease = service.begin_generation(release_after=True)
    handle = lease.release_handle
    assert handle is not None
    service.finish_generation(lease)

    assert listener_entered.wait(timeout=2)
    assert handle.done is False
    diagnostics = service.diagnostics()
    assert diagnostics["lifecycle"] == RuntimeLifecycle.RELEASING.value
    assert diagnostics["scoped_releases"] == [
        {
            "operation_id": handle.operation_id,
            "scope": ReleaseScope.DIRECT_RUNTIME.value,
            "target_model": None,
            "runtime_epoch": lease.runtime_epoch,
            "state": "transitioning",
            "superseded_by": None,
        }
    ]
    with pytest.raises(RuntimeOperationBusy, match="runtime release"):
        with service.serialized_operation(require_idle=True, operation="test mutation"):
            pass

    def mutate() -> None:
        with service.serialized_operation():
            mutation_entered.set()

    mutation = threading.Thread(target=mutate)
    mutation.start()
    mutation.join(timeout=0.05)
    assert mutation.is_alive()
    assert mutation_entered.is_set() is False

    listener_gate.set()
    assert handle.wait(timeout=2).status == ReleaseStatus.COMPLETE
    mutation.join(timeout=2)
    assert mutation.is_alive() is False
    assert mutation_entered.is_set() is True
    assert service.diagnostics()["lifecycle"] == RuntimeLifecycle.IDLE.value


def test_scoped_listener_base_exception_cannot_strand_worker_or_handle() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    observed: list[str] = []

    def broken_listener(_result) -> None:
        raise KeyboardInterrupt("listener must not terminate release worker")

    def healthy_listener(result) -> None:
        observed.append(result.operation_id)

    service.add_release_listener(broken_listener)
    service.add_release_listener(healthy_listener)
    lease = service.begin_generation(release_after=True)
    handle = lease.release_handle
    assert handle is not None

    service.finish_generation(lease)
    result = handle.wait(timeout=2)

    assert result.status == ReleaseStatus.COMPLETE
    assert observed == [handle.operation_id]
    assert service.mode == RuntimeMode.NONE


def test_router_scoped_release_unloads_a_while_b_generation_remains_active() -> None:
    process = FakeProcessController()
    client = FakeRouterClient(
        [
            {"id": "model-a", "state": "loaded"},
            {"id": "model-b", "state": "loaded"},
        ]
    )
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    lease_a = service.begin_generation(model_id="model-a", release_after=True, source="a")
    lease_b = service.begin_generation(model_id="model-b", source="b")
    handle = lease_a.release_handle
    assert handle is not None

    service.finish_generation(lease_a)
    result = handle.wait(timeout=2)

    assert result.status == ReleaseStatus.COMPLETE
    assert result.scope == ReleaseScope.ROUTER_MODEL
    assert result.target_model == "model-a"
    assert result.released_models == ("model-a",)
    assert client.unload_calls == [("model-a", 60.0)]
    assert service.active_generations == 1
    assert service.mode == RuntimeMode.ROUTER
    assert process.stop_calls == 0

    service.finish_generation(lease_b)


def test_running_router_scoped_release_does_not_block_other_model_admission() -> None:
    process = FakeProcessController()
    client = FakeRouterClient(
        [
            {"id": "model-a", "state": "loaded"},
            {"id": "model-b", "state": "loaded"},
        ]
    )
    client.unload_entered = threading.Event()
    client.unload_gate = threading.Event()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    releasing_a = service.begin_generation(model_id="model-a", release_after=True)
    handle = releasing_a.release_handle
    assert handle is not None
    service.finish_generation(releasing_a)
    assert client.unload_entered.wait(timeout=2)

    results: dict[str, object] = {}
    admitted_b = threading.Event()
    rejected_a = threading.Event()

    def admit_b() -> None:
        try:
            results["lease_b"] = service.begin_generation(model_id="model-b")
        except BaseException as exc:
            results["b_error"] = exc
        finally:
            admitted_b.set()

    def admit_a() -> None:
        try:
            results["lease_a"] = service.begin_generation(model_id="model-a")
        except BaseException as exc:
            results["a_error"] = exc
        finally:
            rejected_a.set()

    b_thread = threading.Thread(target=admit_b)
    a_thread = threading.Thread(target=admit_a)
    b_thread.start()
    a_thread.start()
    b_completed_while_unload_blocked = admitted_b.wait(timeout=0.5)
    a_completed_while_unload_blocked = rejected_a.wait(timeout=0.5)

    client.unload_gate.set()
    b_thread.join(timeout=2)
    a_thread.join(timeout=2)

    assert b_completed_while_unload_blocked is True
    assert a_completed_while_unload_blocked is True
    assert "b_error" not in results
    assert isinstance(results.get("a_error"), RuntimeReleasePending)
    assert "lease_a" not in results
    lease_b = results["lease_b"]
    assert handle.wait(timeout=2).status == ReleaseStatus.COMPLETE
    service.finish_generation(lease_b)  # type: ignore[arg-type]


def test_global_release_waits_for_running_scoped_io_without_overlapping_it() -> None:
    class SequencedRouterClient(FakeRouterClient):
        def __init__(self) -> None:
            super().__init__(
                [
                    {"id": "model-a", "state": "loaded"},
                    {"id": "model-b", "state": "loaded"},
                ]
            )
            self.active_unloads = 0
            self.maximum_active_unloads = 0
            self.activity_lock = threading.Lock()

        def unload_model(self, model_id: str, *, timeout: float | None = None):
            with self.activity_lock:
                self.active_unloads += 1
                self.maximum_active_unloads = max(
                    self.maximum_active_unloads,
                    self.active_unloads,
                )
            try:
                result = super().unload_model(model_id, timeout=timeout)
                for model in self._models:
                    if model.get("id") == model_id:
                        model["state"] = "unloaded"
                return result
            finally:
                with self.activity_lock:
                    self.active_unloads -= 1

    process = FakeProcessController()
    client = SequencedRouterClient()
    client.unload_entered = threading.Event()
    client.unload_gate = threading.Event()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    lease = service.begin_generation(model_id="model-a", release_after=True)
    handle = lease.release_handle
    assert handle is not None
    service.finish_generation(lease)
    assert client.unload_entered.wait(timeout=2)

    release_result: dict[str, object] = {}
    global_attempted = threading.Event()

    def release_globally() -> None:
        global_attempted.set()
        release_result["result"] = service.request_release(source="global")

    global_release = threading.Thread(target=release_globally)
    global_release.start()
    assert global_attempted.wait(timeout=2)
    global_release.join(timeout=0.05)
    global_waited_for_scope = global_release.is_alive()

    client.unload_gate.set()
    global_release.join(timeout=2)

    assert global_waited_for_scope is True
    assert global_release.is_alive() is False
    assert handle.wait(timeout=2).status == ReleaseStatus.COMPLETE
    assert release_result["result"].status == ReleaseStatus.COMPLETE  # type: ignore[union-attr]
    assert [model_id for model_id, _timeout in client.unload_calls] == ["model-a", "model-b"]
    assert client.maximum_active_unloads == 1


def test_armed_router_release_blocks_a_and_unknown_but_allows_exact_b() -> None:
    process = FakeProcessController()
    client = FakeRouterClient(
        [
            {"id": "model-a", "state": "loaded"},
            {"id": "model-b", "state": "loaded"},
        ]
    )
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    releasing_a = service.begin_generation(model_id="model-a", release_after=True)
    existing_a = service.begin_generation(model_id="model-a")
    handle = releasing_a.release_handle
    assert handle is not None

    service.finish_generation(releasing_a)
    with pytest.raises(RuntimeReleasePending, match="model-a"):
        service.begin_generation(model_id="model-a")
    with pytest.raises(RuntimeReleasePending, match="model identity is required"):
        service.begin_generation()
    admitted_b = service.begin_generation(model_id="model-b")

    service.finish_generation(existing_a)
    result = handle.wait(timeout=2)
    assert result.status == ReleaseStatus.COMPLETE
    assert client.unload_calls == [("model-a", 60.0)]
    assert service.active_generations == 1
    service.finish_generation(admitted_b)


def test_router_release_after_handles_for_same_model_coalesce_once() -> None:
    process = FakeProcessController()
    client = FakeRouterClient([{"id": "model-a", "state": "loaded"}])
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    first = service.begin_generation(model_id="model-a", release_after=True)
    second = service.begin_generation(model_id="model-a", release_after=True)
    first_handle = first.release_handle
    second_handle = second.release_handle
    assert first_handle is not None
    assert second_handle is not None
    assert first_handle.operation_id == second_handle.operation_id

    service.finish_generation(first)
    assert client.unload_calls == []
    service.finish_generation(second)

    assert first_handle.wait(timeout=2).status == ReleaseStatus.COMPLETE
    assert second_handle.wait(timeout=2).status == ReleaseStatus.COMPLETE
    assert client.unload_calls == [("model-a", 60.0)]


def test_legacy_unknown_router_lease_delays_exact_scoped_release() -> None:
    process = FakeProcessController()
    client = FakeRouterClient([{"id": "model-a", "state": "loaded"}])
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    legacy_unknown = service.begin_generation()
    releasing = service.begin_generation(model_id="model-a", release_after=True)
    handle = releasing.release_handle
    assert handle is not None

    service.finish_generation(releasing)
    assert handle.done is False
    assert client.unload_calls == []

    service.finish_generation(legacy_unknown)
    assert handle.wait(timeout=2).status == ReleaseStatus.COMPLETE
    assert client.unload_calls == [("model-a", 60.0)]


def test_missing_exact_router_target_is_terminal_scoped_noop() -> None:
    process = FakeProcessController()
    client = FakeRouterClient([{"id": "model-b", "state": "loaded"}])
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    lease = service.begin_generation(model_id="model-a", release_after=True)
    handle = lease.release_handle
    assert handle is not None

    service.finish_generation(lease)
    result = handle.wait(timeout=2)

    assert result.status == ReleaseStatus.NOOP
    assert result.released_model_states == (("model-a", "not_loaded"),)
    assert client.unload_calls == []
    assert process.stop_calls == 0


def test_router_scoped_failure_never_stops_router_as_fallback() -> None:
    process = FakeProcessController()
    client = FakeRouterClient([{"id": "model-a", "state": "loaded"}])
    secret = "RAW_SCOPED_RESPONSE_BODY_SECRET"
    client.failure = TimeoutError(secret)
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    lease = service.begin_generation(model_id="model-a", release_after=True)
    handle = lease.release_handle
    assert handle is not None

    service.finish_generation(lease)
    result = handle.wait(timeout=2)

    assert result.status == ReleaseStatus.FAILED
    assert result.fallback_used is False
    assert result.error == "scoped release failed (TimeoutError)"
    assert secret not in str(result.as_dict())
    assert secret not in str(service.diagnostics())
    assert process.stop_calls == 0
    assert process.is_running is True
    assert service.mode == RuntimeMode.ROUTER


def test_router_scoped_mismatched_terminal_identity_fails_closed() -> None:
    process = FakeProcessController()
    client = FakeRouterClient([{"id": "model-a", "state": "loaded"}])
    client.return_model_id = "model-b"
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    lease = service.begin_generation(model_id="model-a", release_after=True)
    handle = lease.release_handle
    assert handle is not None

    service.finish_generation(lease)
    result = handle.wait(timeout=2)

    assert result.status == ReleaseStatus.FAILED
    assert result.error == "router barrier returned a mismatched exact model identity"
    assert "model-b" not in (result.error or "")
    assert result.fallback_used is False
    assert process.stop_calls == 0
    assert service.mode == RuntimeMode.ROUTER


@pytest.mark.parametrize("case", ["duplicate", "unknown", "nonterminal"])
def test_router_scoped_invalid_terminal_evidence_never_uses_process_fallback(
    case: str,
) -> None:
    process = FakeProcessController()
    models = [{"id": "model-a", "state": "loaded"}]
    if case == "duplicate":
        models.append({"id": "model-a", "state": "loaded"})
    elif case == "unknown":
        models[0]["state"] = "unknown"
    client = FakeRouterClient(models)
    if case == "nonterminal":
        client.terminal_state = "loaded"
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    lease = service.begin_generation(model_id="model-a", release_after=True)
    handle = lease.release_handle
    assert handle is not None

    service.finish_generation(lease)
    result = handle.wait(timeout=2)

    assert result.status == ReleaseStatus.FAILED
    assert result.fallback_used is False
    assert process.stop_calls == 0
    assert process.is_running is True
    assert service.mode == RuntimeMode.ROUTER
    if case in {"duplicate", "unknown"}:
        assert client.unload_calls == []
    else:
        assert client.unload_calls == [("model-a", 60.0)]


def test_runtime_epoch_mismatch_fails_scoped_release_without_mutation() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    lease = service.begin_generation(release_after=True)
    handle = lease.release_handle
    assert handle is not None

    # Reconfiguration is normally blocked by the active lease.  Mutating the
    # private epoch simulates a stale token defensively reaching finish_generation.
    with service._state_lock:
        service._runtime_epoch += 1
    service.finish_generation(lease)
    result = handle.wait(timeout=0)

    assert result.status == ReleaseStatus.FAILED
    assert "epoch changed" in (result.error or "")
    assert process.stop_calls == 0
    assert service.mode == RuntimeMode.DIRECT


def test_worker_rechecks_epoch_after_queue_before_mutation() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    lease = service.begin_generation(release_after=True)
    handle = lease.release_handle
    assert handle is not None

    with service._operation_lock:
        service.finish_generation(lease)
        with service._state_lock:
            service._runtime_epoch += 1

    result = handle.wait(timeout=2)
    assert result.status == ReleaseStatus.FAILED
    assert "epoch changed" in (result.error or "")
    assert process.stop_calls == 0
    assert service.mode == RuntimeMode.DIRECT


def test_native_global_release_supersedes_dormant_scoped_router_operation() -> None:
    process = FakeProcessController()
    client = FakeRouterClient(
        [
            {"id": "model-a", "state": "loaded"},
            {"id": "model-b", "state": "loaded"},
        ]
    )
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    lease_a = service.begin_generation(model_id="model-a", release_after=True)
    lease_b = service.begin_generation(model_id="model-b")
    handle = lease_a.release_handle
    assert handle is not None

    deferred = service.request_release(source="comfy_free")
    assert deferred.status == ReleaseStatus.DEFERRED
    assert deferred.operation_id is not None
    service.finish_generation(lease_a)
    assert handle.done is False
    service.finish_generation(lease_b)

    result = handle.wait(timeout=2)
    assert result.status == ReleaseStatus.COMPLETE
    assert result.scope == ReleaseScope.ROUTER_MODEL
    assert result.target_model == "model-a"
    assert result.superseded_by == deferred.operation_id
    assert result.released_models == ("model-a",)
    assert client.unload_calls == [("model-a", 60.0), ("model-b", 60.0)]
    assert process.stop_calls == 0
    assert service.mode == RuntimeMode.ROUTER
    assert service.diagnostics()["last_release"]["operation_id"] == deferred.operation_id


def test_pending_global_release_keeps_one_operation_id_across_deferred_callers() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    lease = service.begin_generation()

    first = service.request_release(source="first")
    second = service.request_release(source="second")
    assert first.status == second.status == ReleaseStatus.DEFERRED
    assert first.request_id != second.request_id
    assert first.operation_id == second.operation_id

    service.finish_generation(lease)
    terminal = service.diagnostics()["last_release"]
    assert terminal["status"] == ReleaseStatus.COMPLETE.value
    assert terminal["operation_id"] == first.operation_id
    assert terminal["source"] == "first+second"
    assert process.stop_calls == 1


def test_global_superseded_handle_signals_after_global_release_listener() -> None:
    process = FakeProcessController()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_direct_owned()
    listener_entered = threading.Event()
    listener_gate = threading.Event()

    def listener(result) -> None:
        assert result.scope == ReleaseScope.GLOBAL
        listener_entered.set()
        assert _wait_for_event(listener_gate, timeout=5)

    service.add_release_listener(listener)
    lease = service.begin_generation(release_after=True)
    handle = lease.release_handle
    assert handle is not None
    deferred = service.request_release(source="comfy_free")
    assert deferred.status == ReleaseStatus.DEFERRED

    finisher = threading.Thread(target=service.finish_generation, args=(lease,))
    finisher.start()
    assert listener_entered.wait(timeout=2)
    assert handle.done is False
    assert finisher.is_alive()

    listener_gate.set()
    finisher.join(timeout=2)
    assert finisher.is_alive() is False
    result = handle.wait(timeout=2)
    assert result.status == ReleaseStatus.COMPLETE
    assert result.superseded_by == deferred.operation_id
    assert process.stop_calls == 1


def test_scoped_router_operations_share_one_serial_worker() -> None:
    process = FakeProcessController()
    client = FakeRouterClient(
        [
            {"id": "model-a", "state": "loaded"},
            {"id": "model-b", "state": "loaded"},
        ]
    )
    client.unload_entered = threading.Event()
    client.unload_gate = threading.Event()
    service = RuntimeService(process)  # type: ignore[arg-type]
    service.configure_router_owned(client)
    lease_a = service.begin_generation(model_id="model-a", release_after=True)
    lease_b = service.begin_generation(model_id="model-b", release_after=True)
    handle_a = lease_a.release_handle
    handle_b = lease_b.release_handle
    assert handle_a is not None
    assert handle_b is not None

    service.finish_generation(lease_a)
    service.finish_generation(lease_b)
    assert client.unload_entered.wait(timeout=2)
    time.sleep(0.05)
    assert len(client.unload_calls) == 1
    assert service._scoped_worker is not None

    client.unload_gate.set()
    assert handle_a.wait(timeout=2).status == ReleaseStatus.COMPLETE
    assert handle_b.wait(timeout=2).status == ReleaseStatus.COMPLETE
    assert [model_id for model_id, _ in client.unload_calls] == ["model-a", "model-b"]


def test_release_after_rejects_attached_and_unconfigured_runtimes() -> None:
    attached_process = FakeProcessController(running=False)
    attached = RuntimeService(attached_process)  # type: ignore[arg-type]
    attached.configure_attached()
    with pytest.raises(RuntimeError, match="requires an owned"):
        attached.begin_generation(release_after=True)
    assert attached.active_generations == 0

    unconfigured = RuntimeService(FakeProcessController(running=False))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="requires an owned"):
        unconfigured.begin_generation(release_after=True)
    assert unconfigured.active_generations == 0


def test_runtime_epoch_advances_on_each_successful_runtime_identity_change() -> None:
    service = RuntimeService(FakeProcessController(running=False))  # type: ignore[arg-type]
    assert service.runtime_epoch == 0

    service.configure_attached()
    assert service.runtime_epoch == 1
    service.clear_runtime()
    assert service.runtime_epoch == 2


def test_finish_generation_rejects_reused_token() -> None:
    service = RuntimeService(FakeProcessController())  # type: ignore[arg-type]
    service.configure_direct_owned()
    lease = service.begin_generation()
    service.finish_generation(lease)

    with pytest.raises(RuntimeError, match="already finished"):
        service.finish_generation(lease)
