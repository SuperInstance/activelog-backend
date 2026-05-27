"""QueryEngine — filter, aggregate, and search log entries."""

from __future__ import annotations

import operator
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .logger import LogEntry, LogLevel
from .store import LogStore


# Operator map for field comparisons
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "in": lambda v, col: v in col,
    "contains": lambda v, sub: sub in v,
}


@dataclass
class FilterSpec:
    """A single filter condition."""

    field: str  # dotted path into entry, e.g. "context.user_id"
    op: str = "eq"  # one of _OPS keys
    value: Any = None

    def match(self, entry: LogEntry) -> bool:
        op_fn = _OPS.get(self.op)
        if op_fn is None:
            raise ValueError(f"Unknown operator: {self.op!r}")
        val = _resolve(entry, self.field)
        if val is _MISSING:
            return False
        return op_fn(val, self.value)


_MISSING = object()


def _resolve(entry: LogEntry, dotted: str) -> Any:
    """Resolve a dotted path like ``level.name`` or ``context.user_id``."""
    parts = dotted.split(".")
    obj: Any = entry
    for p in parts:
        if isinstance(obj, Mapping):
            if p not in obj:
                return _MISSING
            obj = obj[p]
        else:
            obj = getattr(obj, p, _MISSING)
            if obj is _MISSING:
                return _MISSING
    return obj


@dataclass
class QueryEngine:
    """High-level query interface over a :class:`LogStore`.

    Examples
    --------
    >>> engine = QueryEngine(store)
    >>> engine.filter(level="ERROR")
    >>> engine.filter(context={"user_id": 42})
    >>> engine.aggregate("count_by_level")
    """

    store: LogStore

    # -- filtering ----------------------------------------------------

    def filter(
        self,
        *,
        level: LogLevel | str | None = None,
        source: str | None = None,
        trace_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        tags: Sequence[str] = (),
        context: Mapping[str, Any] | None = None,
        message_contains: str | None = None,
        specs: Sequence[FilterSpec] = (),
    ) -> list[LogEntry]:
        """Return entries matching **all** supplied criteria."""
        if isinstance(level, str):
            level = LogLevel[level]

        results = self.store.by_time_range(start, end)

        if level is not None:
            results = [e for e in results if e.level == level]
        if source is not None:
            results = [e for e in results if e.source == source]
        if trace_id is not None:
            results = [e for e in results if e.trace_id == trace_id]
        if tags:
            results = [e for e in results if all(t in e.tags for t in tags)]
        if context:
            results = [
                e for e in results
                if all(e.context.get(k) == v for k, v in context.items())
            ]
        if message_contains:
            q = message_contains.lower()
            results = [e for e in results if q in e.message.lower()]
        if specs:
            for s in specs:
                results = [e for e in results if s.match(e)]

        return results

    # -- search -------------------------------------------------------

    def search(self, query: str, *, limit: int = 100) -> list[LogEntry]:
        """Full-text search across message and string context values."""
        q = query.lower()
        matches: list[LogEntry] = []
        for entry in self.store:
            if q in entry.message.lower():
                matches.append(entry)
                continue
            # search string context values
            for v in entry.context.values():
                if isinstance(v, str) and q in v.lower():
                    matches.append(entry)
                    break
            if len(matches) >= limit:
                break
        return matches

    # -- aggregation --------------------------------------------------

    def count_by_level(self, entries: Sequence[LogEntry] | None = None) -> dict[str, int]:
        items = entries or self.store.all()
        c: Counter[str] = Counter()
        for e in items:
            c[e.level.name] += 1
        return dict(c)

    def count_by_source(self, entries: Sequence[LogEntry] | None = None) -> dict[str, int]:
        items = entries or self.store.all()
        c: Counter[str] = Counter()
        for e in items:
            if e.source:
                c[e.source] += 1
        return dict(c)

    def count_by_field(
        self, context_key: str, entries: Sequence[LogEntry] | None = None
    ) -> dict[Any, int]:
        items = entries or self.store.all()
        c: Counter[Any] = Counter()
        for e in items:
            v = e.context.get(context_key)
            if v is not None:
                c[v] += 1
        return dict(c)

    def timeline(
        self,
        bucket_minutes: int = 5,
        entries: Sequence[LogEntry] | None = None,
    ) -> dict[str, int]:
        """Bucket entries into time windows and count per bucket."""
        items = entries or self.store.all()
        buckets: defaultdict[str, int] = defaultdict(int)
        for e in items:
            ts = e.timestamp
            bucket = ts.replace(
                minute=(ts.minute // bucket_minutes) * bucket_minutes,
                second=0,
                microsecond=0,
            )
            buckets[bucket.isoformat()] += 1
        return dict(sorted(buckets.items()))

    def top_messages(
        self, n: int = 10, entries: Sequence[LogEntry] | None = None
    ) -> list[tuple[str, int]]:
        items = entries or self.store.all()
        c = Counter(e.message for e in items)
        return c.most_common(n)

    def error_rate(self, window_minutes: int = 60) -> float:
        """Fraction of entries at WARN or higher in the last *window_minutes*."""
        from datetime import timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        recent = self.store.by_time_range(start=cutoff)
        if not recent:
            return 0.0
        errors = sum(1 for e in recent if e.level.value >= LogLevel.WARN.value)
        return errors / len(recent)
