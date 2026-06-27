"""Common domain helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model for application-owned truth and view objects."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with second precision."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
