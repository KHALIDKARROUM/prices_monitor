"""Structured logging and low-cardinality Prometheus metrics for the service."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any


SERVICE_NAME = os.getenv("PRICE_MONITOR_SERVICE_NAME", "price-monitor")
STARTED_MONOTONIC = time.monotonic()


class JsonFormatter(logging.Formatter):
    """Render JSON diagnostics and plain website availability messages."""

    RESERVED = set(logging.LogRecord(None, 0, "", 0, "", (), None).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "event", None) == "website_status_changed":
            return record.getMessage()
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": SERVICE_NAME,
        }
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_logging() -> None:
    """Configure console logs without duplicating handlers on reload."""
    root = logging.getLogger()
    if not any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    level_name = os.getenv("PRICE_MONITOR_LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level_name, logging.INFO))


class Metrics:
    """Small dependency-free metrics registry with Prometheus exposition."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._durations: dict[str, tuple[float, int]] = {}

    @staticmethod
    def _key(labels: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(key), str(value)) for key, value in (labels or {}).items()))

    def increment(self, name: str, value: float = 1, labels: dict[str, Any] | None = None) -> None:
        key = (name, self._key(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def observe_duration(self, name: str, seconds: float) -> None:
        with self._lock:
            total, count = self._durations.get(name, (0.0, 0))
            self._durations[name] = (total + seconds, count + 1)

    @staticmethod
    def _labels_text(labels: tuple[tuple[str, str], ...]) -> str:
        if not labels:
            return ""
        escaped = ((key, value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")) for key, value in labels)
        return "{" + ",".join(f'{key}="{value}"' for key, value in escaped) + "}"

    def prometheus(self) -> str:
        lines = [
            "# HELP price_monitor_info Service metadata.",
            "# TYPE price_monitor_info gauge",
            f'price_monitor_info{{service="{SERVICE_NAME}",version="0.1.0"}} 1',
            "# HELP price_monitor_uptime_seconds Process uptime in seconds.",
            "# TYPE price_monitor_uptime_seconds gauge",
            f"price_monitor_uptime_seconds {time.monotonic() - STARTED_MONOTONIC:.3f}",
        ]
        with self._lock:
            counters = list(self._counters.items())
            durations = dict(self._durations)
        grouped: set[str] = set()
        for (name, labels), value in counters:
            if name not in grouped:
                lines.extend([f"# TYPE {name} counter"])
                grouped.add(name)
            lines.append(f"{name}{self._labels_text(labels)} {value:g}")
        for name, (total, count) in durations.items():
            lines.extend([
                f"# HELP {name} Request duration in seconds.",
                f"# TYPE {name} summary",
                f"{name}_sum {total:.6f}",
                f"{name}_count {count}",
            ])
        return "\n".join(lines) + "\n"


configure_logging()
logger = logging.getLogger("price_monitor")
metrics = Metrics()
