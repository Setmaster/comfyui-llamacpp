"""Portable text conversation history, independent of live runtime state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_MESSAGES = 128
MAX_MESSAGES_BYTES = 1_048_576
MAX_MESSAGE_CHARS = 262_144
MESSAGE_ROLES = ("system", "user", "assistant")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate messages key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


@dataclass(frozen=True, slots=True)
class TextMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or self.role not in MESSAGE_ROLES:
            raise ValueError("message role must be system, user or assistant")
        if not isinstance(self.content, str):
            raise TypeError("message content must be text")
        if not self.content or len(self.content) > MAX_MESSAGE_CHARS:
            raise ValueError(f"message content must contain 1 to {MAX_MESSAGE_CHARS} characters")
        self.content.encode("utf-8")

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class ConversationMessages:
    """History preceding Generate's current prompt; never accumulates implicitly."""

    messages: tuple[TextMessage, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.messages, (str, bytes)) or not isinstance(self.messages, Sequence):
            raise TypeError("messages must be a sequence")
        if len(self.messages) > MAX_MESSAGES:
            raise ValueError(f"history exceeds {MAX_MESSAGES} messages")
        normalized = tuple(self.messages)
        for index, message in enumerate(normalized):
            if not isinstance(message, TextMessage):
                raise TypeError("history entries must be TextMessage values")
            if message.role == "system" and index != 0:
                raise ValueError("a system message is allowed only at the beginning of history")
        object.__setattr__(self, "messages", normalized)
        self.to_json()

    @property
    def has_system(self) -> bool:
        return bool(self.messages and self.messages[0].role == "system")

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def append(self, role: str, content: str) -> ConversationMessages:
        return ConversationMessages((*self.messages, TextMessage(role, content)))

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "messages": [m.as_dict() for m in self.messages]}

    def to_json(self) -> str:
        text = json.dumps(
            self.as_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        if len(text.encode("utf-8")) > MAX_MESSAGES_BYTES:
            raise ValueError(f"history exceeds {MAX_MESSAGES_BYTES} serialized bytes")
        return text

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ConversationMessages:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "messages"}:
            raise ValueError("history must contain exactly schema_version and messages")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("history schema_version must be 1")
        items = value["messages"]
        if not isinstance(items, list) or len(items) > MAX_MESSAGES:
            raise ValueError(f"messages must be a list of at most {MAX_MESSAGES} entries")
        result = []
        for item in items:
            if not isinstance(item, Mapping) or set(item) != {"role", "content"}:
                raise ValueError("each message must contain exactly role and content")
            result.append(TextMessage(item["role"], item["content"]))
        return cls(tuple(result))

    @classmethod
    def from_json(cls, text: str) -> ConversationMessages:
        if not isinstance(text, str):
            raise TypeError("history JSON must be text")
        if len(text.encode("utf-8")) > MAX_MESSAGES_BYTES:
            raise ValueError(f"history exceeds {MAX_MESSAGES_BYTES} serialized bytes")
        return cls.from_dict(json.loads(text, object_pairs_hook=_object, parse_constant=_constant))
