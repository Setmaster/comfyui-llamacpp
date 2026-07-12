from __future__ import annotations

import json
import unittest
import uuid

from runtime.client import ConnectionConfig, StreamControl
from runtime.live_generation import (
    EMIT_INTERVAL_SECONDS,
    LIVE_EVENT,
    MAX_EVENT_BYTES,
    MAX_PREVIEW_BYTES,
    CancelScope,
    ExecutionIdentity,
    LiveGenerationCapacityError,
    LiveGenerationRegistry,
)
from runtime.streaming import PromptProgress, StreamResult, StreamUpdate


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.now = value

    def __call__(self) -> float:
        return self.now

    def advance(self, value: float) -> None:
        self.now += value


class FakeTimer:
    def __init__(self, delay: float, callback) -> None:
        self.delay = delay
        self.callback = callback
        self.cancelled = False
        self.fired = False

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        if self.cancelled:
            return
        self.fired = True
        self.callback()


class FakeTimers:
    def __init__(self) -> None:
        self.items: list[FakeTimer] = []

    def __call__(self, delay: float, callback) -> FakeTimer:
        timer = FakeTimer(delay, callback)
        self.items.append(timer)
        return timer


def identity(
    *,
    client_id: str | None = "browser-one",
    node_id: str = "12",
    prompt_id: str = "00000000-0000-4000-8000-000000000001",
    workflow_id: str | None = "workflow-one",
) -> ExecutionIdentity:
    return ExecutionIdentity.create(
        prompt_id=prompt_id,
        node_id=node_id,
        display_node_id=f"display-{node_id}",
        real_node_id=f"real-{node_id}",
        parent_node_id="parent" if node_id != "12" else None,
        list_index=2,
        workflow_id=workflow_id,
        client_id=client_id,
    )


class LiveGenerationTests(unittest.TestCase):
    def make_registry(self, *, max_active: int = 32):
        self.events = []
        self.clock = FakeClock()
        self.wall = FakeClock(1000.0)
        self.timers = FakeTimers()

        def sender(name, payload, client_id):
            self.events.append((self.clock.now, name, payload, client_id))

        return LiveGenerationRegistry(
            sender=sender,
            clock=self.clock,
            wall_clock=self.wall,
            timer_factory=self.timers,
            max_active=max_active,
        )

    def test_identity_is_uuid4_bounded_and_never_exposes_client_id(self) -> None:
        item = identity()
        self.assertEqual(item.execution_id.version, 4)
        self.assertNotIn("browser-one", repr(item))
        payload = item.as_dict()
        self.assertNotIn("client_id", payload)
        self.assertEqual(payload["display_node_id"], "display-12")
        self.assertEqual(payload["list_index"], 2)
        self.assertEqual(payload["workflow_id"], "workflow-one")

        with self.assertRaises(TypeError):
            ExecutionIdentity(  # type: ignore[arg-type]
                "not-a-uuid",
                None,
                "1",
                "1",
                "1",
            )
        with self.assertRaises(ValueError):
            ExecutionIdentity(uuid.uuid1(), None, "1", "1", "1")
        with self.assertRaises(ValueError):
            ExecutionIdentity.create(prompt_id=None, node_id=None)

    def test_begin_targets_one_client_and_restore_is_same_client_only(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity())

        self.assertEqual(registry.active_count, 1)
        self.assertEqual(len(self.events), 1)
        _, name, payload, client_id = self.events[0]
        self.assertEqual(name, LIVE_EVENT)
        self.assertEqual(client_id, "browser-one")
        self.assertEqual(payload["phase"], "starting")
        self.assertEqual(payload["sequence"], 1)
        self.assertEqual(payload["started_at_ms"], 1_000_000)
        self.assertEqual(len(registry.active_for_client("browser-one")), 1)
        self.assertEqual(registry.active_for_client("browser-two"), [])
        self.assertTrue(handle.active)

    def test_execution_start_order_stays_monotonic_when_wall_clock_regresses(self) -> None:
        registry = self.make_registry()
        first = registry.begin(identity(node_id="1"))
        first_started = registry.snapshot(first.identity.execution_id).started_at_ms
        self.wall.now -= 100.0
        second = registry.begin(identity(node_id="2"))
        second_started = registry.snapshot(second.identity.execution_id).started_at_ms
        self.assertGreater(second_started, first_started)

    def test_headless_execution_never_broadcasts_but_retains_bounded_state(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity(client_id=None))
        handle.update(response_delta="headless")
        self.assertEqual(self.events, [])
        self.assertEqual(registry.active_count, 1)
        self.assertEqual(registry.active_for_client("browser-one"), [])

    def test_utf8_tails_and_json_payload_are_strictly_bounded(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity())
        value = ("🙂\x00\n" * 30_000) + "TAIL"
        handle.update(response_delta=value, thinking_delta=value)

        snapshot = registry.snapshot(handle.identity.execution_id)
        self.assertIsNotNone(snapshot)
        self.assertLessEqual(snapshot.response.bytes, MAX_PREVIEW_BYTES)
        self.assertLessEqual(snapshot.thinking.bytes, MAX_PREVIEW_BYTES)
        self.assertTrue(snapshot.response.truncated)
        self.assertTrue(snapshot.response.text.endswith("TAIL"))
        snapshot.response.text.encode("utf-8", errors="strict")
        payload = snapshot.as_dict()
        encoded = json.dumps({"type": LIVE_EVENT, "data": payload}, ensure_ascii=True).encode()
        self.assertLess(len(encoded), MAX_EVENT_BYTES)
        self.assertTrue(payload["response"]["truncated"])
        self.assertTrue(payload["thinking"]["truncated"])

    def test_updates_coalesce_at_eight_hz_and_terminal_flush_cancels_timer(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity())
        for index in range(100):
            handle.update(response_delta=str(index))

        self.assertEqual(len(self.events), 1)
        self.assertEqual(len(self.timers.items), 1)
        timer = self.timers.items[-1]
        self.assertAlmostEqual(timer.delay, EMIT_INTERVAL_SECONDS)

        self.clock.advance(EMIT_INTERVAL_SECONDS)
        timer.fire()
        self.assertEqual(len(self.events), 2)
        self.assertGreater(self.events[-1][2]["sequence"], self.events[0][2]["sequence"])

        handle.update(response_delta="pending")
        pending = self.timers.items[-1]
        terminal = handle.finish(phase="complete")
        self.assertIsNotNone(terminal)
        self.assertTrue(pending.cancelled)
        self.assertEqual(len(self.events), 3)
        self.assertTrue(self.events[-1][2]["terminal"])
        self.assertEqual(self.events[-1][2]["phase"], "complete")
        self.assertFalse(handle.active)
        pending.fire()
        self.assertEqual(len(self.events), 3)

    def test_stale_timer_cannot_clear_or_emit_in_place_of_a_newer_timer(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity())
        handle.update(response_delta="one")
        stale = self.timers.items[-1]

        self.clock.advance(EMIT_INTERVAL_SECONDS)
        handle.update(response_delta="two")
        self.assertTrue(stale.cancelled)
        self.assertEqual(len(self.events), 2)

        handle.update(response_delta="three")
        current = self.timers.items[-1]
        stale.callback()  # emulate a timer callback already racing with cancel()
        self.assertEqual(len(self.events), 2)

        self.clock.advance(EMIT_INTERVAL_SECONDS)
        current.fire()
        self.assertEqual(len(self.events), 3)
        self.assertTrue(self.events[-1][2]["response"]["text"].endswith("three"))

    def test_timer_factory_failure_is_fail_open(self) -> None:
        events = []
        clock = FakeClock()
        registry = LiveGenerationRegistry(
            sender=lambda *event: events.append(event),
            clock=clock,
            wall_clock=FakeClock(1000.0),
            timer_factory=lambda *_args: (_ for _ in ()).throw(RuntimeError("no timer")),
        )
        handle = registry.begin(identity())
        snapshot = handle.update(response_delta="still valid")
        self.assertEqual(snapshot.response.text, "still valid")
        self.assertEqual(len(events), 1)
        clock.advance(EMIT_INTERVAL_SECONDS)
        handle.update(response_delta=" and visible")
        self.assertEqual(len(events), 2)

    def test_stream_updates_drive_phase_progress_usage_and_monotonic_sequence(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity())
        start_sequence = registry.snapshot(handle.identity.execution_id).sequence

        loading = handle.on_stream_update(
            StreamUpdate(
                prompt_progress=PromptProgress(100, 10, 25, 40),
                model="model-one",
                chunk_index=1,
            )
        )
        self.assertEqual(loading.phase, "loading")
        self.assertEqual(loading.prompt_progress.percent, 25.0)
        self.assertGreater(loading.sequence, start_sequence)

        reasoning = handle.on_stream_update(StreamUpdate(thinking="think", chunk_index=2))
        self.assertEqual(reasoning.phase, "reasoning")
        generated = handle.on_stream_update(
            StreamUpdate(
                content="answer",
                finish_reason="stop",
                usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
                chunk_index=3,
            )
        )
        self.assertEqual(generated.phase, "generating")
        self.assertEqual(generated.response.text, "answer")
        self.assertEqual(generated.thinking.text, "think")
        self.assertEqual(generated.usage["total_tokens"], 4)
        self.assertEqual(generated.finish_reason, "stop")
        self.assertEqual(generated.chunks, 3)

    def test_record_and_finish_replace_preview_with_exact_stream_result(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity())
        handle.update(response_delta="old", thinking_delta="old")
        result = StreamResult(
            " final ",
            " thought ",
            True,
            finish_reason="stop",
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            model="model",
            chunks=4,
            prompt_progress=PromptProgress(2, 0, 2, 5),
        )
        recorded = handle.record_result(result)
        self.assertEqual(recorded.response.text, " final ")
        self.assertEqual(recorded.thinking.text, " thought ")

        terminal = handle.finish(
            phase="complete",
            result=result,
            release={"status": "complete", "terminal": True, "scope": "direct"},
        )
        self.assertEqual(terminal.response.text, " final ")
        self.assertEqual(terminal.release["scope"], "direct")
        self.assertFalse(terminal.cancel_enabled)

    def test_local_cancel_is_exact_idempotent_and_sets_token_before_callback(self) -> None:
        registry = self.make_registry()
        calls = []
        handle = None

        def cancel_upstream() -> bool:
            calls.append(handle.token.cancelled)
            return True

        handle = registry.begin(
            identity(),
            cancel_scope=CancelScope.GENERATION,
            cancel_callback=cancel_upstream,
        )
        mismatched = registry.cancel(
            handle.identity.execution_id,
            prompt_id="different-prompt",
        )
        self.assertFalse(mismatched.accepted)
        self.assertEqual(calls, [])

        result = registry.cancel(
            str(handle.identity.execution_id),
            prompt_id=handle.identity.prompt_id,
            node_id=handle.identity.node_id,
        )
        self.assertTrue(result.accepted)
        self.assertTrue(result.upstream_confirmed)
        self.assertEqual(result.scope, CancelScope.GENERATION)
        self.assertEqual(calls, [True])
        self.assertTrue(handle.token.cancelled)
        self.assertEqual(registry.snapshot(handle.identity.execution_id).phase, "cancelling")
        self.assertFalse(registry.snapshot(handle.identity.execution_id).cancel_enabled)

        late = handle.on_stream_update(StreamUpdate(content="late token", chunk_index=1))
        self.assertEqual(late.phase, "cancelling")

        repeated = registry.cancel(handle.identity.execution_id)
        self.assertTrue(repeated.accepted)
        self.assertEqual(repeated.status, "already_requested")
        self.assertEqual(calls, [True])

    def test_client_scoped_cancel_cannot_cross_client_boundaries(self) -> None:
        registry = self.make_registry()
        calls = []
        handle = registry.begin(
            identity(client_id="browser-one"),
            cancel_scope=CancelScope.GENERATION,
            cancel_callback=lambda: calls.append("delete") or True,
        )

        denied = registry.cancel_for_client(handle.identity.execution_id, "browser-two")
        self.assertFalse(denied.accepted)
        self.assertEqual(denied.status, "not_active")
        self.assertEqual(calls, [])

        accepted = registry.cancel_for_client(handle.identity.execution_id, "browser-one")
        self.assertTrue(accepted.accepted)
        self.assertTrue(accepted.upstream_confirmed)
        self.assertEqual(calls, ["delete"])

        with self.assertRaises(ValueError):
            registry.cancel(uuid.uuid1())
        with self.assertRaises(ValueError):
            registry.cancel(str(handle.identity.execution_id).upper())

    def test_prompt_scope_is_never_misrepresented_as_local_cancel(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity(), cancel_scope=CancelScope.PROMPT)
        snapshot = registry.snapshot(handle.identity.execution_id)
        self.assertEqual(snapshot.cancel_scope, CancelScope.PROMPT)
        self.assertEqual(snapshot.as_dict()["cancel"]["label"], "Stop Comfy job")

        result = registry.cancel(handle.identity.execution_id)
        self.assertFalse(result.accepted)
        self.assertEqual(result.scope, CancelScope.PROMPT)
        self.assertEqual(result.status, "whole_job_required")
        self.assertFalse(handle.token.cancelled)

    def test_cancel_callback_failure_reports_only_type_and_generation_stays_active(self) -> None:
        registry = self.make_registry()

        def fail() -> bool:
            raise RuntimeError("secret backend detail")

        handle = registry.begin(
            identity(),
            cancel_scope=CancelScope.GENERATION,
            cancel_callback=fail,
        )
        result = registry.cancel(handle.identity.execution_id)
        self.assertTrue(result.accepted)
        self.assertFalse(result.upstream_confirmed)
        self.assertEqual(result.error_type, "RuntimeError")
        self.assertNotIn("secret", str(result.as_dict()))
        self.assertTrue(handle.active)

    def test_cancel_can_upgrade_after_probe_and_is_disabled_during_release(self) -> None:
        registry = self.make_registry()
        calls = []
        handle = registry.begin(identity(), cancel_scope=CancelScope.PROMPT)
        upgraded = handle.set_cancel(
            CancelScope.GENERATION,
            lambda: calls.append("delete") or True,
        )
        self.assertEqual(upgraded.cancel_scope, CancelScope.GENERATION)
        self.assertEqual(upgraded.as_dict()["cancel"]["label"], "Stop generation")

        disabled = handle.set_cancel(CancelScope.NONE)
        self.assertEqual(disabled.cancel_scope, CancelScope.NONE)
        releasing = handle.set_phase("releasing")
        self.assertFalse(releasing.cancel_enabled)
        result = registry.cancel(handle.identity.execution_id)
        self.assertFalse(result.accepted)
        self.assertEqual(result.status, "whole_job_required")
        self.assertEqual(calls, [])

        automatic = registry.begin(
            identity(node_id="13"),
            cancel_scope=CancelScope.GENERATION,
            cancel_callback=lambda: calls.append("late-delete") or True,
        )
        releasing = automatic.set_phase("releasing")
        self.assertEqual(releasing.cancel_scope, CancelScope.NONE)
        self.assertFalse(releasing.cancel_enabled)
        self.assertFalse(registry.cancel(automatic.identity.execution_id).accepted)
        self.assertEqual(calls, [])

    def test_terminal_snapshot_surfaces_redacted_stream_cleanup_outcome(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity())
        control = StreamControl(
            ConnectionConfig(),
            uuid.uuid4(),
            _delete=lambda _identity, _timeout: True,
        )
        self.assertTrue(control.delete())
        result = StreamResult("done", "", True, stream_cleanup=control.cleanup.snapshot())

        terminal = handle.finish(
            phase="complete",
            result=result,
            error={"message": "Authorization: Bearer should-not-leak"},
        )
        payload = terminal.as_dict()
        self.assertEqual(
            payload["stream_cleanup"],
            {
                "attempts": 1,
                "confirmed": True,
                "failures": 0,
                "last_error_type": None,
            },
        )
        self.assertNotIn("should-not-leak", json.dumps(payload))

    def test_terminal_snapshot_accepts_cleanup_evidence_without_a_stream_result(self) -> None:
        registry = self.make_registry()
        handle = registry.begin(identity())
        control = StreamControl(
            ConnectionConfig(),
            uuid.uuid4(),
            _delete=lambda _identity, _timeout: True,
        )
        self.assertTrue(control.delete())

        terminal = handle.finish(
            phase="cancelled",
            stream_cleanup=control.cleanup.snapshot(),
            error={"category": "cancelled", "message": "Comfy job interrupted"},
        )

        self.assertEqual(
            terminal.as_dict()["stream_cleanup"],
            {
                "attempts": 1,
                "confirmed": True,
                "failures": 0,
                "last_error_type": None,
            },
        )

    def test_registry_capacity_and_restore_count_are_bounded(self) -> None:
        registry = self.make_registry(max_active=2)
        first = registry.begin(identity(node_id="1"))
        registry.begin(identity(node_id="2"))
        with self.assertRaises(LiveGenerationCapacityError):
            registry.begin(identity(node_id="3"))
        self.assertEqual(registry.active_count, 2)

        first.finish(phase="complete")
        registry.begin(identity(node_id="3"))
        restored = registry.active_for_client("browser-one", limit=999)
        self.assertEqual(len(restored), 2)

        with self.assertRaises(ValueError):
            LiveGenerationRegistry(max_active=True)

    def test_sender_failure_is_fail_open(self) -> None:
        registry = LiveGenerationRegistry(
            sender=lambda *_args: (_ for _ in ()).throw(RuntimeError())
        )
        handle = registry.begin(identity())
        handle.update(response_delta="still running")
        self.assertTrue(handle.active)


if __name__ == "__main__":
    unittest.main()
