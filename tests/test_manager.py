from __future__ import annotations

from runtime.capabilities import ServerCapabilities
from runtime.client import (
    HealthStatus,
    ModelOperationResult,
    ModelState,
    RouterModel,
    ServerProps,
)
from runtime.config import RouterConfig, ServerConfig
from runtime.manager import LlamaCppServerManager, ServerStatus
from runtime.process import ProcessLifecycle, StopResult
from runtime.service import ReleaseStatus, RuntimeMode, RuntimeService


class FakeSnapshot:
    def __init__(self, process):
        self.state = process.state
        self.pid = 123 if process.owned else None
        self.create_time = 1.0 if process.owned else None
        self.process_group_id = 123 if process.owned else None
        self.windows_job_assigned = False
        self.descendant_fallback = False
        self.command = tuple(process.command)
        self.cwd = None
        self.started_at = 1.0 if process.owned else None
        self.stopped_at = None
        self.returncode = process.returncode
        self.last_error = process.error
        self.log_tail = tuple(process.logs)

    def as_dict(self):
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


class FakeProcess:
    def __init__(self):
        self.running = False
        self.owned = False
        self.state = ProcessLifecycle.STOPPED
        self.command = []
        self.returncode = None
        self.error = None
        self.logs = []
        self.stop_calls = 0

    @property
    def is_running(self):
        return self.running

    @property
    def has_owned_process(self):
        return self.owned

    def start(self, command, **kwargs):
        self.command = list(command)
        self.running = True
        self.owned = True
        self.state = ProcessLifecycle.RUNNING
        return self.snapshot()

    def stop(self, **kwargs):
        self.stop_calls += 1
        self.running = False
        self.owned = False
        self.state = ProcessLifecycle.STOPPED
        self.returncode = 0
        return StopResult(True, False, 0, 0.01)

    def snapshot(self):
        return FakeSnapshot(self)


class FakeClient:
    def __init__(self, connection, *, role=None, model_path=None):
        self.connection = connection
        self.role = role
        self.model_path = model_path
        self.closed = False
        self.model_records = ()
        self.unload_calls = []

    def close(self):
        self.closed = True

    def health(self, **kwargs):
        return HealthStatus(True, 200, "ok", None, {"status": "ok"})

    def props(self, **kwargs):
        return ServerProps(
            self.role,
            "fixture",
            self.model_path,
            "fixture-model",
            False,
            {},
            {"role": self.role} if self.role else {"model_path": self.model_path},
        )

    def models(self, **kwargs):
        return tuple(self.model_records)

    list_models = models

    def load_model(self, model_id, **kwargs):
        model = RouterModel(model_id, ModelState.LOADED)
        return ModelOperationResult(model, True, True, 0.1)

    def unload_model(self, model_id, **kwargs):
        self.unload_calls.append(model_id)
        model = RouterModel(model_id, ModelState.UNLOADED)
        return ModelOperationResult(model, True, True, 0.1)


def capabilities(tmp_path):
    return ServerCapabilities(
        path=str(tmp_path / "llama-server"),
        version_output="fixture b9999",
        help_output="",
        flags=frozenset(
            {
                "--models-dir",
                "--models-max",
                "--no-models-autoload",
                "--sleep-idle-seconds",
                "--api-key-file",
                "--media-path",
                "--mmproj",
                "--fit",
                "--flash-attn",
            }
        ),
        identity="fixture-identity",
    )


def test_direct_start_native_release_and_status(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    clients = []

    def client_factory(connection):
        client = FakeClient(connection, model_path=str(model))
        clients.append(client)
        return client

    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=client_factory,
    )

    success, error = manager.start(ServerConfig(str(model)), timeout=2)
    assert (success, error) == (True, None)
    assert manager.status == ServerStatus.RUNNING
    assert service.mode == RuntimeMode.DIRECT
    assert process.command[1:3] == ["-m", str(model)]

    release = service.request_release(source="comfy_free")
    assert release.status == ReleaseStatus.COMPLETE
    assert manager.status == ServerStatus.STOPPED
    assert clients[0].closed is True


def test_router_native_release_unloads_models_but_keeps_router(tmp_path, monkeypatch):
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    client = FakeClient(None, role="router")
    client.model_records = (
        RouterModel("resident", ModelState.LOADED),
        RouterModel("cold", ModelState.UNLOADED),
    )
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: client,
    )

    assert manager.start_router(RouterConfig(str(tmp_path)), timeout=2)[0]
    result = service.request_release(source="comfy_free")
    assert result.status == ReleaseStatus.COMPLETE
    assert client.unload_calls == ["resident"]
    assert process.stop_calls == 0
    assert manager.status == ServerStatus.RUNNING


def test_port_collision_is_rejected_without_adopting_process(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: True)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: FakeClient(connection, model_path=str(model)),
    )

    success, error = manager.start(ServerConfig(str(model)), timeout=2)
    assert success is False
    assert "refusing to adopt" in error
    assert process.has_owned_process is False


def test_wrong_ready_role_is_stopped_and_reported(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: FakeClient(
            connection, role="router", model_path=str(model)
        ),
    )

    success, error = manager.start(ServerConfig(str(model)), timeout=2)
    assert success is False
    assert "router, not the requested direct server" in error
    assert process.stop_calls == 1
    assert manager.status == ServerStatus.ERROR
