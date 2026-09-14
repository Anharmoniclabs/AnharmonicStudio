"""Bounded byte-budget LRU caches for decoded and derived media."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

import numpy as np


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CacheStats:
    items: int
    bytes: int
    budget_bytes: int
    hits: int
    misses: int
    evictions: int


class MediaLRU(Generic[T]):
    """Small deterministic LRU with an explicit byte budget.

    Callers supply the byte cost so this class can also cache waveform summaries,
    analysis arrays and native media handles rather than only NumPy audio.
    """

    def __init__(self, budget_bytes: int):
        if budget_bytes <= 0:
            raise ValueError("media cache budget must be positive")
        self.budget_bytes = int(budget_bytes)
        self._items: OrderedDict[str, tuple[T, int]] = OrderedDict()
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> T | None:
        item = self._items.get(key)
        if item is None:
            self._misses += 1
            return None
        self._items.move_to_end(key)
        self._hits += 1
        return item[0]

    def put(self, key: str, value: T, size_bytes: int) -> bool:
        if not key:
            raise ValueError("media cache key is required")
        if size_bytes < 0:
            raise ValueError("media cache item size cannot be negative")
        size = int(size_bytes)
        existing = self._items.pop(key, None)
        if existing is not None:
            self._bytes -= existing[1]
        if size > self.budget_bytes:
            return False
        self._items[key] = (value, size)
        self._bytes += size
        self._items.move_to_end(key)
        while self._bytes > self.budget_bytes:
            _, (_, removed_size) = self._items.popitem(last=False)
            self._bytes -= removed_size
            self._evictions += 1
        return True

    def remove(self, key: str) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            self._bytes -= item[1]

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def stats(self) -> CacheStats:
        return CacheStats(
            items=len(self._items),
            bytes=self._bytes,
            budget_bytes=self.budget_bytes,
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
        )


def numpy_nbytes(value: np.ndarray) -> int:
    return int(np.asarray(value).nbytes)
