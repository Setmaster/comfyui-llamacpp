from __future__ import annotations

import threading

import pytest

import runtime.manager as manager_module
from runtime.capabilities import ServerCapabilities
from runtime.client import (
    HealthStatus,
    ModelOperationResult,
    ModelState,
    RouterModel,
    ServerProps,
)
from runtime.config import RouterConfig, ServerConfig
from runtime.manager import LlamaCppServerManager, ServerStatus, get_server_manager
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
        self.start_entered = None
        self.start_gate = None
        self.stop_entered = None
        self.stop_gate = None

    @property
    def is_running(self):
        return self.running

    @property
    def has_owned_process(self):
        return self.owned

    def start(self, command, **kwargs):
        if self.start_entered is not None:
            self.start_entered.set()
        if self.start_gate is not None:
            assert self.start_gate.wait(timeout=5)
        self.command = list(command)
        self.running = True
        self.owned = True
        self.state = ProcessLifecycle.RUNNING
        return self.snapshot()

    def stop(self, **kwargs):
        self.stop_calls += 1
        if self.stop_entered is not None:
            self.stop_entered.set()
        if self.stop_gate is not None:
            assert self.stop_gate.wait(timeout=5)
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
        self.load_calls = []
        self.models_kwargs = []
        self.models_entered = None
        self.models_gate = None
        self.load_entered = None
        self.load_gate = None
        self.unload_entered = None
        self.unload_gate = None

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
        self.models_kwargs.append(dict(kwargs))
        if kwargs.get("reload") and self.models_entered is not None:
            self.models_entered.set()
        if kwargs.get("reload") and self.models_gate is not None:
            assert self.models_gate.wait(timeout=5)
        return tuple(self.model_records)

    list_models = models

    def load_model(self, model_id, **kwargs):
        self.load_calls.append(model_id)
        if self.load_entered is not None:
            self.load_entered.set()
        if self.load_gate is not None:
            assert self.load_gate.wait(timeout=5)
        model = RouterModel(model_id, ModelState.LOADED)
        return ModelOperationResult(model, True, True, 0.1)

    def unload_model(self, model_id, **kwargs):
        self.unload_calls.append(model_id)
        if self.unload_entered is not None:
            self.unload_entered.set()
        if self.unload_gate is not None:
            assert self.unload_gate.wait(timeout=5)
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


def test_default_manager_construction_preserves_singleton_and_registers_once(monkeypatch):
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    listener_registrations = []
    cleanup_registrations = []
    add_release_listener = service.add_release_listener

    def record_listener(listener):
        listener_registrations.append(listener)
        add_release_listener(listener)

    monkeypatch.setattr(manager_module, "_SERVER_MANAGER", None)
    monkeypatch.setattr(manager_module, "get_runtime_service", lambda: service)
    monkeypatch.setattr(service, "add_release_listener", record_listener)
    monkeypatch.setattr(manager_module.atexit, "register", cleanup_registrations.append)

    first = LlamaCppServerManager()
    second = LlamaCppServerManager()
    from_getter = get_server_manager()

    assert first is second is from_getter
    assert len(listener_registrations) == 1
    assert len(cleanup_registrations) == 1


def test_dependency_injected_manager_construction_remains_independent():
    first_service = RuntimeService(FakeProcess())  # type: ignore[arg-type]
    second_service = RuntimeService(FakeProcess())  # type: ignore[arg-type]

    first = LlamaCppServerManager(runtime_service=first_service)
    second = LlamaCppServerManager(runtime_service=second_service)

    assert first is not second
    assert first.runtime_service is first_service
    assert second.runtime_service is second_service


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


def test_symbolic_gpu_layers_are_rejected_for_integer_only_binary(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: FakeClient(connection, model_path=str(model)),
    )

    success, error = manager.start(ServerConfig(str(model), n_gpu_layers="auto"), timeout=2)

    assert success is False
    assert "does not advertise symbolic --gpu-layers" in error
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
    assert manager.capabilities is None
    assert manager.is_router_mode is False


def test_failed_replacement_preflight_preserves_healthy_owned_server(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    missing = tmp_path / "missing.gguf"
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

    assert manager.start(ServerConfig(str(model)), timeout=2) == (True, None)
    original_client = manager.client

    success, error = manager.start(ServerConfig(str(missing)), timeout=2)

    assert success is False
    assert "Model file not found" in error
    assert process.stop_calls == 0
    assert process.is_running is True
    assert manager.status == ServerStatus.RUNNING
    assert manager.current_config == ServerConfig(str(model))
    assert manager.client is original_client
    assert original_client.closed is False


@pytest.mark.parametrize(
    ("bind_host", "expected_url"),
    [
        ("0.0.0.0", "http://127.0.0.1:8080"),
        ("*", "http://127.0.0.1:8080"),
        ("::", "http://[::1]:8080"),
        ("0:0:0:0:0:0:0:0", "http://[::1]:8080"),
        ("::1", "http://[::1]:8080"),
    ],
)
def test_bind_hosts_produce_valid_loopback_connect_urls(
    tmp_path,
    monkeypatch,
    bind_host,
    expected_url,
):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: FakeClient(connection, model_path=str(model)),
    )

    assert manager.start(ServerConfig(str(model), host=bind_host), timeout=2) == (True, None)
    assert manager.server_url == expected_url


def test_equivalent_loopback_urls_retain_managed_generation_leases(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: FakeClient(connection, model_path=str(model)),
    )
    assert manager.start(ServerConfig(str(model), host="127.0.0.1"), timeout=2)[0]

    for url in (
        "http://127.0.0.1:8080",
        "http://localhost:8080/",
        "http://[::1]:8080",
    ):
        connection, managed = manager.connection_for(url)
        assert connection.base_url == url.rstrip("/")
        assert managed is True

    _, managed = manager.connection_for("http://localhost:8081")
    assert managed is False


@pytest.mark.parametrize(
    "host",
    ["/tmp/llama.sock", "llama.sock", "unix:/tmp/llama.sock", "http+unix:x"],
)
def test_unix_socket_hosts_are_rejected_before_spawn(tmp_path, monkeypatch, host):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: FakeClient(connection, model_path=str(model)),
    )

    success, error = manager.start(ServerConfig(str(model), host=host), timeout=2)

    assert success is False
    assert "Unix-socket hosts are not supported" in error
    assert process.has_owned_process is False


def test_model_list_reload_is_forwarded_to_router_client(tmp_path, monkeypatch):
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    client = FakeClient(None, role="router")
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: client,
    )
    assert manager.start_router(RouterConfig(str(tmp_path)), timeout=2)[0]

    success, _, error = manager.list_models(reload=True)

    assert (success, error) == (True, None)
    assert client.models_kwargs[-1] == {"reload": True}


def test_model_list_reload_rejects_before_request_during_generation(tmp_path, monkeypatch):
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    client = FakeClient(None, role="router")
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: client,
    )
    assert manager.start_router(RouterConfig(str(tmp_path)), timeout=2)[0]

    with manager.generation_lease(managed=True):
        success, models, error = manager.list_models(reload=True)

    assert success is False
    assert models is None
    assert "generation request" in error
    assert {"reload": True} not in client.models_kwargs


def test_concurrent_model_catalog_reloads_are_serialized(tmp_path, monkeypatch):
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    client = FakeClient(None, role="router")
    client.models_entered = threading.Event()
    client.models_gate = threading.Event()
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: client,
    )
    assert manager.start_router(RouterConfig(str(tmp_path)), timeout=2)[0]
    results = []
    first = threading.Thread(target=lambda: results.append(manager.list_models(reload=True)))
    second = threading.Thread(target=lambda: results.append(manager.list_models(reload=True)))

    first.start()
    assert client.models_entered.wait(timeout=5)
    second.start()
    second.join(timeout=0.1)

    assert second.is_alive()
    assert client.models_kwargs.count({"reload": True}) == 1

    client.models_gate.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(results) == 2
    assert all(result == (True, [], None) for result in results)
    assert client.models_kwargs.count({"reload": True}) == 2


def test_native_release_waits_for_startup_then_stops_new_runtime(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    process = FakeProcess()
    process.start_entered = threading.Event()
    process.start_gate = threading.Event()
    service = RuntimeService(process)  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: FakeClient(connection, model_path=str(model)),
    )
    results = {}
    starter = threading.Thread(
        target=lambda: results.setdefault("start", manager.start(ServerConfig(str(model))))
    )
    releaser = threading.Thread(
        target=lambda: results.setdefault("release", service.request_release(source="comfy_free"))
    )

    starter.start()
    assert process.start_entered.wait(timeout=5)
    releaser.start()
    releaser.join(timeout=0.1)
    assert releaser.is_alive()

    process.start_gate.set()
    starter.join(timeout=5)
    releaser.join(timeout=5)

    assert results["start"] == (True, None)
    assert results["release"].status == ReleaseStatus.COMPLETE
    assert process.stop_calls == 1
    assert manager.status == ServerStatus.STOPPED


def test_native_release_waits_for_explicit_stop_without_state_corruption(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fixture")
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: FakeClient(connection, model_path=str(model)),
    )
    assert manager.start(ServerConfig(str(model)), timeout=2)[0]
    process.stop_entered = threading.Event()
    process.stop_gate = threading.Event()
    results = {}
    stopper = threading.Thread(target=lambda: results.setdefault("stop", manager.stop()))
    releaser = threading.Thread(
        target=lambda: results.setdefault("release", service.request_release(source="comfy_free"))
    )

    stopper.start()
    assert process.stop_entered.wait(timeout=5)
    releaser.start()
    releaser.join(timeout=0.1)
    assert releaser.is_alive()

    process.stop_gate.set()
    stopper.join(timeout=5)
    releaser.join(timeout=5)

    assert results["stop"] == (True, None)
    assert results["release"].status == ReleaseStatus.NOOP
    assert process.stop_calls == 1
    assert service.mode == RuntimeMode.NONE


def test_native_release_serializes_behind_router_load(tmp_path, monkeypatch):
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    client = FakeClient(None, role="router")
    client.model_records = (RouterModel("model-a", ModelState.LOADED),)
    client.load_entered = threading.Event()
    client.load_gate = threading.Event()
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: client,
    )
    assert manager.start_router(RouterConfig(str(tmp_path)), timeout=2)[0]
    results = {}
    loader = threading.Thread(
        target=lambda: results.setdefault("load", manager.load_model("model-a"))
    )
    releaser = threading.Thread(
        target=lambda: results.setdefault("release", service.request_release(source="comfy_free"))
    )

    loader.start()
    assert client.load_entered.wait(timeout=5)
    releaser.start()
    releaser.join(timeout=0.1)
    assert releaser.is_alive()

    client.load_gate.set()
    loader.join(timeout=5)
    releaser.join(timeout=5)

    assert results["load"] == (True, None)
    assert results["release"].status == ReleaseStatus.COMPLETE
    assert client.unload_calls == ["model-a"]
    assert process.stop_calls == 0


def test_explicit_mutations_reject_before_change_during_generation(tmp_path, monkeypatch):
    process = FakeProcess()
    service = RuntimeService(process)  # type: ignore[arg-type]
    client = FakeClient(None, role="router")
    client.model_records = (RouterModel("model-a", ModelState.LOADED),)
    monkeypatch.setattr("runtime.manager._port_is_bound", lambda host, port: False)
    manager = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda path: capabilities(tmp_path),
        client_factory=lambda connection: client,
    )
    config = RouterConfig(str(tmp_path))
    assert manager.start_router(config, timeout=2)[0]

    with manager.generation_lease(managed=True):
        outcomes = (
            manager.stop(),
            manager.start_router(config),
            manager.load_model("model-a"),
            manager.unload_model("model-a"),
        )

    assert all(success is False and "generation request" in error for success, error in outcomes)
    assert process.stop_calls == 0
    assert client.load_calls == []
    assert client.unload_calls == []
    assert manager.status == ServerStatus.RUNNING
