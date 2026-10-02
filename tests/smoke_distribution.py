"""Exercise extracted distributions without editable-checkout import fallbacks."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import runpy
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import zipfile
from pathlib import Path

BOOTSTRAP = """
import json, runpy, sys
sys.path.extend(json.loads(sys.argv[1]))
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def _python(source_root: Path, mode: str, wheel_root: Path | None = None):
    dependencies = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
    command = [
        sys.executable,
        "-I",
        "-S",
        "-c",
        BOOTSTRAP,
        json.dumps(dependencies),
        str(source_root / "tests" / "smoke_distribution.py"),
        "--child",
        mode,
        "--source-root",
        str(source_root),
    ]
    if wheel_root is not None:
        command.extend(["--wheel-root", str(wheel_root)])
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return subprocess.run(
        command,
        cwd=source_root,
        env=environment,
        capture_output=mode == "wheel",
        text=True,
        timeout=600,
        check=True,
    )


def _wheel_import(wheel_root: Path, source_root: Path) -> None:
    # Reuse only the extracted import-time Comfy harness, not its package fixture
    # or optional dependency stubs. Dependencies must import from real packages.
    harness = runpy.run_path(str(source_root / "tests" / "conftest.py"))
    with tempfile.TemporaryDirectory(prefix="llamacpp-wheel-models-") as temporary:
        model_root = Path(temporary) / "models" / "LLM" / "gguf"
        model_root.mkdir(parents=True)
        harness["_install_comfy_stubs"](model_root)
        sys.path.insert(0, str(wheel_root))
        package = importlib.import_module("comfyui_llamacpp")
        package_root = wheel_root / "comfyui_llamacpp"
        fixture = source_root / "tests" / "fixtures" / "workflows" / "v0_3_0_contracts.json"
        expected = set(json.loads(fixture.read_text())["nodes"]) | {
            "LlamaCppGenerate",
            "LlamaCppTaskProfile",
            "LlamaCppResult",
            "LlamaCppMessage",
            "LlamaCppMessages",
            "LlamaCppRequestBudget",
            "LlamaCppCaptions",
            "LlamaCppTranscribe",
        }
        assert len(expected) == 25
        assert set(package.NODE_CLASS_MAPPINGS) == expected
        assert set(package.NODE_DISPLAY_NAME_MAPPINGS) == expected
        assert package.__version__ == importlib.metadata.version("comfyui-llamacpp")

        for path in package_root.rglob("*.py"):
            parts = list(path.relative_to(wheel_root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            importlib.import_module(".".join(parts))
        for name, module in tuple(sys.modules.items()):
            if name == "comfyui_llamacpp" or name.startswith("comfyui_llamacpp."):
                assert Path(module.__file__).resolve().is_relative_to(package_root), name

        web_root = (package_root / package.WEB_DIRECTORY).resolve()
        assert web_root.is_relative_to(package_root)
        assert list(web_root.glob("*.js")), "Wheel has no frontend JavaScript"
        for path in web_root.glob("*.js"):
            assert path.stat().st_size > 0, f"Empty frontend asset: {path.name}"
        json.loads((web_root / "templates.json").read_text(encoding="utf-8"))
        print(
            f"Isolated wheel passed: version {package.__version__}, "
            f"{len(expected)} nodes, runtime/web assets"
        )


def _prove_rejection(wheel: Path, sdist: Path, source_root: Path, staging: Path) -> None:
    checker = runpy.run_path(str(source_root / "tests" / "check_distribution.py"))
    for index, member in enumerate(("runtime/client.py", "web/generate.js")):
        broken = staging / f"damaged-{index}"
        broken.mkdir()
        shutil.copyfile(sdist, broken / sdist.name)
        with (
            zipfile.ZipFile(wheel) as original,
            zipfile.ZipFile(broken / wheel.name, "w") as target,
        ):
            removed = f"comfyui_llamacpp/{member}"
            assert removed in original.namelist()
            for info in original.infolist():
                if info.filename != removed:
                    target.writestr(info, original.read(info))
        try:
            checker["check_manifests"](broken, source_root)
        except AssertionError as exc:
            assert removed in str(exc), str(exc)
        else:
            raise AssertionError(f"Manifest checker accepted missing {removed}")
        if member == "runtime/client.py":
            extracted = broken / "wheel"
            with zipfile.ZipFile(broken / wheel.name) as archive:
                archive.extractall(extracted)
            try:
                _python(source_root, "wheel", extracted)
            except subprocess.CalledProcessError as exc:
                assert "ModuleNotFoundError" in exc.stderr, exc.stderr
                assert "comfyui_llamacpp.runtime.client" in exc.stderr, exc.stderr
            else:
                raise AssertionError("Damaged wheel imported through a source fallback")
        print(f"Damaged-artifact rejection passed: missing {member}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("dist"))
    parser.add_argument("--prove-rejection", action="store_true")
    parser.add_argument("--child", choices=("wheel", "sdist"), help=argparse.SUPPRESS)
    parser.add_argument("--source-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--wheel-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child == "wheel":
        _wheel_import(args.wheel_root, args.source_root)
        return
    if args.child == "sdist":
        sys.path.insert(0, str(args.source_root))
        import pytest

        raise SystemExit(pytest.main(["-q", str(args.source_root / "tests")]))

    checkout = Path(__file__).resolve().parents[1]
    checker = runpy.run_path(str(checkout / "tests" / "check_distribution.py"))
    directory = args.directory.resolve()
    checker["check_manifests"](directory, checkout)
    wheel = checker["_single"](directory, "*.whl")
    sdist = checker["_single"](directory, "*.tar.gz")
    with tempfile.TemporaryDirectory(prefix="llamacpp-distribution-") as temporary:
        staging = Path(temporary)
        wheel_root = staging / "wheel"
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(wheel_root)
        with tarfile.open(sdist, "r:gz") as archive:
            # Build artifacts are produced locally by this project's build backend.
            archive.extractall(staging / "source", filter="data")
        (source_root,) = (staging / "source").iterdir()
        try:
            print(_python(source_root, "wheel", wheel_root).stdout, end="", flush=True)
        except subprocess.CalledProcessError as exc:
            print(exc.stdout, end="", file=sys.stderr)
            print(exc.stderr, end="", file=sys.stderr)
            raise
        if args.prove_rejection:
            _prove_rejection(wheel, sdist, source_root, staging)
        _python(source_root, "sdist")
        scripts = sorted(str(path) for path in (source_root / "tests" / "js").glob("*.test.mjs"))
        assert scripts, "Source archive has no frontend tests"
        subprocess.run(["node", "--test", *scripts], cwd=source_root, check=True, timeout=120)
        print("Extracted sdist Python and JavaScript suites passed")


if __name__ == "__main__":
    main()
