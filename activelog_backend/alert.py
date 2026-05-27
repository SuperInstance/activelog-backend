"""AlertRule — pattern matching and threshold triggers for log entries."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence

from .logger import LogEntry, LogLevel


class AlertSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AlertEvent:
    """An alert that has been triggered."""

    rule_name: str
    severity: AlertSeverity
    message: str
    triggering_entries: list[LogEntry]
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "message": self.message,
            "trigger_count": len(self.triggering_entries),
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class AlertRule:
    """A rule that matches log entries and can fire alerts.

    Parameters
    ----------
    name:
        Human-readable rule name.
    severity:
        Alert severity when triggered.
    pattern:
        Regex applied to the log message.
    min_level:
        Minimum log level to match.
    context_match:
        Dict of key–value pairs that must exist in ``entry.context``.
    tags:
        List of tags — entry must contain **all** of them.
    threshold_count:
        If set, the rule only fires after *threshold_count* matching entries
        within *threshold_window_seconds*.
    threshold_window_seconds:
        Sliding window for threshold counting.  Defaults to 60 s.
    enabled:
        Whether the rule is active.
    """

    name: str
    severity: AlertSeverity = AlertSeverity.MEDIUM
    pattern: str | None = None
    min_level: LogLevel = LogLevel.WARN
    context_match: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    threshold_count: int = 1
    threshold_window_seconds: float = 60.0
    enabled: bool = True

    _compiled: re.Pattern | None = field(default=None, init=False, repr=False)
    _match_times: list[float] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.pattern:
            self._compiled = re.compile(self.pattern)

    def matches(self, entry: LogEntry) -> bool:
        if not self.enabled:
            return False
        if entry.level.value < self.min_level.value:
            return False
        if self._compiled and not self._compiled.search(entry.message):
            return False
        for k, v in self.context_match.items():
            if entry.context.get(k) != v:
                return False
        if self.tags and not all(t in entry.tags for t in self.tags):
            return False
        return True

    def evaluate(self, entry: LogEntry) -> AlertEvent | None:
        """Check entry and return an :class:`AlertEvent` if the rule fires."""
        if not self.matches(entry):
            return None

        now = time.time()
        self._match_times.append(now)
        # prune outside window
        cutoff = now - self.threshold_window_seconds
        self._match_times = [t for t in self._match_times if t >= cutoff]

        if len(self._match_times) >= self.threshold_count:
            # reset after firing to avoid duplicate alerts
            self._match_times.clear()
            return AlertEvent(
                rule_name=self.name,
                severity=self.severity,
                message=f"Alert rule '{self.name}' triggered ({self.threshold_count} matches in {self.threshold_window_seconds}s)",
                triggering_entries=[entry],
                metadata={
                    "threshold_count": self.threshold_count,
                    "window_seconds": self.threshold_window_seconds,
                },
            )
        return None


@dataclass
class AlertManager:
    """Manages multiple :class:`AlertRule` instances and dispatches events."""

    rules: list[AlertRule] = field(default_factory=list)
    _callbacks: list[Callable[[AlertEvent], None]] = field(
        default_factory=list, init=False, repr=False
    )
    _events: list[AlertEvent] = field(
        default_factory=list, init=False, repr=False
    )

    def add_rule(self, rule: AlertRule) -> None:
        self.rules.append(rule)

    def remove_rule(self, name: str) -> None:
        self.rules = [r for r in self.rules if r.name != name]

    def on_alert(self, callback: Callable[[AlertEvent], None]) -> None:
        self._callbacks.append(callback)

    def evaluate(self, entry: LogEntry) -> list[AlertEvent]:
        """Evaluate all rules against *entry* and return any triggered events."""
        fired: list[AlertEvent] = []
        for rule in self.rules:
            event = rule.evaluate(entry)
            if event is not None:
                fired.append(event)
                self._events.append(event)
                for cb in self._callbacks:
                    cb(event)
        return fired

    def evaluate_batch(self, entries: Sequence[LogEntry]) -> list[AlertEvent]:
        all_fired: list[AlertEvent] = []
        for entry in entries:
            all_fired.extend(self.evaluate(entry))
        return all_fired

    @property
    def events(self) -> list[AlertEvent]:
        return list(self._events)

    def clear_events(self) -> int:
        n = len(self._events)
        self._events.clear()
        return n
