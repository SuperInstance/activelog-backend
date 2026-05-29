# activelog-backend

**Structured logging with real-time querying and alerting** — pure Python, zero external dependencies.

## What This Gives You

- **Structured log entries** — JSON, key-value, or custom formats with levels and trace IDs
- **Query engine** — filter by level, source, time range, and custom fields
- **Alert rules** — threshold-based alerting with configurable windows
- **Log exporter** — export to JSON, CSV, or custom formats
- **Zero dependencies** — stdlib only, pytest for tests

## Installation

```bash
pip install activelog-backend
```

## Quick Start

```python
from activelog_backend import ActiveLogger, LogLevel, LogStore, QueryEngine, AlertRule, AlertManager

store = LogStore()
logger = ActiveLogger(name="myapp", min_level=LogLevel.DEBUG, handlers=[store.add])

alerts = AlertManager()
alerts.add_rule(AlertRule(name="high_error_rate", min_level=LogLevel.ERROR, threshold_count=5, threshold_window_seconds=60))

logger.info("Server started", fields={"port": 8080})
logger.error("Database timeout", source="db-service")

# Query logs
engine = QueryEngine(store)
results = engine.query(level=LogLevel.ERROR, source="db-service")
```

## API Reference

| Module | Purpose |
|--------|---------|
| `logger.py` | `ActiveLogger` with levels, handlers, trace context |
| `store.py` | `LogStore` — in-memory structured log storage |
| `query.py` | `QueryEngine` — filter, aggregate, and search logs |
| `alert.py` | `AlertRule` + `AlertManager` — threshold alerting |
| `export.py` | `LogExporter` — JSON/CSV/custom export |

## Testing

```bash
pip install -e ".[dev]"
pytest
```

## How It Fits

Storage and query layer for the activelog pipeline: `activelog-agent` monitors → `activelog-backend` stores and queries → `activelog-ai` analyzes.

## License

MIT
