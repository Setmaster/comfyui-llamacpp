"""Standalone test harness for the ComfyUI custom-node package.

The contract suite must be able to import the package without importing a real
ComfyUI installation or initializing GPU/runtime dependencies.  The stubs in
this file intentionally implement only import-time surfaces.  Any accidental
runtime use raises immediately so these tests cannot silently validate behavior
against a fake server or fake numerical stack.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "comfyui_llamacpp_contract_target"


class _UnexpectedRuntimeUse(AssertionError):
    """Raised when an import-only stub is used as a runtime dependency."""


class _StubRequestError(Exception):
    pass


class _StubConnectionError(_StubRequestError):
    pass


class _StubReadTimeout(_StubRequestError):
    pass


class _StubTimeout(_StubRequestError):
    pass


class _StubHTTPError(_StubRequestError):
    def __init__(self, *args, response=None, **kwargs):
        super().__init__(*args)
        self.response = response


def _unexpected_call(*args, **kwargs):
    raise _UnexpectedRuntimeUse(
        "An import-only test stub was used for runtime behavior. "
        "Inject an explicit fake into the behavior test instead."
    )


def _install_requests_stub() -> None:
    if importlib.util.find_spec("requests") is not None:
        return

    requests = types.ModuleType("requests")
    requests.get = _unexpected_call
    requests.post = _unexpected_call
    requests.delete = _unexpected_call
    requests.Session = _unexpected_call
    requests.exceptions = types.SimpleNamespace(
        RequestException=_StubRequestError,
        ConnectionError=_StubConnectionError,
        ReadTimeout=_StubReadTimeout,
        Timeout=_StubTimeout,
        HTTPError=_StubHTTPError,
    )
    sys.modules["requests"] = requests


def _install_psutil_stub() -> None:
    if importlib.util.find_spec("psutil") is not None:
        return

    psutil = types.ModuleType("psutil")
    psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    psutil.AccessDenied = type("AccessDenied", (Exception,), {})
    psutil.TimeoutExpired = type("TimeoutExpired", (Exception,), {})
    psutil.process_iter = lambda *args, **kwargs: []
    psutil.Process = _unexpected_call
    sys.modules["psutil"] = psutil


def _install_numpy_stub() -> None:
    if importlib.util.find_spec("numpy") is not None:
        return

    numpy = types.ModuleType("numpy")
    numpy.uint8 = object()
    numpy.asarray = _unexpected_call
    sys.modules["numpy"] = numpy


def _install_pillow_stub() -> None:
    if importlib.util.find_spec("PIL") is not None:
        return

    pil = types.ModuleType("PIL")
    pil.__path__ = []
    image = types.ModuleType("PIL.Image")
    image.fromarray = _unexpected_call
    pil.Image = image
    sys.modules["PIL"] = pil
    sys.modules["PIL.Image"] = image


def _install_aiohttp_stub() -> None:
    if importlib.util.find_spec("aiohttp") is not None:
        return

    aiohttp = types.ModuleType("aiohttp")

    class _Response:
        def __init__(self, status=200, **kwargs):
            self.status = status

    class _Web:
        Request = object
        Response = _Response

        @staticmethod
        def middleware(function):
            return function

    aiohttp.web = _Web
    sys.modules["aiohttp"] = aiohttp


class _StubRoutes:
    """Minimal PromptServer route table used only during package import."""

    def _decorator(self, method: str, path: str):
        def register(function):
            return function

        return register

    def get(self, path: str):
        return self._decorator("GET", path)

    def post(self, path: str):
        return self._decorator("POST", path)

    def delete(self, path: str):
        return self._decorator("DELETE", path)


def _install_comfy_stubs(model_root: Path) -> None:
    comfy = types.ModuleType("comfy")
    comfy.__path__ = []
    model_management = types.ModuleType("comfy.model_management")

    class InterruptProcessingException(BaseException):
        pass

    model_management.InterruptProcessingException = InterruptProcessingException
    model_management.throw_exception_if_processing_interrupted = lambda: None
    model_management.unload_all_models = _unexpected_call
    model_management.soft_empty_cache = _unexpected_call
    comfy.model_management = model_management

    folder_paths = types.ModuleType("folder_paths")
    folder_paths.models_dir = str(model_root.parent.parent)
    folder_paths.folder_names_and_paths = {}

    def add_model_folder_path(name: str, path: str, is_default: bool = False):
        paths, extensions = folder_paths.folder_names_and_paths.setdefault(name, ([], set()))
        if path not in paths:
            if is_default:
                paths.insert(0, path)
            else:
                paths.append(path)

    def get_folder_paths(name: str):
        return list(folder_paths.folder_names_and_paths[name][0])

    folder_paths.add_model_folder_path = add_model_folder_path
    folder_paths.get_folder_paths = get_folder_paths

    server = types.ModuleType("server")
    app = types.SimpleNamespace(middlewares=[])
    server.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(
            app=app,
            routes=_StubRoutes(),
            loop=None,
        )
    )

    sys.modules["comfy"] = comfy
    sys.modules["comfy.model_management"] = model_management
    sys.modules["folder_paths"] = folder_paths
    sys.modules["server"] = server


def _remove_target_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == PACKAGE_NAME or module_name.startswith(f"{PACKAGE_NAME}."):
            sys.modules.pop(module_name, None)


@pytest.fixture(scope="session")
def model_root() -> Iterator[Path]:
    temporary_root = Path(tempfile.mkdtemp(prefix="comfyui-llamacpp-contracts-"))
    root = temporary_root / "models" / "LLM" / "gguf"
    root.mkdir(parents=True)
    (root / "legacy-model.gguf").touch()
    (root / "vision").mkdir()
    (root / "vision" / "vision-model.gguf").touch()
    (root / "vision" / "mmproj-vision.gguf").touch()
    try:
        yield root
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


@pytest.fixture(scope="session")
def node_package(model_root: Path):
    _install_requests_stub()
    _install_psutil_stub()
    _install_numpy_stub()
    _install_pillow_stub()
    _install_aiohttp_stub()
    _install_comfy_stubs(model_root)
    _remove_target_modules()

    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to create an import spec for the node package")

    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE_NAME] = package
    spec.loader.exec_module(package)

    # The released model manager derives the model directory from its package
    # location.  Redirect that public discovery boundary to the isolated model
    # root.  A refactored folder_paths-aware catalog will instead use the stub
    # registered above and needs no special handling here.
    legacy_model_manager = sys.modules.get(f"{PACKAGE_NAME}.model_manager")
    if legacy_model_manager is not None and hasattr(legacy_model_manager, "get_models_directory"):
        legacy_model_manager.get_models_directory = lambda: str(model_root)

    try:
        yield package
    finally:
        _remove_target_modules()


@pytest.fixture(scope="session")
def released_contracts() -> dict:
    path = REPO_ROOT / "tests" / "fixtures" / "workflows" / "v0_2_1_contracts.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def historical_workflow() -> dict:
    path = REPO_ROOT / "tests" / "fixtures" / "workflows" / "v0_2_1_saved_workflow.json"
    return json.loads(path.read_text(encoding="utf-8"))
