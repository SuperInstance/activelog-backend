"""Tests for activelog_backend."""

from __future__ import annotations

import json
import time
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from activelog_backend import (
    ActiveLogger,
    AlertManager,
    AlertRule,
    AlertSeverity,
    LogExporter,
    LogLevel,
    LogStore,
    QueryEngine,
)
from activelog_backend.logger import LogEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    message: str = "test",
    level: LogLevel = LogLevel.INFO,
    source: str = "test",
    context: dict | None = None,
    tags: list[str] | None = None,
    trace_id: str | None = None,
) -> LogEntry:
    return LogEntry(
        id="abc123",
        timestamp=datetime.now(timezone.utc),
        level=level,
        message=message,
        context=context or {},
        tags=tags or [],
        source=source,
        trace_id=trace_id,
    )


# ---------------------------------------------------------------------------
# ActiveLogger
# ---------------------------------------------------------------------------

class TestActiveLogger:
    def test_basic_log(self):
        logger = ActiveLogger(name="test", min_level=LogLevel.DEBUG)
        entry = logger.info("hello", user_id=42)
        assert entry is not None
        assert entry.message == "hello"
        assert entry.level == LogLevel.INFO
        assert entry.context["user_id"] == 42
        assert entry.source == "test"

    def test_level_filtering(self):
        logger = ActiveLogger(name="test", min_level=LogLevel.WARN)
        assert logger.debug("nope") is None
        assert logger.info("nope") is None
        assert logger.warn("yes") is not None
        assert logger.error("yes") is not None

    def test_all_levels(self):
        logger = ActiveLogger(name="test", min_level=LogLevel.TRACE)
        assert logger.trace("t") is not None
        assert logger.debug("d") is not None
        assert logger.info("i") is not None
        assert logger.warn("w") is not None
        assert logger.error("e") is not None
        assert logger.fatal("f") is not None

    def test_default_context(self):
        logger = ActiveLogger(name="test", default_context={"app": "demo"})
        entry = logger.info("msg")
        assert entry.context["app"] == "demo"

    def test_context_override(self):
        logger = ActiveLogger(
            name="test",
            default_context={"env": "prod"},
            min_level=LogLevel.DEBUG,
        )
        entry = logger.info("msg", env="dev", extra=1)
        assert entry.context["env"] == "dev"
        assert entry.context["extra"] == 1

    def test_trace_and_spans(self):
        logger = ActiveLogger(name="test", min_level=LogLevel.DEBUG)
        trace = logger.set_trace("trace-001")
        assert trace == "trace-001"
        span1 = logger.push_span("span-a")
        entry = logger.info("in span a")
        assert entry.trace_id == "trace-001"
        assert entry.span_id == "span-a"
        assert entry.parent_span_id is None

        span2 = logger.push_span("span-b")
        entry2 = logger.info("nested")
        assert entry2.span_id == "span-b"
        assert entry2.parent_span_id == "span-a"

        logger.pop_span()
        assert logger.current_span == "span-a"

    def test_child_logger(self):
        parent = ActiveLogger(name="svc", min_level=LogLevel.DEBUG)
        parent.set_trace("t1")
        parent.push_span("s1")
        child = parent.child("db", db_name="testdb")
        assert child.name == "svc.db"
        assert child._trace_id == "t1"
        entry = child.info("query")
        assert entry.source == "svc.db"
        assert entry.context["db_name"] == "testdb"

    def test_handler(self):
        collected: list[LogEntry] = []
        logger = ActiveLogger(
            name="test",
            min_level=LogLevel.DEBUG,
            handlers=[collected.append],
        )
        logger.info("a")
        logger.debug("b")
        assert len(collected) == 2
        # entries list is not used when handlers are set
        assert len(logger.entries) == 0

    def test_entry_serialization(self):
        entry = _make_entry("hello", tags=["a", "b"])
        d = entry.to_dict()
        assert d["message"] == "hello"
        assert d["tags"] == ["a", "b"]
        j = entry.to_json()
        parsed = json.loads(j)
        assert parsed["level"] == "INFO"


# ---------------------------------------------------------------------------
# LogStore
# ---------------------------------------------------------------------------

class TestLogStore:
    def test_add_and_count(self):
        store = LogStore()
        store.add(_make_entry())
        store.add(_make_entry())
        assert store.count == 2

    def test_get_by_id(self):
        store = LogStore()
        e = _make_entry()
        store.add(e)
        assert store.get(e.id) is e
        assert store.get("nonexistent") is None

    def test_by_level(self):
        store = LogStore()
        store.add(_make_entry(level=LogLevel.ERROR))
        store.add(_make_entry(level=LogLevel.INFO))
        store.add(_make_entry(level=LogLevel.ERROR))
        assert len(store.by_level(LogLevel.ERROR)) == 2

    def test_by_time_range(self):
        store = LogStore()
        now = datetime.now(timezone.utc)
        old = _make_entry()
        old.timestamp = now - timedelta(hours=2)
        store.add(old)
        recent = _make_entry()
        recent.timestamp = now
        store.add(recent)

        results = store.by_time_range(start=now - timedelta(minutes=5))
        assert len(results) == 1
        assert results[0] is recent

    def test_by_field(self):
        store = LogStore()
        store.add(_make_entry(context={"user": "alice"}))
        store.add(_make_entry(context={"user": "bob"}))
        assert len(store.by_field("user", "alice")) == 1

    def test_by_tag(self):
        store = LogStore()
        store.add(_make_entry(tags=["http", "inbound"]))
        store.add(_make_entry(tags=["db"]))
        assert len(store.by_tag("http")) == 1

    def test_by_source_and_trace(self):
        store = LogStore()
        store.add(_make_entry(source="auth", trace_id="t1"))
        store.add(_make_entry(source="api", trace_id="t2"))
        assert len(store.by_source("auth")) == 1
        assert len(store.by_trace("t1")) == 1

    def test_text_search(self):
        store = LogStore()
        store.add(_make_entry(message="Connection timeout"))
        store.add(_make_entry(message="Query succeeded"))
        assert len(store.text_search("timeout")) == 1
        assert len(store.text_search("QUERY")) == 1  # case-insensitive

    def test_custom_search(self):
        store = LogStore()
        store.add(_make_entry(level=LogLevel.ERROR, context={"code": 500}))
        store.add(_make_entry(level=LogLevel.INFO, context={"code": 200}))
        results = store.search(lambda e: e.context.get("code", 0) >= 400)
        assert len(results) == 1

    def test_max_entries_eviction(self):
        store = LogStore(max_entries=10)
        for i in range(15):
            store.add(_make_entry(message=f"entry-{i}"))
        assert store.count <= 10

    def test_observer(self):
        store = LogStore()
        seen: list[LogEntry] = []
        handler = seen.append
        store.subscribe(handler)
        store.add(_make_entry(message="obs"))
        assert len(seen) == 1
        store.unsubscribe(handler)
        store.add(_make_entry(message="obs2"))
        assert len(seen) == 1

    def test_clear(self):
        store = LogStore()
        store.add(_make_entry())
        assert store.clear() == 1
        assert store.count == 0


# ---------------------------------------------------------------------------
# QueryEngine
# ---------------------------------------------------------------------------

class TestQueryEngine:
    @pytest.fixture()
    def populated(self) -> tuple[LogStore, QueryEngine]:
        store = LogStore()
        now = datetime.now(timezone.utc)
        entries = [
            _make_entry("auth ok", LogLevel.INFO, source="auth", context={"user": "alice"}, trace_id="t1"),
            _make_entry("auth fail", LogLevel.WARN, source="auth", context={"user": "bob"}, trace_id="t1"),
            _make_entry("db error", LogLevel.ERROR, source="db", context={"query": "SELECT"}, trace_id="t2"),
            _make_entry("slow query", LogLevel.WARN, source="db", context={"query": "INSERT"}, trace_id="t2"),
            _make_entry("cache miss", LogLevel.DEBUG, source="cache", tags=["perf"], trace_id="t3"),
        ]
        for i, e in enumerate(entries):
            e.timestamp = now + timedelta(seconds=i)
            store.add(e)
        return store, QueryEngine(store)

    def test_filter_by_level(self, populated):
        store, engine = populated
        errors = engine.filter(level=LogLevel.ERROR)
        assert len(errors) == 1
        assert errors[0].message == "db error"

    def test_filter_by_source(self, populated):
        _, engine = populated
        auth = engine.filter(source="auth")
        assert len(auth) == 2

    def test_filter_by_context(self, populated):
        _, engine = populated
        alice = engine.filter(context={"user": "alice"})
        assert len(alice) == 1

    def test_filter_by_tags(self, populated):
        _, engine = populated
        perf = engine.filter(tags=["perf"])
        assert len(perf) == 1

    def test_filter_by_time_range(self, populated):
        store, engine = populated
        now = store.all()[0].timestamp
        results = engine.filter(start=now + timedelta(seconds=2))
        assert len(results) == 3

    def test_filter_message_contains(self, populated):
        _, engine = populated
        results = engine.filter(message_contains="error")
        assert len(results) == 1

    def test_combined_filter(self, populated):
        _, engine = populated
        results = engine.filter(source="db", level=LogLevel.WARN)
        assert len(results) == 1
        assert results[0].message == "slow query"

    def test_search(self, populated):
        _, engine = populated
        results = engine.search("auth")
        assert len(results) == 2

    def test_count_by_level(self, populated):
        _, engine = populated
        counts = engine.count_by_level()
        assert counts["INFO"] == 1
        assert counts["ERROR"] == 1

    def test_count_by_source(self, populated):
        _, engine = populated
        counts = engine.count_by_source()
        assert counts["auth"] == 2
        assert counts["db"] == 2

    def test_count_by_field(self, populated):
        _, engine = populated
        counts = engine.count_by_field("user")
        assert counts.get("alice") == 1

    def test_timeline(self, populated):
        _, engine = populated
        tl = engine.timeline(bucket_minutes=60)
        assert len(tl) >= 1
        total = sum(tl.values())
        assert total == 5

    def test_top_messages(self, populated):
        _, engine = populated
        top = engine.top_messages(3)
        assert len(top) <= 3
        assert all(isinstance(t, tuple) and len(t) == 2 for t in top)

    def test_error_rate(self, populated):
        _, engine = populated
        rate = engine.error_rate(window_minutes=60)
        assert 0.0 <= rate <= 1.0
        # WARN + ERROR = 3 out of 5
        assert rate == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# AlertRule / AlertManager
# ---------------------------------------------------------------------------

class TestAlertRule:
    def test_basic_match(self):
        rule = AlertRule(name="errors", min_level=LogLevel.ERROR)
        assert rule.matches(_make_entry(level=LogLevel.ERROR))
        assert not rule.matches(_make_entry(level=LogLevel.INFO))

    def test_pattern_match(self):
        rule = AlertRule(name="timeout", pattern=r"timeout|timed out", min_level=LogLevel.WARN)
        assert rule.matches(_make_entry(message="Connection timeout", level=LogLevel.ERROR))
        assert not rule.matches(_make_entry(message="OK", level=LogLevel.ERROR))

    def test_context_match(self):
        rule = AlertRule(
            name="prod_errors",
            context_match={"env": "production"},
            min_level=LogLevel.ERROR,
        )
        assert rule.matches(_make_entry(level=LogLevel.ERROR, context={"env": "production"}))
        assert not rule.matches(_make_entry(level=LogLevel.ERROR, context={"env": "staging"}))

    def test_tag_match(self):
        rule = AlertRule(name="security", tags=["security"], min_level=LogLevel.WARN)
        assert rule.matches(_make_entry(level=LogLevel.WARN, tags=["security", "auth"]))
        assert not rule.matches(_make_entry(level=LogLevel.WARN, tags=["perf"]))

    def test_threshold(self):
        rule = AlertRule(
            name="burst",
            min_level=LogLevel.ERROR,
            threshold_count=3,
            threshold_window_seconds=5.0,
        )
        # First two shouldn't fire
        assert rule.evaluate(_make_entry(level=LogLevel.ERROR)) is None
        assert rule.evaluate(_make_entry(level=LogLevel.ERROR)) is None
        # Third should fire
        event = rule.evaluate(_make_entry(level=LogLevel.ERROR))
        assert event is not None
        assert event.rule_name == "burst"

    def test_disabled_rule(self):
        rule = AlertRule(name="off", enabled=False, min_level=LogLevel.TRACE)
        assert not rule.matches(_make_entry(level=LogLevel.FATAL))


class TestAlertManager:
    def test_evaluate_triggers(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule(name="err", min_level=LogLevel.ERROR))
        events = mgr.evaluate(_make_entry(level=LogLevel.ERROR))
        assert len(events) == 1
        assert events[0].rule_name == "err"

    def test_no_trigger_for_info(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule(name="err", min_level=LogLevel.ERROR))
        events = mgr.evaluate(_make_entry(level=LogLevel.INFO))
        assert len(events) == 0

    def test_callback(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule(name="err", min_level=LogLevel.ERROR))
        received: list = []
        mgr.on_alert(received.append)
        mgr.evaluate(_make_entry(level=LogLevel.ERROR))
        assert len(received) == 1

    def test_remove_rule(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule(name="r1", min_level=LogLevel.ERROR))
        mgr.remove_rule("r1")
        events = mgr.evaluate(_make_entry(level=LogLevel.ERROR))
        assert len(events) == 0

    def test_events_history(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule(name="err", min_level=LogLevel.ERROR))
        mgr.evaluate(_make_entry(level=LogLevel.ERROR))
        mgr.evaluate(_make_entry(level=LogLevel.ERROR))
        assert len(mgr.events) == 2
        assert mgr.clear_events() == 2
        assert len(mgr.events) == 0

    def test_batch_evaluate(self):
        mgr = AlertManager()
        mgr.add_rule(AlertRule(name="err", min_level=LogLevel.ERROR))
        entries = [_make_entry(level=LogLevel.ERROR) for _ in range(3)]
        events = mgr.evaluate_batch(entries)
        assert len(events) == 3


# ---------------------------------------------------------------------------
# LogExporter
# ---------------------------------------------------------------------------

class TestLogExporter:
    @pytest.fixture()
    def store_with_entries(self) -> LogStore:
        store = LogStore()
        now = datetime.now(timezone.utc)
        entries = [
            _make_entry("msg1", LogLevel.INFO, source="svc", context={"user": "a"}, tags=["t1"]),
            _make_entry("msg2", LogLevel.ERROR, source="svc", context={"user": "b"}, tags=["t1", "t2"]),
        ]
        for i, e in enumerate(entries):
            e.timestamp = now + timedelta(seconds=i)
            store.add(e)
        return store

    def test_to_json(self, store_with_entries):
        exporter = LogExporter(store_with_entries)
        data = json.loads(exporter.to_json())
        assert len(data) == 2
        assert data[0]["message"] == "msg1"

    def test_to_json_pretty(self, store_with_entries):
        exporter = LogExporter(store_with_entries)
        text = exporter.to_json(pretty=True)
        assert "\n" in text

    def test_to_ndjson(self, store_with_entries):
        exporter = LogExporter(store_with_entries)
        text = exporter.to_ndjson()
        lines = text.strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # must be valid JSON

    def test_to_csv(self, store_with_entries):
        exporter = LogExporter(store_with_entries)
        text = exporter.to_csv()
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["message"] == "msg1"
        assert rows[0]["level"] == "INFO"

    def test_to_csv_flatten(self, store_with_entries):
        exporter = LogExporter(store_with_entries)
        text = exporter.to_csv(flatten_context=True)
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert "user" in rows[0]
        assert rows[0]["user"] == "a"

    def test_to_text(self, store_with_entries):
        exporter = LogExporter(store_with_entries)
        text = exporter.to_text()
        lines = text.strip().split("\n")
        assert len(lines) == 2
        assert "msg1" in lines[0]

    def test_to_text_custom_format(self, store_with_entries):
        exporter = LogExporter(store_with_entries)
        text = exporter.to_text(fmt="[{level}] {message}")
        assert "[INFO]" in text

    def test_summary(self, store_with_entries):
        exporter = LogExporter(store_with_entries)
        s = exporter.summary()
        assert s["total"] == 2
        assert s["levels"]["INFO"] == 1
        assert s["levels"]["ERROR"] == 1

    def test_to_json_file(self, store_with_entries, tmp_path):
        exporter = LogExporter(store_with_entries)
        path = str(tmp_path / "logs.json")
        exporter.to_json_file(path)
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 2

    def test_to_csv_file(self, store_with_entries, tmp_path):
        exporter = LogExporter(store_with_entries)
        path = str(tmp_path / "logs.csv")
        exporter.to_csv_file(path)
        with open(path) as f:
            reader = csv.DictReader(f)
            assert len(list(reader)) == 2

    def test_to_text_file(self, store_with_entries, tmp_path):
        exporter = LogExporter(store_with_entries)
        path = str(tmp_path / "logs.txt")
        exporter.to_text_file(path)
        with open(path) as f:
            lines = f.read().strip().split("\n")
        assert len(lines) == 2

    def test_empty_store(self):
        store = LogStore()
        exporter = LogExporter(store)
        assert exporter.to_json() == "[]"
        assert exporter.to_csv() == ""
        assert exporter.to_text() == ""
        assert exporter.summary() == {"total": 0}


# ---------------------------------------------------------------------------
# Integration: logger → store → query → alert → export
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_pipeline(self, tmp_path):
        store = LogStore()
        alert_mgr = AlertManager()
        alert_mgr.add_rule(AlertRule(
            name="high_errors",
            min_level=LogLevel.ERROR,
            threshold_count=2,
            threshold_window_seconds=10,
        ))
        alerts_fired: list = []
        alert_mgr.on_alert(alerts_fired.append)

        logger = ActiveLogger(
            name="app",
            min_level=LogLevel.DEBUG,
            handlers=[store.add],
        )
        logger.set_trace("integration-trace")

        # Log some entries
        logger.info("Starting up", version="1.0")
        logger.error("DB connection failed", host="db1")
        logger.error("Retry failed", host="db1")
        logger.warn("Latency spike", latency_ms=500)

        assert store.count == 4

        # Query
        engine = QueryEngine(store)
        errors = engine.filter(level=LogLevel.ERROR)
        assert len(errors) == 2

        # Alerts — we evaluated via store observer? No, let's do it manually
        for entry in store.all():
            alert_mgr.evaluate(entry)
        assert len(alerts_fired) == 1  # threshold of 2 errors triggered

        # Export
        exporter = LogExporter(store)
        json_path = str(tmp_path / "out.json")
        exporter.to_json_file(json_path)
        with open(json_path) as f:
            data = json.load(f)
        assert len(data) == 4

        # Text export
        text = exporter.to_text()
        assert "Starting up" in text
        assert "DB connection failed" in text
