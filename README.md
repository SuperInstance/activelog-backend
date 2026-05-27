# activelog-backend

Structured logging with real-time querying and alerting — pure Python, zero external dependencies.

## Installation

```bash
pip install activelog-backend
```

For development:

```bash
pip install -e ".[dev]"
```

## Quick Start

```python
from activelog_backend import (
    ActiveLogger,
    LogLevel,
    LogStore,
    QueryEngine,
    AlertRule,
    AlertManager,
    LogExporter,
)

# Create a store to collect logs
store = LogStore()

# Create a logger that writes to the store
logger = ActiveLogger(
    name="myapp",
    min_level=LogLevel.DEBUG,
    handlers=[store.add],
)

# Set up alerting
alerts = AlertManager()
alerts.add_rule(AlertRule(
    name="high_error_rate",
    min_level=LogLevel.ERROR,
    threshold_count=5,
    threshold_window_seconds=60,
))

# Log some events
logger.set_trace("req-abc123")
logger.push_span("handler")

logger.info("Request received", path="/api/users", method="GET")
logger.debug("Query executed", sql="SELECT * FROM users", duration_ms=23)
logger.error("Database timeout", host="db-primary", retries=3)

# Query
engine = QueryEngine(store)
errors = engine.filter(level=LogLevel.ERROR)
recent = engine.filter(start=some_datetime)
by_user = engine.filter(context={"user_id": 42})

# Aggregation
engine.count_by_level()        # {"INFO": 2, "DEBUG": 1, "ERROR": 1}
engine.count_by_source()
engine.error_rate(window_minutes=60)

# Export
exporter = LogExporter(store)
exporter.to_json_file("logs.json")
exporter.to_csv_file("logs.csv", flatten_context=True)
exporter.to_text_file("logs.txt")
```

## Modules

### `activelog_backend.logger` — ActiveLogger

Structured logger with:
- **6 log levels**: TRACE, DEBUG, INFO, WARN, ERROR, FATAL
- **Distributed tracing**: trace IDs and nested span support
- **Rich context**: arbitrary key-value pairs on every log entry
- **Child loggers**: inherit trace/span state with additional context
- **Handler hooks**: route entries to any callable (stores, files, networks)

### `activelog_backend.store` — LogStore

Thread-safe in-memory store with:
- Time-range queries
- Field-level lookups
- Tag-based filtering
- Full-text search
- Observer pattern for real-time subscriptions
- Automatic eviction when `max_entries` is reached

### `activelog_backend.query` — QueryEngine

Filter, search, and aggregate:
- Multi-criteria filtering (level, source, trace, tags, context, time range)
- Full-text search across messages and context values
- Aggregations: count by level, source, field, timeline buckets
- Error rate calculation over sliding windows

### `activelog_backend.alert` — AlertRule & AlertManager

Pattern-based alerting:
- Match by level, regex pattern, context values, and tags
- Threshold triggers (N matches in T seconds)
- Severity levels (LOW, MEDIUM, HIGH, CRITICAL)
- Callback-based notification system
- Batch evaluation support

### `activelog_backend.export` — LogExporter

Export to standard formats:
- **JSON**: compact or pretty-printed
- **NDJSON**: newline-delimited (one entry per line)
- **CSV**: with optional context flattening
- **Plain text**: customizable format string
- **Summary**: statistics and metadata

## Running Tests

```bash
python -m pytest tests/ -q
```

## License

MIT
