"""Periodic email summary of collection activity while the service runs."""

from __future__ import annotations

import smtplib
import ssl
import time
from collections import Counter
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Callable

from ..config import (
    ALERT_SMTP_FROM,
    ALERT_SMTP_HOST,
    ALERT_SMTP_PASSWORD,
    ALERT_SMTP_PORT,
    ALERT_SMTP_USE_TLS,
    ALERT_SMTP_USER,
    ALERT_WEBHOOK_TIMEOUT_SECONDS,
    DIGEST_EMAIL_TO,
    DIGEST_INTERVAL_HOURS,
)
from ..database import DatabaseBackend
from ..observability import logger, metrics


def _utc_label(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def format_collection_digest(rows: list[dict], started_at: datetime, finished_at: datetime) -> tuple[str, str]:
    """Turn many minute-by-minute collection records into one readable report."""
    counts = Counter(str(row["status"]) for row in rows)
    record_count = sum(int(row.get("rows_collected") or 0) for row in rows)
    problems = Counter(
        str(row.get("message") or "Unspecified collection issue").strip()
        for row in rows
        if row["status"] in {"partial", "error"}
    )
    subject = f"Price Monitor | Collection report | {_utc_label(finished_at)}"
    lines = [
        "Price Monitor — Collection report",
        "",
        f"Reporting period: {_utc_label(started_at)} to {_utc_label(finished_at)}",
        "Service status: Running at the time of this report",
        "",
        "Collection summary",
        f"Total runs: {len(rows)}",
        f"Successful: {counts['success']}",
        f"Partial: {counts['partial']}",
        f"Failed: {counts['error']}",
        f"Records collected: {record_count}",
    ]
    if rows:
        latest = rows[-1]
        lines.append(f"Latest run: {str(latest['status']).capitalize()} at {_utc_label(latest['finished_at'] or latest['started_at'])}")
    else:
        lines.append("Latest run: No collections during this period")
    lines.extend(["", "Issues requiring attention"])
    if problems:
        for message, count in problems.most_common(5):
            lines.append(f"- {count} run(s): {message}")
        if len(problems) > 5:
            lines.append(f"- {len(problems) - 5} additional issue type(s) omitted")
    else:
        lines.append("No collection issues recorded during this period.")
    lines.extend(["", "This is an automated operational summary from Price Monitor."])
    return subject, "\n".join(lines)


def send_digest_email(subject: str, body: str) -> None:
    """Send through the SMTP account already configured for alerts."""
    sender = ALERT_SMTP_FROM or ALERT_SMTP_USER
    if not (DIGEST_EMAIL_TO and ALERT_SMTP_HOST and sender and (not ALERT_SMTP_USER or ALERT_SMTP_PASSWORD)):
        raise RuntimeError("Digest email or SMTP settings are incomplete")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(DIGEST_EMAIL_TO)
    message.set_content(body)
    with smtplib.SMTP(ALERT_SMTP_HOST, ALERT_SMTP_PORT, timeout=ALERT_WEBHOOK_TIMEOUT_SECONDS) as server:
        if ALERT_SMTP_USE_TLS:
            server.starttls(context=ssl.create_default_context())
        if ALERT_SMTP_USER:
            server.login(ALERT_SMTP_USER, ALERT_SMTP_PASSWORD)
        server.send_message(message)


class CollectionDigest:
    """Send one report every interval, keeping unsent records for the next try."""

    def __init__(
        self,
        database: DatabaseBackend,
        interval_hours: int = DIGEST_INTERVAL_HOURS,
        send: Callable[[str, str], None] = send_digest_email,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.database = database
        self.interval_seconds = interval_hours * 3600
        self.send = send
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic
        self.started_at = self.now()
        latest = database.logs(limit=1)
        self.last_log_id = int(latest[0]["id"]) if latest else 0
        self.next_due = self.monotonic() + self.interval_seconds

    def maybe_send(self) -> bool:
        current = self.monotonic()
        if current < self.next_due:
            return False
        # Schedule the next attempt now, so an SMTP failure cannot cause a
        # retry on every one-second scheduler tick.
        self.next_due = current + self.interval_seconds
        finished_at = self.now()
        try:
            rows = self.database.collection_logs_after(self.last_log_id)
            subject, body = format_collection_digest(rows, self.started_at, finished_at)
            self.send(subject, body)
        except Exception as exc:
            metrics.increment("price_monitor_digest_notifications_total", labels={"status": "error"})
            logger.error("Collection digest email failed", extra={"event": "collection_digest_failed", "error": str(exc)})
            return False
        if rows:
            self.last_log_id = int(rows[-1]["id"])
        self.started_at = finished_at
        metrics.increment("price_monitor_digest_notifications_total", labels={"status": "success"})
        logger.info("Collection digest email sent", extra={"event": "collection_digest_sent", "runs": len(rows)})
        return True
