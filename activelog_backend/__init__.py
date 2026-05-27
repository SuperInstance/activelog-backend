"""ActiveLog Backend — structured logging with real-time querying and alerting."""

__version__ = "0.2.0"

from .logger import ActiveLogger, LogLevel
from .store import LogStore
from .query import QueryEngine
from .alert import AlertRule, AlertManager, AlertSeverity
from .export import LogExporter

__all__ = [
    "ActiveLogger",
    "LogLevel",
    "LogStore",
    "QueryEngine",
    "AlertRule",
    "AlertSeverity",
    "LogExporter",
]
