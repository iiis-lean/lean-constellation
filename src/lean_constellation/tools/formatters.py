"""Small reusable format helpers for Agent-facing tool views."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel


def bounded_text(value: str | None, *, limit: int = 4000) -> str | None:
    """Return a bounded text value suitable for Agent-visible output."""

    if value is None or len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def compact_model(value: Any) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
    """Convert common internal values into simple JSON-compatible views."""

    if isinstance(value, BaseModel):
        return compact_model(value.model_dump())
    if isinstance(value, Mapping):
        return {str(key): compact_model(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [compact_model(item) for item in value]
    if isinstance(value, str):
        return bounded_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def summarize_items(label: str, count: int) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {label}{suffix}"
