"""ActiveLogger — structured logging with levels, contexts, and enrichment."""

from __future__ import annotations

import copy
import json
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, MutableMapping, Sequence


class LogLevel(Enum):
    """Log severity levels, ordered from most to least verbose."""

    TRACE = 10
    DEBUG = 20
    INFO = 30
    WARN = 40
    ERROR = 50
    FATAL = 60


# Alias for convenience
TRACE = LogLevel.TRACE
DEBUG = LogLevel.DEBUG
INFO = LogLevel.INFO
WARN = LogLevel.WARN
ERROR = LogLevel.ERROR
FATAL = LogLevel.FATAL


@dataclass(slots=True)
class LogEntry:
    """A single structured log record."""

    id: str
    timestamp: datetime
    level: LogLevel
    message: str
    context: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    source: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    # --- convenience --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.name,
            "message": self.message,
            "context": self.context,
        }
        if self.tags:
            d["tags"] = list(self.tags)
        if self.source:
            d["source"] = self.source
        if self.trace_id:
            d["trace_id"] = self.trace_id
        if self.span_id:
            d["span_id"] = self.span_id
        if self.parent_span_id:
            d["parent_span_id"] = self.parent_span_id
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


@dataclass
class ActiveLogger:
    """Structured logger that emits :class:`LogEntry` objects to a handler list.

    Parameters
    ----------
    name:
        Identifier for this logger (e.g. ``"auth.service"``).
    min_level:
        Minimum severity to emit.  Defaults to :data:`LogLevel.INFO`.
    default_context:
        Key–value pairs merged into every entry.
    handlers:
        Callables that receive each emitted :class:`LogEntry`.
        If *None*, entries are collected in :attr:`entries`.
    """

    name: str
    min_level: LogLevel = LogLevel.INFO
    default_context: dict[str, Any] = field(default_factory=dict)
    handlers: list[Any] = field(default_factory=list)
    entries: list[LogEntry] = field(default_factory=list, init=False, repr=False)
    _trace_id: str | None = field(default=None, init=False, repr=False)
    _span_stack: list[str] = field(default_factory=list, init=False, repr=False)

    # -- trace / span management --------------------------------------

    def set_trace(self, trace_id: str | None = None) -> str:
        """Set (or generate) a trace id and return it."""
        self._trace_id = trace_id or uuid.uuid4().hex
        return self._trace_id

    def push_span(self, span_id: str | None = None) -> str:
        """Start a new span (nested if a span already exists)."""
        sid = span_id or uuid.uuid4().hex[:16]
        self._span_stack.append(sid)
        return sid

    def pop_span(self) -> str | None:
        """End the current span and return it."""
        return self._span_stack.pop() if self._span_stack else None

    @property
    def current_span(self) -> str | None:
        return self._span_stack[-1] if self._span_stack else None

    @property
    def parent_span(self) -> str | None:
        return self._span_stack[-2] if len(self._span_stack) > 1 else None

    # -- core emit ----------------------------------------------------

    def _emit(
        self,
        level: LogLevel,
        message: str,
        **kwargs: Any,
    ) -> LogEntry | None:
        if level.value < self.min_level.value:
            return None

        ctx: dict[str, Any] = {}
        ctx.update(self.default_context)
        ctx.update(kwargs)

        entry = LogEntry(
            id=uuid.uuid4().hex[:20],
            timestamp=datetime.now(timezone.utc),
            level=level,
            message=message,
            context=ctx,
            source=self.name,
            trace_id=self._trace_id,
            span_id=self.current_span,
            parent_span_id=self.parent_span,
        )

        if self.handlers:
            for h in self.handlers:
                h(entry)
        else:
            self.entries.append(entry)

        return entry

    # -- convenience methods ------------------------------------------

    def trace(self, message: str, **kw: Any) -> LogEntry | None:
        return self._emit(LogLevel.TRACE, message, **kw)

    def debug(self, message: str, **kw: Any) -> LogEntry | None:
        return self._emit(LogLevel.DEBUG, message, **kw)

    def info(self, message: str, **kw: Any) -> LogEntry | None:
        return self._emit(LogLevel.INFO, message, **kw)

    def warn(self, message: str, **kw: Any) -> LogEntry | None:
        return self._emit(LogLevel.WARN, message, **kw)

    def error(self, message: str, **kw: Any) -> LogEntry | None:
        return self._emit(LogLevel.ERROR, message, **kw)

    def fatal(self, message: str, **kw: Any) -> LogEntry | None:
        return self._emit(LogLevel.FATAL, message, **kw)

    def exception(self, message: str, **kw: Any) -> LogEntry | None:
        """Log an ERROR with exception info from the current traceback."""
        kw.setdefault("exc_type", traceback.format_exc())
        return self._emit(LogLevel.ERROR, message, **kw)

    # -- child logger -------------------------------------------------

    def child(self, name_suffix: str, **extra_ctx: Any) -> ActiveLogger:
        """Create a child logger inheriting trace/span state."""
        ctx = {**self.default_context, **extra_ctx}
        child = ActiveLogger(
            name=f"{self.name}.{name_suffix}",
            min_level=self.min_level,
            default_context=ctx,
            handlers=list(self.handlers),
        )
        child._trace_id = self._trace_id
        child._span_stack = list(self._span_stack)
        return child
