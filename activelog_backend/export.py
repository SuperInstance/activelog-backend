"""LogExporter — export log entries to JSON, CSV, and text formats."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence, TextIO

from .logger import LogEntry
from .store import LogStore


@dataclass
class LogExporter:
    """Export logs from a :class:`LogStore` to various formats.

    Examples
    --------
    >>> exporter = LogExporter(store)
    >>> exporter.to_json_file("logs.json")
    >>> csv_text = exporter.to_csv()
    """

    store: LogStore

    # -- JSON ---------------------------------------------------------

    def to_json(
        self,
        entries: Sequence[LogEntry] | None = None,
        *,
        pretty: bool = False,
    ) -> str:
        items = entries or self.store.all()
        data = [e.to_dict() for e in items]
        indent = 2 if pretty else None
        return json.dumps(data, indent=indent, default=str)

    def to_json_file(
        self,
        path: str,
        entries: Sequence[LogEntry] | None = None,
        *,
        pretty: bool = False,
    ) -> None:
        with open(path, "w") as f:
            f.write(self.to_json(entries, pretty=pretty))

    def to_ndjson(
        self,
        entries: Sequence[LogEntry] | None = None,
    ) -> str:
        """Newline-delimited JSON — one entry per line."""
        items = entries or self.store.all()
        lines = [json.dumps(e.to_dict(), default=str) for e in items]
        return "\n".join(lines)

    # -- CSV ----------------------------------------------------------

    def to_csv(
        self,
        entries: Sequence[LogEntry] | None = None,
        *,
        include_context: bool = True,
        flatten_context: bool = False,
    ) -> str:
        items = entries or self.store.all()
        if not items:
            return ""

        base_fields = ["id", "timestamp", "level", "message", "source", "trace_id"]

        # collect all context keys if flattening
        ctx_keys: list[str] = []
        if flatten_context and include_context:
            seen: set[str] = set()
            for e in items:
                for k in e.context:
                    if k not in seen:
                        ctx_keys.append(k)
                        seen.add(k)

        fieldnames = list(base_fields)
        if include_context and not flatten_context:
            fieldnames.append("context")
        elif flatten_context:
            fieldnames.extend(ctx_keys)
        fieldnames.append("tags")

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for e in items:
            row: dict[str, Any] = {
                "id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "level": e.level.name,
                "message": e.message,
                "source": e.source or "",
                "trace_id": e.trace_id or "",
                "tags": ",".join(e.tags),
            }
            if flatten_context and include_context:
                for k in ctx_keys:
                    row[k] = e.context.get(k, "")
            elif include_context:
                row["context"] = json.dumps(e.context, default=str)
            writer.writerow(row)

        return buf.getvalue()

    def to_csv_file(
        self,
        path: str,
        entries: Sequence[LogEntry] | None = None,
        **csv_kwargs: Any,
    ) -> None:
        with open(path, "w", newline="") as f:
            f.write(self.to_csv(entries, **csv_kwargs))

    # -- plain text ---------------------------------------------------

    def to_text(
        self,
        entries: Sequence[LogEntry] | None = None,
        *,
        fmt: str = "{timestamp} {level} {source} {message}",
    ) -> str:
        items = entries or self.store.all()
        lines: list[str] = []
        for e in items:
            line = fmt.format(
                timestamp=e.timestamp.isoformat(),
                level=e.level.name,
                source=e.source or "-",
                message=e.message,
                tags=",".join(e.tags) if e.tags else "",
                trace_id=e.trace_id or "",
            )
            lines.append(line)
        return "\n".join(lines)

    def to_text_file(
        self,
        path: str,
        entries: Sequence[LogEntry] | None = None,
        **text_kwargs: Any,
    ) -> None:
        with open(path, "w") as f:
            f.write(self.to_text(entries, **text_kwargs))

    # -- summary ------------------------------------------------------

    def summary(self, entries: Sequence[LogEntry] | None = None) -> dict[str, Any]:
        items = entries or self.store.all()
        if not items:
            return {"total": 0}

        levels: dict[str, int] = {}
        sources: dict[str, int] = {}
        earliest = items[0].timestamp
        latest = items[0].timestamp

        for e in items:
            levels[e.level.name] = levels.get(e.level.name, 0) + 1
            if e.source:
                sources[e.source] = sources.get(e.source, 0) + 1
            if e.timestamp < earliest:
                earliest = e.timestamp
            if e.timestamp > latest:
                latest = e.timestamp

        return {
            "total": len(items),
            "levels": levels,
            "sources": sources,
            "earliest": earliest.isoformat(),
            "latest": latest.isoformat(),
        }
