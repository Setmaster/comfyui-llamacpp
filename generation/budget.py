"""Pure, versioned request budgeting without runtime or prompt side effects.

``input + requested output <= effective slot context`` is an explicit policy,
not a claim that every llama.cpp configuration rejects larger requests. Context
shifting and upstream generation limits can differ. No policy alters a request.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

MAX_BUDGET_JSON_BYTES = 65_536
MAX_TOKEN_COUNT = 2**63 - 1
INPUT_TOKEN_SOURCE = "/v1/chat/completions/input_tokens"
CONTEXT_LIMIT_SOURCE = "/props.default_generation_settings.n_ctx"


class RequestBudgetStatus(str, Enum):
    FIT = "fit"
    OVERFLOW = "overflow"
    UNKNOWN = "unknown"


class RequestBudgetPolicy(str, Enum):
    REPORT = "report"
    ENFORCE = "enforce"


class RequestBudgetError(ValueError):
    """The caller's explicit enforcement policy could not be satisfied."""


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_TOKEN_COUNT:
        raise ValueError(f"{name} must be an integer between {minimum} and {MAX_TOKEN_COUNT}")
    return value


def _text(value: Any, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must contain 1 to {maximum} characters")
    value.encode("utf-8")
    return value


def effective_context_limit(props: Mapping[str, Any]) -> int | None:
    """Only the effective per-slot limit is authoritative, never total/train context."""

    if not isinstance(props, Mapping):
        raise TypeError("props must be an object")
    settings = props.get("default_generation_settings")
    value = settings.get("n_ctx") if isinstance(settings, Mapping) else None
    # Router-root props use a dummy zero. Missing/invalid limits stay unknown.
    return value if type(value) is int and 0 < value <= MAX_TOKEN_COUNT else None


@dataclass(frozen=True, slots=True)
class RequestBudget:
    input_tokens: int | None
    context_limit: int | None
    max_tokens: int
    model: str | None = None
    input_source: str | None = None
    context_source: str | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.input_tokens is not None:
            _integer(self.input_tokens, "input_tokens")
        if self.context_limit is not None:
            _integer(self.context_limit, "context_limit", minimum=1)
        _integer(self.max_tokens, "max_tokens", minimum=1)
        for name in ("model", "input_source", "context_source"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name)
        if isinstance(self.warnings, (str, bytes)) or not isinstance(self.warnings, Sequence):
            raise TypeError("warnings must be a sequence of text")
        if len(self.warnings) > 32:
            raise ValueError("warnings must contain at most 32 entries")
        object.__setattr__(self, "warnings", tuple(_text(v, "warning") for v in self.warnings))
        self.to_json()

    @property
    def remaining(self) -> int | None:
        if self.input_tokens is None or self.context_limit is None:
            return None
        return self.context_limit - self.input_tokens - self.max_tokens

    @property
    def status(self) -> RequestBudgetStatus:
        remaining = self.remaining
        if remaining is None:
            return RequestBudgetStatus.UNKNOWN
        return RequestBudgetStatus.FIT if remaining >= 0 else RequestBudgetStatus.OVERFLOW

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "context_limit": self.context_limit,
            "max_tokens": self.max_tokens,
            "remaining": self.remaining,
            "status": self.status.value,
            "input_source": self.input_source,
            "context_source": self.context_source,
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        text = json.dumps(
            self.as_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        if len(text.encode("utf-8")) > MAX_BUDGET_JSON_BYTES:
            raise ValueError(f"budget JSON exceeds {MAX_BUDGET_JSON_BYTES} bytes")
        return text

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RequestBudget:
        expected = {
            "schema_version",
            "model",
            "input_tokens",
            "context_limit",
            "max_tokens",
            "remaining",
            "status",
            "input_source",
            "context_source",
            "warnings",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("budget must contain exactly the versioned budget fields")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("budget schema_version must be 1")
        result = cls(
            **{key: value[key] for key in expected - {"schema_version", "remaining", "status"}}
        )
        remaining = value["remaining"]
        if (remaining is not None and type(remaining) is not int) or remaining != result.remaining:
            raise ValueError("budget remaining does not match its input and output allowance")
        if value["status"] != result.status.value:
            raise ValueError("budget status does not match its input and output allowance")
        return result

    @classmethod
    def from_json(cls, text: str) -> RequestBudget:
        if not isinstance(text, str):
            raise TypeError("budget JSON must be text")
        if len(text.encode("utf-8")) > MAX_BUDGET_JSON_BYTES:
            raise ValueError(f"budget JSON exceeds {MAX_BUDGET_JSON_BYTES} bytes")

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError(f"duplicate budget key: {key}")
                result[key] = item
            return result

        def reject(value: str) -> None:
            raise ValueError(f"invalid JSON constant: {value}")

        return cls.from_dict(json.loads(text, object_pairs_hook=unique, parse_constant=reject))


def enforce_request_budget(
    budget: RequestBudget,
    policy: RequestBudgetPolicy | str = RequestBudgetPolicy.REPORT,
) -> RequestBudget:
    """Return the same value, or fail closed only when enforcement was selected."""

    if not isinstance(budget, RequestBudget):
        raise TypeError("budget must be RequestBudget")
    policy = RequestBudgetPolicy(policy)
    if policy == RequestBudgetPolicy.ENFORCE:
        if budget.status == RequestBudgetStatus.UNKNOWN:
            raise RequestBudgetError(
                "request budget is unknown; enforcement requires count and context"
            )
        if budget.status == RequestBudgetStatus.OVERFLOW:
            raise RequestBudgetError(
                f"request input plus requested output exceeds the effective slot context "
                f"by {-budget.remaining} tokens"
            )
    return budget
