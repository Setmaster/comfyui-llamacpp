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


def main() -> None:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
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
        for path in Path("example_workflows").iterdir()
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

    root = sdist_members[0].split("/", 1)[0]
    normalized_sdist = {name.removeprefix(f"{root}/") for name in sdist_members if name != root}
    missing_workflows = {f"example_workflows/{name}" for name in workflow_files} - normalized_sdist
    if missing_workflows:
        raise AssertionError(
            f"Source archive is missing workflow assets: {sorted(missing_workflows)}"
        )
    expected_tests = {
        path.as_posix()
        for path in Path("tests").rglob("*")
        if path.is_file() and path.suffix in {".json", ".md", ".mjs", ".py"}
    }
    missing = expected_tests - normalized_sdist
    if missing:
        raise AssertionError(f"Source archive is missing test files: {sorted(missing)}")
    if "package.json" not in normalized_sdist:
        raise AssertionError("Source archive is missing package.json for frontend tests")

    print(
        f"Distribution manifests passed: {len(workflow_files)} workflow assets, "
        f"{len(expected_tests)} source-test files, no duplicate members"
    )


if __name__ == "__main__":
    main()
