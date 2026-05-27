"""LogStore — in-memory storage with time-range and field queries."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator, Sequence

from .logger import LogEntry, LogLevel


@dataclass
class LogStore:
    """Thread-safe in-memory log store.

    Supports append, time-range queries, field lookups, and
    subscription-style observers.
    """

    max_entries: int = 100_000
    _entries: list[LogEntry] = field(default_factory=list, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _observers: list[Callable[[LogEntry], None]] = field(
        default_factory=list, init=False, repr=False
    )

    # -- ingestion ----------------------------------------------------

    def add(self, entry: LogEntry) -> None:
        with self._lock:
            if len(self._entries) >= self.max_entries:
                # drop oldest 10%
                drop = max(1, self.max_entries // 10)
                self._entries = self._entries[drop:]
            self._entries.append(entry)
        for obs in self._observers:
            obs(entry)

    def add_many(self, entries: Sequence[LogEntry]) -> None:
        for e in entries:
            self.add(e)

    def subscribe(self, observer: Callable[[LogEntry], None]) -> None:
        self._observers.append(observer)

    def unsubscribe(self, observer: Callable[[LogEntry], None]) -> None:
        self._observers = [o for o in self._observers if o is not observer]

    # -- queries ------------------------------------------------------

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def all(self) -> list[LogEntry]:
        with self._lock:
            return list(self._entries)

    def get(self, entry_id: str) -> LogEntry | None:
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    return e
        return None

    def by_level(self, level: LogLevel) -> list[LogEntry]:
        with self._lock:
            return [e for e in self._entries if e.level == level]

    def by_time_range(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[LogEntry]:
        with self._lock:
            results: list[LogEntry] = []
            for e in self._entries:
                if start and e.timestamp < start:
                    continue
                if end and e.timestamp > end:
                    continue
                results.append(e)
            return results

    def by_field(self, key: str, value: Any) -> list[LogEntry]:
        """Match entries where ``entry.context[key] == value``."""
        with self._lock:
            return [
                e for e in self._entries if e.context.get(key) == value
            ]

    def by_tag(self, tag: str) -> list[LogEntry]:
        with self._lock:
            return [e for e in self._entries if tag in e.tags]

    def by_source(self, source: str) -> list[LogEntry]:
        with self._lock:
            return [e for e in self._entries if e.source == source]

    def by_trace(self, trace_id: str) -> list[LogEntry]:
        with self._lock:
            return [e for e in self._entries if e.trace_id == trace_id]

    def search(self, predicate: Callable[[LogEntry], bool]) -> list[LogEntry]:
        with self._lock:
            return [e for e in self._entries if predicate(e)]

    def text_search(self, query: str) -> list[LogEntry]:
        """Case-insensitive substring search on message."""
        q = query.lower()
        with self._lock:
            return [e for e in self._entries if q in e.message.lower()]

    def clear(self) -> int:
        with self._lock:
            n = len(self._entries)
            self._entries.clear()
            return n

    def __iter__(self) -> Iterator[LogEntry]:
        return iter(self.all())

    def __len__(self) -> int:
        return self.count
