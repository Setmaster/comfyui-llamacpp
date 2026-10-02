from __future__ import annotations

import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from test_manager import FakeClient, FakeProcess, capabilities

import runtime.manager as manager_module
from runtime.config import RouterConfig
from runtime.manager import LlamaCppServerManager
from runtime.presets import PresetSnapshot, load_local_preset
from runtime.process import StopResult
from runtime.service import RuntimeService


@pytest.fixture
def preset(tmp_path):
    (tmp_path / "small.gguf").write_bytes(b"fixture")
    (tmp_path / "large.gguf").write_bytes(b"fixture")
    (tmp_path / "mmproj.gguf").write_bytes(b"fixture")
    source = tmp_path / "models.ini"
    source.write_text(
        "version = 1\n[*]\nthreads = 4\n[small]\nmodel = small.gguf\nc = 2048\n"
        "ngl = 2\n[large]\nmodel = large.gguf\nmmproj = mmproj.gguf\n"
        "ctx-size = 4096\nLLAMA_ARG_N_GPU_LAYERS = 8\n",
        encoding="utf-8",
    )
    return source


def test_local_paths_resolve_beside_source_and_provenance_is_content_based(preset):
    config = RouterConfig("", models_preset=str(preset))
    parsed = config.validated_preset
    assert parsed is not None
    assert parsed.models == ("small", "large")
    assert parsed.source_sha256 == sha256(preset.read_bytes()).hexdigest()
    assert f"model = {preset.parent / 'small.gguf'}" in parsed.text
    assert "gpu-layers = 8" in parsed.text
    assert "version" not in parsed.text  # avoid upstream's phantom default section
    public = config.effective_values()
    assert public["preset_sha256"] == parsed.source_sha256
    assert public["preset_models"] == ("small", "large")
    assert "_preset" not in public
    first = config.fingerprint()
    preset.write_text(preset.read_text().replace("2048", "3072"))
    refreshed = config.refresh_preset()
    assert refreshed.fingerprint() != first
    assert config.fingerprint() == first  # loaded provenance remains immutable


def test_override_and_inherit_have_deliberate_precedence(preset):
    config = RouterConfig(
        "",
        models_preset=str(preset),
        context_size=8192,
        n_gpu_layers=9,
        main_gpu=1,
        threads=8,
        batch_size=256,
        tensor_split="2,1",
        flash_attention_mode="off",
        no_mmap=True,
        sleep_idle_seconds=30,
        fit=False,
        models_max=2,
        models_autoload=False,
        api_key_file="keys.txt",
        media_path="media",
        extra_args=("--cache-ram", "0"),
    )
    override = config.to_command_args()
    assert override[override.index("-c") + 1] == "8192"
    assert override[override.index("-ngl") + 1] == "9"
    assert override[override.index("-b") + 1] == "256"
    inherited = replace(config, preset_policy="inherit").to_command_args()
    for flag in (
        "-c",
        "-ngl",
        "--main-gpu",
        "-t",
        "-b",
        "--tensor-split",
        "-fa",
        "--no-mmap",
        "--fit",
        "--sleep-idle-seconds",
    ):
        assert flag not in inherited
    for flag in (
        "--port",
        "--host",
        "--models-max",
        "--no-models-autoload",
        "--api-key-file",
        "--media-path",
    ):
        assert flag in inherited
    assert inherited[-3:] == ["--cache-ram", "0", "--offline"]


@pytest.mark.parametrize("section", ["*", "default", "hidden"])
@pytest.mark.parametrize(
    "key",
    [
        "hf-repo",
        "hf",
        "hfr",
        "hf_repo",
        "LLAMA_ARG_HF_REPO",
        "hff",
        "model-url",
        "mu",
        "LLAMA_ARG_MODEL_URL",
        "docker-repo",
        "dr",
        "mmproj-url",
        "mmu",
        "LLAMA_ARG_MMPROJ_URL",
        "spec-draft-hf",
        "hfd",
        "hfrd",
        "hf-repo-draft",
        "LLAMA_ARG_SPEC_DRAFT_HF_REPO",
        "hfv",
        "hf-repo-v",
        "LLAMA_ARG_HF_REPO_V",
        "hffv",
        "hf-file-v",
        "fim-qwen-7b-default",
        "embedding-gemma-default",
        "tts-oute-default",
        "gpt-oss-20b-default",
        "models-preset",
        "LLAMA_ARG_MODELS_PRESET",
        "offline",
        "alias",
        "api-key",
        "tools",
    ],
)
def test_every_section_rejects_acquisition_aliases_and_unknown_ownership_options(
    preset, section, key
):
    text = preset.read_text()
    if section == "*":
        text = text.replace("[*]\n", f"[*]\n{key} = remote/example\n")
    else:
        text += f"\n[{section}]\n{key} = remote/example\n"
    preset.write_text(text)
    with pytest.raises(ValueError, match="Unsupported local preset option"):
        RouterConfig("", models_preset=str(preset))


@pytest.mark.parametrize(
    "args",
    [
        ("--spec-draft-hf", "remote/model"),
        ("-hfd", "remote/model"),
        ("--hf_repo_v=remote/model",),
        ("--tts-oute-default",),
        ("--fim-qwen-7b-spec",),
        ("--offline=false",),
        ("--unknown-future-download",),
        ("--spec-type", "draft-mtp", "--hf-file-v", "model.gguf"),
        ("--cache-ram",),
        ("--cache-ram", "garbage"),
        ("--cache-ram", "0", "other"),
    ],
)
def test_extra_args_cannot_escape_local_preset_policy(preset, args):
    with pytest.raises(ValueError):
        RouterConfig("", models_preset=str(preset), extra_args=args)


@pytest.mark.parametrize(
    "body",
    [
        "[x]\nmodel = missing.gguf\n",
        "[x]\nmodel = https://example/model.gguf\n",
        "[x]\nmodel = small.gguf\nc = -1\n",
        "[x]\nmodel = small.gguf\nc = 100junk\n",
        "[x]\nmodel = small.gguf\nc = 1\nctx-size = 2\n",
        "[x]\nmodel = small.gguf\n[x]\nmodel = large.gguf\n",
        "[x]\nmodel = small.gguf\ntop-p = NaN\n",
        "[x]\nmodel = small.gguf\ntop-p = 1.5\n",
        "[x]\nmodel = small.gguf\nunknown = 1\n",
        "[x]\nthreads = 4\n",
        "[x]\nmodel: small.gguf\n",
        "version = 2\n[x]\nmodel = small.gguf\n",
        "[x]\nmodel = small.gguf\nmmproj = absent.gguf\n",
        "[x]\nmodel = small.gguf\nmodel-draft = absent.gguf\n",
        "[x:q4_k_m]\nmodel = small.gguf\n[x:Q4_K_M]\nmodel = large.gguf\n",
    ],
)
def test_invalid_values_paths_and_ambiguous_ini_fail_before_launch(preset, body):
    preset.write_text(body)
    with pytest.raises(ValueError):
        RouterConfig("", models_preset=str(preset))


def test_global_model_default_is_validated_and_inherited(preset):
    preset.write_text("[*]\nmodel = small.gguf\nc = 2048\n[x]\nngl = 0\n[y]\nngl = 1\n")
    assert load_local_preset(str(preset)).models == ("x", "y")


def test_negative_boolean_aliases_render_one_canonical_option(preset):
    preset.write_text("[x]\nmodel = small.gguf\nno-mmproj = true\nno-mmap = false\n")
    text = load_local_preset(str(preset)).text
    assert "mmproj-auto = false" in text
    assert "mmap = true" in text


@pytest.mark.parametrize("content", [b"\xff", b"#" * (256 * 1024 + 1)])
def test_preset_input_is_bounded_utf8(preset, content):
    preset.write_bytes(content)
    with pytest.raises(ValueError):
        load_local_preset(str(preset))


def test_invalid_policy_and_inherit_without_preset_are_rejected():
    with pytest.raises(ValueError, match="preset_policy"):
        RouterConfig("models", preset_policy="other")
    with pytest.raises(ValueError, match="requires a local preset"):
        RouterConfig("models", preset_policy="inherit")


def test_safe_extra_args_are_normalized_to_valid_cli_and_absolute_paths(preset):
    config = RouterConfig(
        "",
        models_preset=str(preset),
        extra_args=(
            "--cache_ram=0",
            "--spec_draft_model",
            str(preset.parent / "small.gguf"),
            "--no_jinja",
        ),
    )
    assert config.extra_args == (
        "--cache-ram",
        "0",
        "--spec-draft-model",
        str(preset.parent / "small.gguf"),
        "--no-jinja",
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("LLAMA_ARG_CTX_SIZE", "8192"),
        ("LLAMA_ARG_N_GPU_LAYERS", "2"),
        ("LLAMA_ARG_MODEL", "small.gguf"),
        ("LLAMA_ARG_MMPROJ", "mmproj.gguf"),
    ],
)
def test_normalized_extra_args_cannot_bypass_typed_option_guard(preset, monkeypatch, key, value):
    monkeypatch.chdir(preset.parent)
    with pytest.raises(ValueError, match="cannot override typed option"):
        RouterConfig("", models_preset=str(preset), extra_args=("--" + key, value))


@pytest.mark.parametrize("option", ["--no-mmproj", "--no-mmproj-auto", "--no_mmproj_auto"])
def test_projector_auto_aliases_keep_typed_option_protection(preset, option):
    with pytest.raises(ValueError, match="cannot override typed option"):
        RouterConfig("", models_preset=str(preset), extra_args=(option,))


def test_snapshot_is_independent_of_source_and_deletes_only_owned_file(preset):
    snapshot = PresetSnapshot(load_local_preset(str(preset)))
    path = Path(snapshot.path)
    text = path.read_text()
    preset.write_text("[new]\nhf = remote/model\n")
    assert path.read_text() == text
    snapshot.close()
    snapshot.close()
    assert not path.exists()
    assert preset.exists()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    process = FakeProcess()
    service = RuntimeService(process)
    caps = capabilities(tmp_path)
    caps = replace(
        caps,
        flags=caps.flags
        | {"--models-preset", "--offline", "--model", "--ctx-size", "--gpu-layers", "--threads"},
    )
    instance = LlamaCppServerManager(
        runtime_service=service,
        probe_binary=lambda _: caps,
        client_factory=lambda connection: FakeClient(connection, role="router"),
        register_atexit=False,
    )
    monkeypatch.setattr(manager_module, "_port_is_bound", lambda *_: False)
    yield instance, process
    instance.stop()


def test_same_path_edit_replaces_snapshot_and_unchanged_start_is_idempotent(preset, manager):
    instance, process = manager
    config = RouterConfig("", models_preset=str(preset), preset_policy="inherit")
    assert instance.start_router(config)[0]
    first = Path(process.command[process.command.index("--models-preset") + 1])
    assert first != preset
    assert instance.start_router(config)[0]
    assert process.stop_calls == 0
    preset.write_text(preset.read_text().replace("2048", "3072"))
    assert "3072" not in first.read_text()
    assert instance.start_router(config)[0]  # reusing the same config also rereads bytes
    second = Path(process.command[process.command.index("--models-preset") + 1])
    assert second != first
    assert not first.exists()
    assert "3072" in second.read_text()
    assert instance.current_config.preset_sha256 == sha256(preset.read_bytes()).hexdigest()
    assert instance.stop()[0]
    assert not second.exists()
    assert preset.exists()


@pytest.mark.parametrize("failure", ["invalid_edit", "missing_file", "staging", "capability"])
def test_failed_preflight_preserves_healthy_runtime_and_snapshot(
    preset, manager, monkeypatch, failure
):
    instance, process = manager
    config = RouterConfig("", models_preset=str(preset))
    assert instance.start_router(config)[0]
    old_snapshot = Path(process.command[process.command.index("--models-preset") + 1])
    old_config = instance.current_config
    if failure == "invalid_edit":
        preset.write_text("[*]\nhf = remote/model\n[x]\nmodel = small.gguf\n")
    elif failure == "missing_file":
        preset.unlink()
    elif failure == "staging":
        preset.write_text(preset.read_text() + "\n# new content\n")

        def fail(_):
            raise OSError("fixture cannot stage")

        monkeypatch.setattr(manager_module, "PresetSnapshot", fail)
    else:
        caps = instance._probe_binary(None)
        monkeypatch.setattr(
            instance, "_probe_binary", lambda _: replace(caps, flags=caps.flags - {"--offline"})
        )
    success, error = instance.start_router(config)
    assert not success and error
    assert process.stop_calls == 0
    assert instance.is_running
    assert instance.current_config is old_config
    assert old_snapshot.is_file()


def test_preset_launch_strips_ambient_model_acquisition_and_forces_offline(
    preset, manager, monkeypatch
):
    instance, process = manager
    monkeypatch.setenv("LLAMA_ARG_HF_REPO", "remote/model")
    monkeypatch.setenv("LLAMA_ARG_SPEC_DRAFT_HF_REPO", "remote/draft")
    monkeypatch.setenv("LLAMA_ARG_CTX_SIZE", "1")
    monkeypatch.setenv("LLAMA_API_KEY", "inert-fixture")
    monkeypatch.setenv("LLAMACPP_API_KEY", "client-fixture")
    assert instance.start_router(RouterConfig("", models_preset=str(preset)))[0]
    assert process.command[-1] == "--offline"
    environment = process.start_kwargs["env"]
    assert not any(key.startswith("LLAMA_ARG_") for key in environment)
    assert "LLAMA_API_KEY" not in environment
    assert environment["LLAMACPP_API_KEY"] == "client-fixture"


def test_failed_spawn_removes_candidate_snapshot(preset, manager, monkeypatch):
    instance, process = manager
    paths = []

    def fail(command, **kwargs):
        path = Path(command[command.index("--models-preset") + 1])
        assert path.exists()
        paths.append(path)
        raise OSError("fixture spawn failure")

    monkeypatch.setattr(process, "start", fail)
    assert not instance.start_router(RouterConfig("", models_preset=str(preset)))[0]
    assert len(paths) == 1 and not paths[0].exists()


def test_incomplete_stop_retains_snapshot_until_process_is_gone(preset, manager, monkeypatch):
    instance, process = manager
    assert instance.start_router(RouterConfig("", models_preset=str(preset)))[0]
    path = Path(process.command[process.command.index("--models-preset") + 1])
    stop = process.stop
    monkeypatch.setattr(
        process, "stop", lambda **_: StopResult(False, False, None, 0.01, remaining_pids=(123,))
    )
    assert not instance.stop()[0]
    assert path.exists()
    monkeypatch.setattr(process, "stop", stop)
    assert instance.stop()[0]
    assert not path.exists()


def test_source_edit_after_staging_cannot_change_launched_bytes(preset, manager, monkeypatch):
    instance, process = manager
    snapshot_type = manager_module.PresetSnapshot

    def stage_and_edit(validated):
        snapshot = snapshot_type(validated)
        preset.write_text("[bad]\nhf = remote/model\n")
        return snapshot

    monkeypatch.setattr(manager_module, "PresetSnapshot", stage_and_edit)
    assert instance.start_router(RouterConfig("", models_preset=str(preset)))[0]
    path = Path(process.command[process.command.index("--models-preset") + 1])
    assert "remote/model" not in path.read_text()
    assert "small.gguf" in path.read_text()


def test_router_model_release_keeps_snapshot_while_router_remains_owned(preset, manager):
    instance, process = manager
    assert instance.start_router(RouterConfig("", models_preset=str(preset)))[0]
    path = Path(process.command[process.command.index("--models-preset") + 1])
    result = instance.runtime_service.request_release(source="fixture")
    assert result.success
    assert process.is_running
    assert path.exists()


def test_public_node_appends_preset_fields_and_can_start_without_model_root(
    node_package, preset, monkeypatch
):
    cls = node_package.NODE_CLASS_MAPPINGS["StartLlamaCppRouter"]
    module = sys.modules[cls.__module__]
    configs = []

    class Manager:
        server_url = "http://127.0.0.1:8080"

        def start_router(self, config, **kwargs):
            configs.append(config)
            return True, None

    monkeypatch.setattr(module, "get_server_manager", lambda: Manager())
    monkeypatch.setattr(
        module, "get_router_models_directory", lambda *_: pytest.fail("unneeded model scan")
    )
    schema = cls.INPUT_TYPES()
    assert list(schema["optional"])[-3:] == ["models_directory", "models_preset", "preset_policy"]
    assert schema["optional"]["preset_policy"][1]["default"] == "override"
    assert cls().start_router(4096, "", 0, 4, models_preset=str(preset), preset_policy="inherit")[1]
    assert configs[0].models_dir == ""
    assert configs[0].models_preset == str(preset)
    assert configs[0].preset_policy == "inherit"
    preset.write_text("[bad]\nhf = remote/model\n")
    assert not cls().start_router(4096, "", 0, 4, models_preset=str(preset))[1]
    assert len(configs) == 1
