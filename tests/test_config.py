from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from unittest import mock

from runtime.capabilities import (
    BinaryResolutionError,
    clear_capability_cache,
    probe_server_binary,
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

    def test_configs_are_immutable_and_copy_mutable_inputs(self) -> None:
        extra = ["--metrics"]
        config = ServerConfig("model.gguf", extra_args=extra)
        extra.append("--props")
        self.assertEqual(config.extra_args, ("--metrics",))
        with self.assertRaises(FrozenInstanceError):
            config.port = 9000  # type: ignore[misc]

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
                output = "version b9957 (commit abcdef123456)\n"
            else:
                output = "--models-dir PATH\n--models-max N\n--sleep-idle-seconds N\n--api-key-file FILE\n"
            return subprocess.CompletedProcess(command, 0, stdout=output)

        with mock.patch("runtime.capabilities.subprocess.run", side_effect=run):
            first = probe_server_binary(self.binary)
            second = probe_server_binary(self.binary)

        self.assertIs(first, second)
        self.assertEqual(len(calls), 2)
        self.assertTrue(first.supports_router)
        self.assertTrue(first.supports_idle_sleep)
        self.assertTrue(first.supports_api_key_file)
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


if __name__ == "__main__":
    unittest.main()
