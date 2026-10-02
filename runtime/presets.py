"""Validated, local-only subset of llama.cpp's b9957 router INI format.

Unknown options fail closed: acquiring models or changing process/API ownership
is outside this format. Rendered snapshots prevent later catalog reloads from
reading unvalidated edits to the operator's source file.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

MAX_PRESET_BYTES = 256 * 1024
MAX_PRESET_MODELS = 128

# canonical key -> (validation kind, accepted CLI/INI aliases, environment alias)
_OPTIONS = {
    "model": ("path", "m", "LLAMA_ARG_MODEL"),
    "mmproj": ("path", "mm", "LLAMA_ARG_MMPROJ"),
    "spec-draft-model": ("path", "md model-draft", "LLAMA_ARG_SPEC_DRAFT_MODEL"),
    "ctx-size": ("nonnegative", "c", "LLAMA_ARG_CTX_SIZE"),
    "gpu-layers": ("gpu", "ngl n-gpu-layers", "LLAMA_ARG_N_GPU_LAYERS"),
    "main-gpu": ("nonnegative", "mg", "LLAMA_ARG_MAIN_GPU"),
    "threads": ("positive", "t", "LLAMA_ARG_THREADS"),
    "batch-size": ("positive", "b", "LLAMA_ARG_BATCH"),
    "ubatch-size": ("positive", "ub", "LLAMA_ARG_UBATCH"),
    "parallel": ("nonnegative", "np", "LLAMA_ARG_N_PARALLEL"),
    "tensor-split": ("split", "ts", "LLAMA_ARG_TENSOR_SPLIT"),
    "flash-attn": ("flash", "fa", "LLAMA_ARG_FLASH_ATTN"),
    "mmap": ("bool", "", "LLAMA_ARG_MMAP"),
    "mmproj-auto": ("bool", "", "LLAMA_ARG_MMPROJ_AUTO"),
    "mmproj-offload": ("bool", "", "LLAMA_ARG_MMPROJ_OFFLOAD"),
    "fit": ("onoff", "", "LLAMA_ARG_FIT"),
    "sleep-idle-seconds": ("minus_one", "", "LLAMA_ARG_SLEEP_IDLE_SECONDS"),
    "cache-ram": ("minus_one", "cram", "LLAMA_ARG_CACHE_RAM"),
    "cache-type-k": ("cache", "ctk", "LLAMA_ARG_CACHE_TYPE_K"),
    "cache-type-v": ("cache", "ctv", "LLAMA_ARG_CACHE_TYPE_V"),
    "ctx-checkpoints": ("nonnegative", "ctxcp swa-checkpoints", "LLAMA_ARG_CTX_CHECKPOINTS"),
    "jinja": ("bool", "", "LLAMA_ARG_JINJA"),
    "temp": ("nonnegative_float", "temperature", "LLAMA_ARG_TEMP"),
    "top-k": ("nonnegative", "", "LLAMA_ARG_TOP_K"),
    "top-p": ("probability", "", "LLAMA_ARG_TOP_P"),
    "min-p": ("probability", "", "LLAMA_ARG_MIN_P"),
    "repeat-penalty": ("nonnegative_float", "", "LLAMA_ARG_REPEAT_PENALTY"),
    "seed": ("minus_one", "s", "LLAMA_ARG_SEED"),
    "reasoning-budget": ("minus_one", "", "LLAMA_ARG_THINK_BUDGET"),
    "spec-type": ("spec", "", "LLAMA_ARG_SPEC_TYPE"),
    "spec-draft-n-max": ("nonnegative", "", "LLAMA_ARG_SPEC_DRAFT_N_MAX"),
    "spec-draft-n-min": ("nonnegative", "", "LLAMA_ARG_SPEC_DRAFT_N_MIN"),
    "load-on-startup": ("bool", "", ""),
    "stop-timeout": ("nonnegative", "", ""),
}
_ALIASES: dict[str, tuple[str, bool]] = {}
for _key, (_kind, _aliases, _env) in _OPTIONS.items():
    for _alias in (_key, *_aliases.split(), *([_env] if _env else [])):
        _ALIASES[_alias.lower().replace("_", "-")] = (_key, False)
for _negative, _positive in {
    "no-mmap": "mmap",
    "no-mmproj": "mmproj-auto",
    "no-mmproj-auto": "mmproj-auto",
    "no-mmproj-offload": "mmproj-offload",
    "no-jinja": "jinja",
}.items():
    _ALIASES[_negative] = (_positive, True)


def _option(key: str) -> tuple[str, bool]:
    normalized = key.lower().replace("_", "-")
    if normalized not in _ALIASES:
        raise ValueError(
            f"Unsupported local preset option {key!r}; see docs/router-presets.md. "
            "Acquisition, credentials, API/process settings and unknown options are not allowed."
        )
    return _ALIASES[normalized]


def _value(key: str, value: str, base: Path, *, negate: bool = False) -> str:
    kind = _OPTIONS[key][0]
    if not value or any(ord(char) < 32 for char in value):
        raise ValueError(f"Preset option {key!r} requires one nonempty value")
    if kind == "path":
        if "://" in value or value.startswith(("\\\\", "//")):
            raise ValueError(f"Preset {key!r} requires a local file path")
        path = Path(value)
        if not path.is_absolute():
            path = base / path
        path = path.resolve()
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise ValueError(f"Preset {key!r} must name an existing local GGUF file: {path}")
        rendered = str(path)
        if any(char in rendered for char in "\r\n;#"):
            raise ValueError("Preset file paths cannot contain INI comment/newline characters")
        return rendered
    if kind == "bool":
        if value.lower() not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            raise ValueError(f"Preset {key!r} must be a Boolean")
        enabled = value.lower() in {"true", "1", "yes", "on"}
        return "true" if enabled != negate else "false"
    if kind in {"positive", "nonnegative", "minus_one", "gpu"}:
        if kind == "gpu" and value in {"auto", "all"}:
            return value
        minimum = {"positive": 1, "nonnegative": 0, "minus_one": -1, "gpu": -1}[kind]
        if not re.fullmatch(r"-?\d+", value) or not minimum <= int(value) <= 2**31 - 1:
            raise ValueError(f"Preset {key!r} requires an integer >= {minimum}")
        return str(int(value))
    if kind in {"nonnegative_float", "probability", "split"}:
        try:
            values = (
                [float(part) for part in re.split(r"[,/]", value)]
                if kind == "split"
                else [float(value)]
            )
        except ValueError as exc:
            raise ValueError(f"Preset {key!r} requires finite numeric values") from exc
        if not all(math.isfinite(number) and number >= 0 for number in values):
            raise ValueError(f"Preset {key!r} requires finite nonnegative values")
        if kind == "probability" and values[0] > 1:
            raise ValueError(f"Preset {key!r} must be between 0 and 1")
        if kind == "split" and (len(values) > 16 or not any(values)):
            raise ValueError("Preset tensor-split requires 1 to 16 weights with a positive sum")
        return ",".join(str(number) for number in values)
    choices = {
        "flash": {"on", "off", "auto"},
        "onoff": {"on", "off"},
        "cache": {"f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"},
        "spec": {
            "none",
            "draft-simple",
            "draft-eagle3",
            "draft-mtp",
            "ngram-simple",
            "ngram-map-k",
            "ngram-map-k4v",
            "ngram-mod",
            "ngram-cache",
        },
    }
    if kind == "spec":
        if any(part not in choices[kind] for part in value.split(",")):
            raise ValueError("Unsupported preset spec-type; see docs/router-presets.md")
    elif value not in choices[kind]:
        raise ValueError(f"Unsupported value for preset {key!r}: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class LocalPreset:
    source_path: str
    source_sha256: str
    text: str
    models: tuple[str, ...]
    required_flags: tuple[str, ...]


def load_local_preset(path: str) -> LocalPreset:
    try:
        source = Path(path).resolve()
        if not source.is_file():
            raise ValueError("Local preset must be an existing regular file")
        with source.open("rb") as handle:
            raw = handle.read(MAX_PRESET_BYTES + 1)
        if len(raw) > MAX_PRESET_BYTES:
            raise ValueError("Local preset exceeds 256 KiB")
        text = raw.decode("utf-8")
    except (OSError, UnicodeError, RuntimeError) as exc:
        raise ValueError(f"Cannot read local UTF-8 preset file: {path}") from exc
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    for number, raw_line in enumerate(text.splitlines(), 1):
        line = re.split(r"[;#]", raw_line, maxsplit=1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            if (
                not name
                or any(char in name for char in "[]")
                or any(ord(char) < 32 for char in name)
            ):
                raise ValueError(f"Invalid preset section on line {number}")
            # b9957 canonicalizes the last colon's quantization tag.
            if ":" in name:
                prefix, tag = name.rsplit(":", 1)
                match = re.search(r"[-.]([A-Z0-9_]+)$", tag, re.IGNORECASE)
                name = prefix + ":" + (match.group(1) if match else tag).upper()
            if name in sections:
                raise ValueError(f"Duplicate preset section {name!r}")
            sections[name] = {}
            current = name
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*)", line)
        if match is None:
            raise ValueError(f"Invalid preset syntax on line {number}; use key = value")
        key, value = match.groups()
        if current is None:
            if key == "version" and value == "1":
                continue
            raise ValueError("Only version = 1 is allowed before preset sections")
        canonical, negate = _option(key)
        if canonical in sections[current]:
            raise ValueError(f"Duplicate preset option {canonical!r} in [{current}]")
        sections[current][canonical] = _value(
            canonical, value.strip(), source.parent, negate=negate
        )
    models = tuple(name for name in sections if name != "*")
    if not models or len(models) > MAX_PRESET_MODELS:
        raise ValueError("Local preset requires 1 to 128 named model sections")
    defaults = sections.get("*", {})
    for name in models:
        effective = {**defaults, **sections[name]}
        if "model" not in effective:
            raise ValueError(f"Preset [{name}] requires a local model = path.gguf")
    rendered = (
        "\n\n".join(
            f"[{name}]\n" + "\n".join(f"{key} = {value}" for key, value in values.items())
            for name, values in sections.items()
        )
        + "\n"
    )
    required_flags = tuple(
        sorted(
            {
                "--" + key
                for values in sections.values()
                for key in values
                if key not in {"load-on-startup", "stop-timeout"}
            }
        )
    )
    return LocalPreset(str(source), sha256(raw).hexdigest(), rendered, models, required_flags)


def normalize_preset_extra_args(arguments: tuple[str, ...]) -> tuple[str, ...]:
    """Allow explicit local tuning, without allowing an argv acquisition escape."""
    index = 0
    normalized_args: list[str] = []
    while index < len(arguments):
        argument = arguments[index]
        if not argument.startswith("-"):
            raise ValueError("Preset extra_args must contain supported named options")
        key, equals, inline = argument.lstrip("-").partition("=")
        normalized = key.lower().replace("_", "-")
        if normalized in {"metrics", "props", "log-timestamps", "log-prefix", "offline"}:
            if equals:
                raise ValueError(f"Preset extra_args flag {key!r} takes no value")
            normalized_args.append("--" + normalized)
            index += 1
            continue
        canonical, negate = _option(key)
        if canonical in {"load-on-startup", "stop-timeout"}:
            raise ValueError(f"{key!r} is an INI-only preset option")
        if _OPTIONS[canonical][0] == "bool":
            if equals:
                raise ValueError(f"Preset extra_args flag {key!r} takes no value")
            normalized_args.append("--" + ("no-" if negate else "") + canonical)
            index += 1
            continue
        if equals:
            value = inline
        else:
            index += 1
            if index == len(arguments):
                raise ValueError(f"Preset extra_args option {key!r} needs a value")
            value = arguments[index]
        normalized_args.extend(
            ("--" + canonical, _value(canonical, value, Path.cwd(), negate=negate))
        )
        index += 1
    return tuple(normalized_args)


def local_preset_environment() -> dict[str, str]:
    """Use declared launch settings, keeping platform/CUDA/client environments."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("LLAMA_ARG_") and key.upper() != "LLAMA_API_KEY"
    }


class PresetSnapshot:
    """One owned temporary INI; cleanup never traverses a supplied directory."""

    def __init__(self, preset: LocalPreset):
        self.path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix="llamacpp-preset-",
                suffix=".ini",
                delete=False,
            ) as handle:
                self.path = handle.name
                handle.write(preset.text)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.path is not None:
            Path(self.path).unlink(missing_ok=True)
            self.path = None
