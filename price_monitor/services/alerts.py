"""Threshold and source-health alerts with optional webhook and SMTP delivery."""

from __future__ import annotations

import json
import smtplib
import ssl
import threading
from functools import wraps
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from math import isfinite
from typing import Any, Callable, Protocol
from urllib.request import Request, urlopen

from ..config import (
    ALERT_EMAIL_TO,
    ALERT_NOTIFY_RECOVERY,
    ALERT_SMTP_FROM,
    ALERT_SMTP_HOST,
    ALERT_SMTP_PASSWORD,
    ALERT_SMTP_PORT,
    ALERT_SMTP_USE_TLS,
    ALERT_SMTP_USER,
    ALERT_THRESHOLDS,
    ALERT_WEBHOOK_TIMEOUT_SECONDS,
    ALERT_WEBHOOK_URL,
)
from ..database import DatabaseBackend, PRODUCTS
from ..bitumen import INDEX_LABEL, INDEX_UNIT
from ..observability import logger, metrics
from ..instruments import INSTRUMENTS, DEFAULT_INSTRUMENTS, canonical_latest, row_instrument, price_alert_key
from ..market_calendar import LIVE_SOURCE_CODES, live_monitoring_anchor
from ..config import PROVIDER_INTERVAL_MINUTES


FRESHNESS_WINDOWS = {
    "temps réel": timedelta(minutes=5),
    "quotidienne": timedelta(days=2),
    "daily": timedelta(days=2),
    "hebdomadaire": timedelta(days=8),
    "weekly": timedelta(days=8),
    "mensuelle": timedelta(days=62),
    "monthly": timedelta(days=62),
}


_evaluation_lock = threading.RLock()


def _serialized_evaluation(method):
    """Provider workers cannot notify the same unacknowledged event twice."""
    @wraps(method)
    def evaluate(*args, **kwargs):
        with _evaluation_lock:
            return method(*args, **kwargs)
    return evaluate


@dataclass(frozen=True)
class AlertEvent:
    key: str
    kind: str
    status: str
    title: str
    message: str
    occurred_at: str
    product: str | None = None
    source_code: str | None = None
    value: float | None = None
    threshold: float | None = None
    channel: str | None = None
    instrument: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Notifier(Protocol):
    def send(self, events: list[AlertEvent]) -> bool: ...


class NotificationDispatcher:
    """Deliver one aggregated batch to every configured channel.

    Webhooks use a small Teams-compatible payload with a ``text`` field, while
    retaining the full event list for generic webhook consumers.
    """

    def __init__(
        self,
        webhook_url: str = ALERT_WEBHOOK_URL,
        email_to: tuple[str, ...] = ALERT_EMAIL_TO,
        smtp_host: str = ALERT_SMTP_HOST,
        smtp_port: int = ALERT_SMTP_PORT,
        smtp_user: str = ALERT_SMTP_USER,
        smtp_password: str = ALERT_SMTP_PASSWORD,
        smtp_from: str = ALERT_SMTP_FROM,
        smtp_use_tls: bool = ALERT_SMTP_USE_TLS,
    ) -> None:
        self.webhook_url = webhook_url
        self.email_to = email_to
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from or smtp_user
        self.smtp_use_tls = smtp_use_tls

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url or self.email_ready)

    @property
    def email_ready(self) -> bool:
        return bool(self.email_to and self.smtp_host and self.smtp_from and (not self.smtp_user or self.smtp_password))

    def send(self, events: list[AlertEvent]) -> bool:
        if not events or not self.enabled:
            return False

        delivered = False
        grouped: dict[str, list[AlertEvent]] = {}
        for event in events:
            channel = event.channel or "both"
            if channel == "none":
                continue
            grouped.setdefault(channel, []).append(event)
        for channel, batch in grouped.items():
            delivered = self._send_batch(batch, channel) or delivered
        return delivered

    def _send_batch(self, events: list[AlertEvent], channel: str) -> bool:
        """Send one rule-specific batch to its selected channel(s)."""
        if not events:
            return False

        text = "\n".join(f"• {event.title}: {event.message}" for event in events)
        delivered = False
        if channel in {"webhook", "both"} and self.webhook_url:
            try:
                payload = {
                    "title": "Price Monitor - alerte prix",
                    "text": text,
                    "alerts": [event.as_dict() for event in events],
                }
                request = Request(
                    self.webhook_url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json", "User-Agent": "PriceMonitor/1.0"},
                    method="POST",
                )
                with urlopen(request, timeout=ALERT_WEBHOOK_TIMEOUT_SECONDS) as response:
                    if not 200 <= int(response.status) < 300:
                        raise RuntimeError(f"webhook HTTP {response.status}")
                delivered = True
                metrics.increment("price_monitor_alert_notifications_total", labels={"channel": "webhook", "status": "success"})
            except Exception as exc:
                metrics.increment("price_monitor_alert_notifications_total", labels={"channel": "webhook", "status": "error"})
                logger.error("Alert webhook delivery failed", extra={"event": "alert_notification_failed", "channel": "webhook", "error": str(exc)})

        if channel in {"email", "both"} and self.email_ready:
            try:
                message = EmailMessage()
                message["Subject"] = f"Price Monitor - {len(events)} alerte(s)"
                message["From"] = self.smtp_from
                message["To"] = ", ".join(self.email_to)
                message.set_content(text)
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=ALERT_WEBHOOK_TIMEOUT_SECONDS) as server:
                    if self.smtp_use_tls:
                        server.starttls(context=ssl.create_default_context())
                    if self.smtp_user:
                        server.login(self.smtp_user, self.smtp_password)
                    server.send_message(message)
                delivered = True
                metrics.increment("price_monitor_alert_notifications_total", labels={"channel": "email", "status": "success"})
            except Exception as exc:
                metrics.increment("price_monitor_alert_notifications_total", labels={"channel": "email", "status": "error"})
                logger.error("Alert email delivery failed", extra={"event": "alert_notification_failed", "channel": "email", "error": str(exc)})

        return delivered


class AlertManager:
    """Evaluate alert conditions and persist their active/recovered state."""

    def __init__(
        self,
        database: DatabaseBackend,
        notifier: Notifier | None = None,
        thresholds: dict[str, dict[str, float]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.notifier = notifier or NotificationDispatcher()
        stored_rules = []
        if thresholds is None:
            list_rules = getattr(database, "alert_rules", None)
            if callable(list_rules):
                stored_rules = list_rules(include_disabled=False)
        self.thresholds = thresholds if thresholds is not None else (self._thresholds_from_rules(stored_rules) if stored_rules else ALERT_THRESHOLDS)
        self.thresholds = {
            DEFAULT_INSTRUMENTS.get(key, "asphalt_index" if key == "bitume:index" else key): value
            for key, value in self.thresholds.items()
        }
        self.freshness_rules = [rule for rule in stored_rules if rule.get("direction") == "stale"]
        self.now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _thresholds_from_rules(rules: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
        thresholds: dict[str, dict[str, dict[str, Any]]] = {}
        for rule in rules:
            product = str(rule.get("product", ""))
            direction = str(rule.get("direction", ""))
            if product not in PRODUCTS or direction not in {"above", "below"}:
                continue
            rule_product = rule.get("instrument") or ("asphalt_index" if rule.get("basis") == "index" else DEFAULT_INSTRUMENTS[product])
            thresholds.setdefault(rule_product, {})[direction] = {
                "threshold": rule.get("threshold"),
                "channel": "none" if rule.get("muted") else rule.get("channel", "webhook"),
            }
        return thresholds

    @_serialized_evaluation
    def evaluate_collection(
        self,
        observations: list[dict[str, Any]],
        source_health: list[dict[str, Any]],
    ) -> list[AlertEvent]:
        events = self.evaluate_prices(observations, dispatch=False)
        events.extend(self.evaluate_sources(source_health, dispatch=False))
        events.extend(self.evaluate_live_freshness(source_health, dispatch=False))
        self._dispatch(events)
        return events

    @_serialized_evaluation
    def evaluate_live_freshness(
        self,
        sources: list[dict[str, Any]],
        dispatch: bool = True,
    ) -> list[AlertEvent]:
        """Alert on the exchange quote time, not the time our poll succeeded."""
        now = self.now().astimezone(timezone.utc)
        occurred_at = now.replace(microsecond=0).isoformat()
        by_code = {str(row.get("code")): row for row in sources}
        source_codes = {"gasoil": "live_gasoil_proxy", "brent": "live_brent"}
        events: list[AlertEvent] = []
        for rule in self.freshness_rules:
            product = str(rule["product"])
            source = by_code.get(source_codes.get(product, ""), {})
            if not source or not source.get("active", 1):
                continue
            opened_at = live_monitoring_anchor(source_codes[product], now)
            if opened_at is None:
                # Leave both active and recovered state untouched while closed.
                continue
            threshold = float(rule["threshold"])
            if now < opened_at + timedelta(minutes=min(5, threshold)):
                # A reopening alone cannot count as a fresh quote or recovery.
                continue
            quote_at = source.get("last_quote_at")
            try:
                anchor = self._parse_datetime(quote_at or rule["created_at"])
            except (TypeError, ValueError, KeyError):
                continue
            age_minutes = max(0.0, (now - anchor).total_seconds() / 60)
            stale = age_minutes >= threshold
            key = f"live_stale:{product}:{threshold:g}"
            previous = self.database.get_alert_state(key)

            def make_event(status: str) -> AlertEvent:
                label = PRODUCTS[product]["label"]
                quote_description = f"Dernière cotation : {anchor.isoformat()}" if quote_at else "Aucune cotation reçue"
                return AlertEvent(
                    key=key, kind="live_quote_stale", status=status,
                    title=f"Cotation en direct — {label}",
                    message=(
                        f"{label} sans nouvelle cotation depuis {age_minutes:.0f} min "
                        f"(limite {threshold:g} min). {quote_description}."
                        if status == "triggered"
                        else f"Une cotation récente est à nouveau disponible pour {label}. {quote_description}."
                    ),
                    occurred_at=occurred_at, product=product,
                    source_code=source_codes[product], value=age_minutes,
                    threshold=threshold, channel="none" if rule.get("muted") else rule["channel"],
                )

            event = self._transition(key, stale, age_minutes, occurred_at, make_event)
            if event:
                events.append(event)
            elif stale and previous and previous.get("active") and not previous.get("last_notified_at"):
                # A missing SMTP setup or temporary delivery error must not
                # permanently lose the first stale notification.
                events.append(make_event("triggered"))
        if dispatch:
            self._dispatch(events)
        return events

    @_serialized_evaluation
    def evaluate_prices(
        self,
        observations: list[dict[str, Any]],
        dispatch: bool = True,
    ) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        now = self.now().astimezone(timezone.utc)
        occurred_at = now.replace(microsecond=0).isoformat()
        eligible = [row for row in observations if row.get("product") in PRODUCTS]
        for observation in canonical_latest(eligible):
            product = str(observation.get("product", ""))
            if product not in PRODUCTS:
                continue
            try:
                price = float(observation["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if not isfinite(price):
                continue
            is_index = observation.get("data_kind") == "index"
            rule_product = row_instrument(observation)
            label = INSTRUMENTS[rule_product]["label"]
            unit = INDEX_UNIT if is_index else PRODUCTS[product]["unit"]
            for direction, rule in self._rules_for(rule_product).items():
                threshold = rule["threshold"]
                active = price >= threshold if direction == "above" else price <= threshold
                key = price_alert_key(product, rule_product, direction, threshold)
                event = self._transition(
                    key=key,
                    active=active,
                    value=price,
                    occurred_at=occurred_at,
                    event_factory=lambda status, direction=direction, threshold=threshold, product=product, price=price: AlertEvent(
                        key=key,
                        kind="index_threshold" if is_index else "price_threshold",
                        status=status,
                        title=f"{label} — seuil {direction}",
                        message=(
                            f"{'L’indice' if is_index else 'Le prix'} est de {price:g} {unit} "
                            f"({'au-dessus' if direction == 'above' else 'en-dessous'} du seuil {threshold:g})."
                            if status == "triggered"
                            else f"{'L’indice' if is_index else 'Le prix'} est revenu à {price:g} {unit} ; le seuil {threshold:g} n'est plus atteint."
                        ),
                        occurred_at=occurred_at,
                        product=product,
                        value=price,
                        threshold=threshold,
                        channel=rule.get("channel"),
                        instrument=rule_product,
                    ),
                )
                if event:
                    events.append(event)
        if dispatch:
            self._dispatch(events)
        return events

    @_serialized_evaluation
    def evaluate_sources(
        self,
        sources: list[dict[str, Any]],
        dispatch: bool = True,
    ) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        now = self.now().astimezone(timezone.utc)
        occurred_at = now.replace(microsecond=0).isoformat()
        for source in sources:
            code = str(source.get("code", "")).strip()
            if not code or not source.get("active", 1):
                continue
            opened_at = live_monitoring_anchor(code, now) if code in LIVE_SOURCE_CODES else None
            if code in LIVE_SOURCE_CODES and (opened_at is None or now < opened_at + timedelta(minutes=5)):
                continue
            label = str(source.get("label") or code)
            last_error = str(source.get("last_error") or "").strip()
            failed = bool(last_error)
            failure_key = f"source:{code}:failed"
            failure_event = self._transition(
                key=failure_key,
                active=failed,
                value=None,
                occurred_at=occurred_at,
                event_factory=lambda status, code=code, label=label, last_error=last_error: AlertEvent(
                    key=failure_key,
                    kind="source_failure",
                    status=status,
                    title=f"Source indisponible — {label}",
                    message=(
                        f"La source {label} ({code}) a échoué : {last_error}."
                        if status == "triggered"
                        else f"La source {label} ({code}) fonctionne à nouveau."
                    ),
                    occurred_at=occurred_at,
                    source_code=code,
                ),
            )
            if failure_event:
                events.append(failure_event)

            # A failed source is already covered by the failure alert. Sources
            # that are manually maintained or have never run are not stale.
            if failed or not source.get("last_success_at"):
                continue
            frequency = str(source.get("frequency") or "").strip().lower()
            freshness_window = FRESHNESS_WINDOWS.get(frequency)
            if freshness_window is None:
                continue
            provider = {"fred_diesel": "fred", "fred_brent": "fred", "eia_diesel": "eia", "eia_brent": "eia",
                        "alpha_brent": "alpha_vantage", "bls_asphalt_ppi": "bls", "live_brent": "yahoo",
                        "live_gasoil_proxy": "yahoo"}.get(code)
            if provider:
                freshness_window = max(freshness_window, timedelta(minutes=PROVIDER_INTERVAL_MINUTES[provider] * 2))
            try:
                last_success = self._parse_datetime(source["last_success_at"])
            except (TypeError, ValueError):
                continue
            age = now - last_success
            stale = age > freshness_window
            stale_key = f"source:{code}:stale"
            stale_event = self._transition(
                key=stale_key,
                active=stale,
                value=age.total_seconds() / 3600,
                occurred_at=occurred_at,
                event_factory=lambda status, code=code, label=label, age=age, frequency=frequency: AlertEvent(
                    key=stale_key,
                    kind="source_stale",
                    status=status,
                    title=f"Source obsolète — {label}",
                    message=(
                        f"La dernière donnée de {label} date de {self._format_age(age)} "
                        f"(fréquence attendue : {frequency})."
                        if status == "triggered"
                        else f"La source {label} fournit à nouveau des données fraîches."
                    ),
                    occurred_at=occurred_at,
                    source_code=code,
                    value=age.total_seconds() / 3600,
                ),
            )
            if stale_event:
                events.append(stale_event)
        if dispatch:
            self._dispatch(events)
        return events

    def _rules_for(self, product: str) -> dict[str, dict[str, Any]]:
        rules = self.thresholds.get(product, {})
        normalized: dict[str, dict[str, Any]] = {}
        for direction in ("above", "below"):
            if direction not in rules:
                continue
            raw_rule = rules[direction]
            if isinstance(raw_rule, dict):
                raw_threshold = raw_rule.get("threshold")
                channel = raw_rule.get("channel", "webhook")
            else:
                raw_threshold = raw_rule
                channel = None
            try:
                value = float(raw_threshold)
            except (TypeError, ValueError):
                continue
            if isfinite(value):
                normalized[direction] = {"threshold": value, "channel": channel}
        return normalized

    def _transition(
        self,
        key: str,
        active: bool,
        value: float | None,
        occurred_at: str,
        event_factory: Callable[[str], AlertEvent],
    ) -> AlertEvent | None:
        previous = self.database.get_alert_state(key)
        was_active = bool(previous and previous.get("active"))
        transition_status = "triggered" if active else "recovered"
        self.database.set_alert_state(
            key,
            active,
            value,
            occurred_at,
            last_status=transition_status if active != was_active else None,
            clear_notified=active and active != was_active,
        )
        if active == was_active:
            return None
        return event_factory("triggered" if active else "recovered")

    def _dispatch(self, events: list[AlertEvent]) -> None:
        dispatchable = [
            event for event in events
            if event.channel != "none" and (event.status == "triggered" or ALERT_NOTIFY_RECOVERY)
        ]
        if not dispatchable:
            return
        try:
            delivered = self.notifier.send(dispatchable)
        except Exception as exc:
            delivered = False
            logger.exception("Alert notification failed", extra={"event": "alert_notification_failed", "error": str(exc)})
        if delivered:
            notified_at = self.now().astimezone(timezone.utc).replace(microsecond=0).isoformat()
            for event in dispatchable:
                state = self.database.get_alert_state(event.key)
                self.database.set_alert_state(
                    event.key,
                    bool(state and state.get("active")),
                    event.value,
                    notified_at,
                    last_notified_at=notified_at,
                )
            metrics.increment("price_monitor_alert_events_total", labels={"status": "notified"})
        else:
            metrics.increment("price_monitor_alert_events_total", labels={"status": "pending"})

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_age(age: timedelta) -> str:
        hours = max(0, int(age.total_seconds() // 3600))
        if hours < 48:
            return f"{hours} heure(s)"
        return f"{hours // 24} jour(s)"
