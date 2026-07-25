from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from unittest import mock

from runtime.capabilities import (
    BinaryProbeError,
    BinaryResolutionError,
    clear_capability_cache,
    probe_server_binary,
    probe_server_devices,
    resolve_server_binary,
)
from runtime.config import RouterConfig, ServerConfig


class ConfigContractTests(unittest.TestCase):
    def test_single_legacy_flash_boolean_maps_to_current_explicit_on(self) -> None:
        config = ServerConfig(
            model_path="/models/legacy.gguf",
            tensor_split="3,1",
            threads=8,
            batch_size=512,
            flash_attention=True,
            no_mmap=True,
        )
        self.assertEqual(
            config.to_command_args(),
            [
                "-m",
                "/models/legacy.gguf",
                "--port",
                "8080",
                "--host",
                "127.0.0.1",
                "-c",
                "4096",
                "-ngl",
                "999",
                "--main-gpu",
                "0",
                "--tensor-split",
                "3,1",
                "-t",
                "8",
                "-b",
                "512",
                "-fa",
                "on",
                "--no-mmap",
            ],
        )

    def test_router_legacy_command_and_autoload_negation(self) -> None:
        config = RouterConfig(
            models_dir="/models/LLM/gguf",
            models_autoload=False,
            batch_size=512,
        )
        self.assertEqual(
            config.to_command_args(),
            [
                "--models-dir",
                "/models/LLM/gguf",
                "--port",
                "8080",
                "--host",
                "127.0.0.1",
                "-c",
                "4096",
                "-ngl",
                "999",
                "--main-gpu",
                "0",
                "--models-max",
                "4",
                "--no-models-autoload",
                "-b",
                "512",
            ],
        )

    def test_modern_optional_flags_are_explicit(self) -> None:
        config = ServerConfig(
            model_path="model.gguf",
            n_gpu_layers="auto",
            flash_attention_mode="off",
            mmproj_path="mmproj.gguf",
            sleep_idle_seconds=60,
            api_key_file="keys.txt",
            media_path="media",
            fit=False,
            extra_args=("--cache-ram", "0"),
        )
        args = config.to_command_args()
        self.assertIn("auto", args)
        self.assertEqual(args[args.index("-fa") + 1], "off")
        self.assertEqual(args[args.index("--sleep-idle-seconds") + 1], "60")
        self.assertEqual(args[-2:], ["--cache-ram", "0"])

    def test_text_only_mode_emits_an_explicit_no_projector_flag(self) -> None:
        args = ServerConfig("model.gguf", no_mmproj=True).to_command_args()

        self.assertIn("--no-mmproj", args)
        self.assertNotIn("--mmproj", args)

    def test_configs_are_immutable_and_copy_mutable_inputs(self) -> None:
        extra = ["--metrics"]
        config = ServerConfig("model.gguf", extra_args=extra)
        extra.append("--props")
        self.assertEqual(config.extra_args, ("--metrics",))
        with self.assertRaises(FrozenInstanceError):
            config.port = 9000  # type: ignore[misc]

    def test_extra_args_cannot_override_typed_or_security_options(self) -> None:
        reserved = (
            "--port",
            "--host=0.0.0.0",
            "-m",
            "--model=other.gguf",
            "-mu",
            "--reuse-port",
            "--api-prefix=/hidden",
            "--api-key=secret",
            "--api-key-file",
            "--ssl-key-file=key.pem",
            "--ssl-cert-file",
            "--sleep-idle-seconds=1",
            "--mmproj=other.gguf",
            "--mmproj-auto",
            "--no-mmproj",
            "--models-dir",
            "--models-max=9",
        )
        for argument in reserved:
            with self.subTest(argument=argument):
                with self.assertRaisesRegex(ValueError, "cannot override typed option"):
                    ServerConfig("model.gguf", extra_args=(argument,))

        # Untyped diagnostic/tuning flags remain available.
        self.assertEqual(
            ServerConfig("model.gguf", extra_args=("--metrics",)).extra_args,
            ("--metrics",),
        )

    def test_fingerprint_covers_every_field_and_binary(self) -> None:
        base = ServerConfig("model.gguf")
        baseline = base.fingerprint("binary-a")
        replacements = {
            "model_path": "other.gguf",
            "port": 8081,
            "host": "0.0.0.0",
            "context_size": 8192,
            "n_gpu_layers": 1,
            "main_gpu": 1,
            "tensor_split": "1,1",
            "threads": 2,
            "batch_size": 256,
            "flash_attention": True,
            "no_mmap": True,
            "mmproj_path": "mmproj.gguf",
            "sleep_idle_seconds": 10,
            "api_key_file": "keys",
            "media_path": "media",
            "flash_attention_mode": "auto",
            "fit": False,
            "extra_args": ("--metrics",),
            "no_mmproj": True,
        }
        self.assertEqual(set(replacements), {item.name for item in fields(base)})
        for name, value in replacements.items():
            with self.subTest(field=name):
                self.assertNotEqual(
                    replace(base, **{name: value}).fingerprint("binary-a"), baseline
                )
        self.assertNotEqual(base.fingerprint("binary-b"), baseline)
        self.assertEqual(base.config_hash("binary-a"), baseline)

    def test_router_fingerprint_includes_previous_omissions(self) -> None:
        base = RouterConfig("models")
        for changed in (
            replace(base, host="0.0.0.0"),
            replace(base, threads=4),
            replace(base, batch_size=512),
            replace(base, flash_attention=True),
            replace(base, models_autoload=False),
        ):
            self.assertNotEqual(changed.fingerprint("same-bin"), base.fingerprint("same-bin"))

    def test_invalid_values_fail_before_spawn(self) -> None:
        with self.assertRaises(ValueError):
            ServerConfig("model.gguf", port=0)
        with self.assertRaises(ValueError):
            ServerConfig("model.gguf", n_gpu_layers="banana")
        with self.assertRaises(ValueError):
            RouterConfig("models", models_max=-1)
        with self.assertRaises(ValueError):
            ServerConfig("model.gguf", flash_attention=True, flash_attention_mode="on")
        with self.assertRaises(TypeError):
            ServerConfig("model.gguf", extra_args=(1,))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ServerConfig("model.gguf", no_mmproj=1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "either mmproj_path or no_mmproj"):
            ServerConfig("model.gguf", mmproj_path="mmproj.gguf", no_mmproj=True)
        with self.assertRaisesRegex(ValueError, "mmproj_path must not be empty"):
            ServerConfig("model.gguf", mmproj_path=" ")


class CapabilityProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_capability_cache()
        self.tempdir = tempfile.TemporaryDirectory()
        self.binary = Path(self.tempdir.name, "llama-server")
        self.binary.write_text("fixture", encoding="utf-8")
        self.binary.chmod(0o755)

    def tearDown(self) -> None:
        clear_capability_cache()
        self.tempdir.cleanup()

    def test_explicit_and_environment_resolution(self) -> None:
        self.assertEqual(resolve_server_binary(self.binary), self.binary.resolve())
        self.assertEqual(
            resolve_server_binary(env={"LLAMA_SERVER_BINARY": str(self.binary)}),
            self.binary.resolve(),
        )

    def test_missing_explicit_binary_is_clear(self) -> None:
        with self.assertRaises(BinaryResolutionError):
            resolve_server_binary(Path(self.tempdir.name, "missing"))

    def test_probe_parses_flags_build_commit_and_is_cached(self) -> None:
        calls: list[list[str]] = []

        def run(command, **kwargs):
            calls.append(command)
            if command[-1] == "--version":
                output = "version: 9957 (abcdef123456)\n"
            else:
                output = (
                    "-ngl, --gpu-layers, --n-gpu-layers N  max layers, either an exact "
                    "number, 'auto', or 'all' (default: auto)\n"
                    "--models-dir PATH\n--models-max N\n--sleep-idle-seconds N\n"
                    "--api-key-file FILE\n"
                )
            return subprocess.CompletedProcess(command, 0, stdout=output)

        with mock.patch("runtime.capabilities.subprocess.run", side_effect=run):
            first = probe_server_binary(self.binary)
            second = probe_server_binary(self.binary)

        self.assertIs(first, second)
        self.assertEqual(len(calls), 2)
        self.assertTrue(first.supports_router)
        self.assertTrue(first.supports_idle_sleep)
        self.assertTrue(first.supports_api_key_file)
        self.assertTrue(first.supports_symbolic_gpu_layers)
        self.assertEqual(first.build_number, 9957)
        self.assertEqual(first.commit, "abcdef123456")
        self.assertEqual(len(first.identity), 64)

    def test_injected_probe_runner_is_not_cached(self) -> None:
        calls = 0

        def run(command, **kwargs):
            nonlocal calls
            calls += 1
            return subprocess.CompletedProcess(command, 0, stdout="--models-dir\n")

        probe_server_binary(self.binary, runner=run)
        probe_server_binary(self.binary, runner=run)
        self.assertEqual(calls, 4)

    def test_old_integer_only_gpu_layers_help_is_not_treated_as_symbolic(self) -> None:
        def run(command, **kwargs):
            output = (
                "version b6000\n"
                if command[-1] == "--version"
                else "-ngl, --n-gpu-layers N  number of layers to store in VRAM\n"
            )
            return subprocess.CompletedProcess(command, 0, stdout=output)

        result = probe_server_binary(self.binary, runner=run)

        self.assertFalse(result.supports_symbolic_gpu_layers)

    def test_device_probe_is_separate_and_bounds_lines(self) -> None:
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            output = "Available devices:\n" + "\n".join(
                ["CUDA0: test device", "X" * 50, "ignored third device"]
            )
            return subprocess.CompletedProcess(command, 0, stdout=output)

        result = probe_server_devices(
            self.binary,
            timeout=1.5,
            runner=run,
            max_lines=2,
            max_line_length=20,
        )

        self.assertEqual(result, ("CUDA0: test device", "X" * 20))
        self.assertEqual(calls[0][0], [str(self.binary.resolve()), "--list-devices"])
        self.assertEqual(calls[0][1]["timeout"], 1.5)

    def test_device_probe_timeout_is_actionable(self) -> None:
        def run(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with self.assertRaisesRegex(BinaryProbeError, r"--list-devices timed out after 2s"):
            probe_server_devices(self.binary, timeout=2, runner=run)


if __name__ == "__main__":
    unittest.main()
