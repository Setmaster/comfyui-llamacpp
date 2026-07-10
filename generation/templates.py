"""Version-tolerant backend template loading."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

DEFAULT_TEMPLATES_PATH = Path(__file__).resolve().parents[1] / "web" / "templates.json"
_lock = threading.RLock()
_cache: tuple[Path, int, dict[str, dict[str, str]]] | None = None


def _validate_templates(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise ValueError("Template file must contain an object")
    result: dict[str, dict[str, str]] = {}
    for name, template in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Template names must be non-empty strings")
        if not isinstance(template, dict):
            raise ValueError(f"Template {name!r} must be an object")
        system_prompt = template.get("system_prompt", "")
        prompt = template.get("prompt", "")
        if not isinstance(system_prompt, str) or not isinstance(prompt, str):
            raise ValueError(f"Template {name!r} fields must be strings")
        result[name] = {"system_prompt": system_prompt, "prompt": prompt}
    if "Empty" not in result:
        result = {"Empty": {"system_prompt": "", "prompt": ""}, **result}
    return result


def load_templates(path: str | Path | None = None) -> dict[str, dict[str, str]]:
    global _cache
    template_path = Path(path or DEFAULT_TEMPLATES_PATH).resolve()
    try:
        mtime_ns = template_path.stat().st_mtime_ns
    except OSError:
        return {"Empty": {"system_prompt": "", "prompt": ""}}

    with _lock:
        if _cache and _cache[0] == template_path and _cache[1] == mtime_ns:
            return {name: values.copy() for name, values in _cache[2].items()}
        try:
            parsed = json.loads(template_path.read_text(encoding="utf-8"))
            templates = _validate_templates(parsed)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[llama.cpp] Warning: Could not load templates: {exc}")
            templates = {"Empty": {"system_prompt": "", "prompt": ""}}
        _cache = (template_path, mtime_ns, templates)
        return {name: values.copy() for name, values in templates.items()}


def get_template_names(path: str | Path | None = None) -> list[str]:
    return list(load_templates(path))


def apply_template(
    template_name: str,
    prompt: str,
    system_prompt: str,
    *,
    path: str | Path | None = None,
) -> tuple[str, str]:
    """Fill only blank fields so serialized widget values remain authoritative."""

    template = load_templates(path).get(template_name)
    if not template or template_name == "Empty":
        return prompt, system_prompt
    return prompt or template["prompt"], system_prompt or template["system_prompt"]
