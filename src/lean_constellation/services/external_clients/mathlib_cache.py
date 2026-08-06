"""Process-local bounded cache used by the Mathlib lookup facade."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MathlibCacheStats:
    """Small diagnostic snapshot for the process-local lookup cache."""

    entries: int
    hits: int
    misses: int
    bypasses: int
    evictions: int


class MathlibLookupCache:
    """An LRU cache for successful, already-normalized Mathlib results."""

    def __init__(self, *, max_entries: int = 256) -> None:
        if max_entries < 1:
            raise ValueError("Mathlib cache max_entries must be >= 1")
        self.max_entries = max_entries
        self._entries: OrderedDict[str, Any] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._bypasses = 0
        self._evictions = 0

    def get(self, key: str | None) -> Any | None:
        if key is None:
            self._bypasses += 1
            return None
        if key not in self._entries:
            self._misses += 1
            return None
        self._hits += 1
        value = self._entries.pop(key)
        self._entries[key] = value
        return value

    def put(self, key: str | None, value: Any) -> None:
        if key is None:
            return
        self._entries.pop(key, None)
        self._entries[key] = value
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self._evictions += 1

    def clear(self) -> None:
        self._entries.clear()

    def stats(self) -> MathlibCacheStats:
        return MathlibCacheStats(
            entries=len(self._entries),
            hits=self._hits,
            misses=self._misses,
            bypasses=self._bypasses,
            evictions=self._evictions,
        )
