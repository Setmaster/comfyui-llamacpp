from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading

import pytest
from test_comfy_routes import FakeContent, FakeRequest, FakeWeb, install

from generation import profiles as storage
from generation.profiles import (
    FREEFORM_PROFILE,
    MAX_PROFILE_FILE_BYTES,
    ProfileConflictError,
    ProfileValidationError,
    TaskProfileSnapshot,
    load_profile_library,
    load_profiles,
    save_profile_library,
)
from runtime.comfy_routes import PROFILES_LIBRARY_ROUTE, PROFILES_ROUTE, install_generation_routes


def document(*profiles):
    return json.dumps(
        {"schema_version": 1, "profiles": [profile.as_dict() for profile in profiles]},
        ensure_ascii=False,
    )


def test_library_roundtrip_preserves_unicode_whitespace_and_saved_snapshot(tmp_path):
    path = tmp_path / "user-a" / "comfyui-llamacpp" / "profiles.json"
    first = TaskProfileSnapshot("author", " 文 ✨ ", "\tAbout\n", "\r\nSystem  ", " \t", "\n end ")
    saved_snapshot = first.to_json()
    missing = load_profile_library(path)
    assert not path.parent.exists()
    result = save_profile_library(path, document(first), missing["content_sha256"])
    assert load_profile_library(path) == result
    assert result["profiles"] == [first.as_dict()]
    assert load_profiles(path) == (FREEFORM_PROFILE, first)
    changed = TaskProfileSnapshot("author", "New", prompt_prefix="changed")
    save_profile_library(path, document(changed), result["content_sha256"])
    assert TaskProfileSnapshot.from_json(saved_snapshot) == first
    assert saved_snapshot == first.to_json()
    assert list(path.parent.iterdir()) == [path]


def test_two_editors_with_same_revision_cannot_overwrite_each_other(tmp_path):
    path = tmp_path / "profiles.json"
    revision = load_profile_library(path)["content_sha256"]
    barrier = threading.Barrier(2)

    def save(name):
        barrier.wait(timeout=5)
        try:
            return save_profile_library(path, document(TaskProfileSnapshot(name, name)), revision)
        except ProfileConflictError:
            return "conflict"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ("first", "second")))
    assert sum(result == "conflict" for result in results) == 1
    winner = next(result for result in results if result != "conflict")
    assert load_profile_library(path) == winner


def test_external_file_change_and_missing_file_recreation_are_conflicts(tmp_path):
    path = tmp_path / "profiles.json"
    revision = load_profile_library(path)["content_sha256"]
    external = document(TaskProfileSnapshot("external", "External"))
    path.write_text(external, encoding="utf-8")
    with pytest.raises(ProfileConflictError):
        save_profile_library(path, document(), revision)
    assert path.read_text(encoding="utf-8") == external


@pytest.mark.parametrize(
    "bad",
    [
        '{"schema_version":1,"profiles":[],"profiles":[]}',
        '{"schema_version":1,"profiles":[],"\\u0070rofiles":[]}',
        '{"schema_version":2,"profiles":[]}',
        '{"schema_version":1,"profiles":[],"path":"../other-user"}',
        document(FREEFORM_PROFILE),
        document(TaskProfileSnapshot("same", "First"), TaskProfileSnapshot("same", "Second")),
        "x" * (MAX_PROFILE_FILE_BYTES + 1),
        b"\xff",
        "[" * 1100 + "]" * 1100,
    ],
)
def test_invalid_import_never_changes_existing_library(tmp_path, bad):
    path = tmp_path / "profiles.json"
    original = document(TaskProfileSnapshot("existing", "Keep"))
    path.write_text(original, encoding="utf-8")
    revision = load_profile_library(path)["content_sha256"]
    with pytest.raises(ProfileValidationError):
        save_profile_library(path, bad, revision)
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.iterdir()) == [path]


def test_replace_failure_keeps_original_and_removes_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    original = document(TaskProfileSnapshot("existing", "Keep"))
    path.write_text(original, encoding="utf-8")
    revision = load_profile_library(path)["content_sha256"]

    def fail_replace(*args):
        raise PermissionError("PRIVATE_PATH should not reach UI")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(ProfileValidationError, match="PermissionError") as failure:
        save_profile_library(path, document(), revision)
    assert "PRIVATE_PATH" not in str(failure.value)
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.iterdir()) == [path]


def test_external_edit_during_temp_write_is_rechecked_before_replace(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    revision = load_profile_library(path)["content_sha256"]
    external = document(TaskProfileSnapshot("external", "External"))
    real_fsync = storage.os.fsync

    def change_file(fd):
        real_fsync(fd)
        path.write_text(external, encoding="utf-8")

    monkeypatch.setattr(storage.os, "fsync", change_file)
    with pytest.raises(ProfileConflictError):
        save_profile_library(path, document(), revision)
    assert path.read_text(encoding="utf-8") == external
    assert list(tmp_path.iterdir()) == [path]


def test_symlink_library_is_not_followed_for_authoring(tmp_path):
    target = tmp_path / "other-user.json"
    target.write_text(document(), encoding="utf-8")
    link = tmp_path / "profiles.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ProfileValidationError, match="symbolic link"):
        load_profile_library(link)
    assert target.read_text(encoding="utf-8") == document()


def route(server, method, path=PROFILES_LIBRARY_ROUTE):
    return next(r.handler for r in server.routes if (r.method, r.path) == (method, path))


def request(body=None, revision=None, *, user="a", length=None):
    req = FakeRequest(content=FakeContent(body or b""), content_length=length)
    req.user = user
    req.headers = {} if revision is None else {"If-Match": f'"{revision}"'}
    return req


def test_authoring_routes_resolve_each_request_user_and_preserve_legacy_get(tmp_path):
    server, manager, registry = install(tmp_path)

    class Users:
        def get_request_user_filepath(self, req, relative, *, create_dir):
            assert relative == "comfyui-llamacpp/profiles.json"
            assert create_dir is False
            return tmp_path / req.user / relative

    server.user_manager = Users()
    assert install_generation_routes(server, FakeWeb, manager=manager, registry=registry)
    assert len([r for r in server.routes if r.path == PROFILES_LIBRARY_ROUTE]) == 2
    before_a = asyncio.run(route(server, "GET")(request(user="a")))
    before_b = asyncio.run(route(server, "GET")(request(user="b")))
    encoded = document(TaskProfileSnapshot("tenant-a", "Tenant A")).encode("utf-8")
    saved = asyncio.run(route(server, "POST")(request(encoded, before_a.payload["content_sha256"])))
    assert saved.status == 200
    assert saved.headers["Cache-Control"] == "no-store"
    assert saved.payload["profiles"][0]["id"] == "tenant-a"
    after_b = asyncio.run(route(server, "GET")(request(user="b")))
    assert before_b.payload == after_b.payload
    assert not (tmp_path / "b").exists()
    old_get = asyncio.run(route(server, "GET", PROFILES_ROUTE)(request()))
    assert set(old_get.payload) == {"schema_version", "profiles"}
    assert [p["id"] for p in old_get.payload["profiles"]] == ["freeform", "tenant-a"]
    conflict = asyncio.run(
        route(server, "POST")(request(encoded, before_a.payload["content_sha256"]))
    )
    assert conflict.status == 409


def test_profile_post_requires_revision_and_bounds_declared_and_streaming_bodies(tmp_path):
    server, _, _ = install(tmp_path)
    post = route(server, "POST")
    missing = asyncio.run(post(request(document().encode())))
    assert missing.status == 428
    for req in (
        request(b"", "a" * 64, length=MAX_PROFILE_FILE_BYTES + 1),
        request(b"x" * (MAX_PROFILE_FILE_BYTES + 1), "a" * 64),
    ):
        assert asyncio.run(post(req)).status == 413
    assert not server.user_manager.path.exists()


def test_profile_route_rejects_duplicate_keys_before_any_write(tmp_path):
    server, _, _ = install(tmp_path)
    revision = asyncio.run(route(server, "GET")(request())).payload["content_sha256"]
    bad = b'{"schema_version":1,"profiles":[],"profiles":[]}'
    response = asyncio.run(route(server, "POST")(request(bad, revision)))
    assert response.status == 400
    assert "duplicate JSON key" in response.payload["error"]["message"]
    assert not server.user_manager.path.exists()
