"""Validate the built wheel and source archive using only the standard library."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path


def _single(directory: Path, pattern: str) -> Path:
    matches = tuple(directory.glob(pattern))
    if len(matches) != 1:
        raise AssertionError(f"Expected one {pattern} in {directory}, found {len(matches)}")
    return matches[0]


def _assert_unique(members: list[str], archive: Path) -> None:
    duplicates = {name for name in members if members.count(name) > 1}
    if duplicates:
        raise AssertionError(f"Duplicate members in {archive.name}: {sorted(duplicates)}")


def check_manifests(directory: Path, source_root: Path) -> None:
    wheel = _single(directory, "*.whl")
    sdist = _single(directory, "*.tar.gz")

    with zipfile.ZipFile(wheel) as archive:
        wheel_members = archive.namelist()
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_members = archive.getnames()

    _assert_unique(wheel_members, wheel)
    _assert_unique(sdist_members, sdist)

    workflow_files = {
        path.name
        for path in (source_root / "example_workflows").iterdir()
        if path.suffix in {".json", ".jpg"}
    }
    packaged_workflows = {
        Path(name).name
        for name in wheel_members
        if "/example_workflows/" in name and Path(name).suffix in {".json", ".jpg"}
    }
    if packaged_workflows != workflow_files:
        raise AssertionError(
            f"Wheel workflow assets differ: {sorted(packaged_workflows ^ workflow_files)}"
        )
    if any("/tests/" in name for name in wheel_members):
        raise AssertionError("Runtime wheel unexpectedly contains the source test suite")
    if any("docs/research/" in name for name in wheel_members):
        raise AssertionError("Runtime wheel unexpectedly contains source research documents")

    root = sdist_members[0].split("/", 1)[0]
    normalized_sdist = {name.removeprefix(f"{root}/") for name in sdist_members if name != root}
    runtime_files = {path.name for path in source_root.glob("*.py")}
    for package in ("generation", "models", "nodes", "runtime", "web"):
        runtime_files.update(
            path.relative_to(source_root).as_posix()
            for path in (source_root / package).glob("*")
            if path.is_file() and path.suffix in {".py", ".js", ".json"}
        )
    missing_runtime = {f"comfyui_llamacpp/{name}" for name in runtime_files} - set(wheel_members)
    if missing_runtime:
        raise AssertionError(f"Wheel is missing runtime assets: {sorted(missing_runtime)}")
    missing_source = runtime_files - normalized_sdist
    if missing_source:
        raise AssertionError(f"Source archive is missing runtime assets: {sorted(missing_source)}")
    missing_workflows = {f"example_workflows/{name}" for name in workflow_files} - normalized_sdist
    if missing_workflows:
        raise AssertionError(
            f"Source archive is missing workflow assets: {sorted(missing_workflows)}"
        )
    expected_tests = {
        path.relative_to(source_root).as_posix()
        for path in (source_root / "tests").rglob("*")
        if path.is_file() and path.suffix in {".json", ".md", ".mjs", ".pem", ".py"}
    }
    missing = expected_tests - normalized_sdist
    if missing:
        raise AssertionError(f"Source archive is missing test files: {sorted(missing)}")
    if "package.json" not in normalized_sdist:
        raise AssertionError("Source archive is missing package.json for frontend tests")
    protocol_docs = {
        "docs/research/task-profile-bakeoff-2026-10-02.md",
        "docs/research/visible-evidence-heldout-2026-10-02.md",
    }
    missing_protocols = protocol_docs - normalized_sdist
    if missing_protocols:
        raise AssertionError(
            f"Source archive is missing benchmark protocols: {sorted(missing_protocols)}"
        )

    print(
        f"Distribution manifests passed: {len(runtime_files)} runtime assets, "
        f"{len(workflow_files)} workflow assets, "
        f"{len(expected_tests)} source-test files, {len(protocol_docs)} benchmark protocols, "
        "no duplicate members"
    )


if __name__ == "__main__":
    check_manifests(
        Path(sys.argv[1] if len(sys.argv) > 1 else "dist"), Path(__file__).resolve().parents[1]
    )
