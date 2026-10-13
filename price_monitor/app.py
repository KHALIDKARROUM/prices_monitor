from __future__ import annotations

import json
import hmac
import mimetypes
import threading
import time
import uuid
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import (
    API_KEY,
    ALERT_EMAIL_TO,
    ALERT_SMTP_FROM,
    ALERT_SMTP_HOST,
    ALERT_SMTP_PASSWORD,
    ALERT_SMTP_USER,
    ALERT_WEBHOOK_URL,
    DEMO_MODE,
    ENVIRONMENT,
    HOST,
    PORT,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
)
from .database import create_database
from .observability import logger, metrics
from .openapi import OPENAPI_SPEC
from .services.export import csv_bytes, xlsx_bytes
from .services.forecasting import build_forecast
from .services.alerts import AlertManager
from .services.procurement import build_procurement_guide
from .services.scheduler import CollectionScheduler, run_collection
from .services.connectivity import ConnectivityMonitor


ROOT = Path(__file__).resolve().parent
database = create_database()
scheduler = CollectionScheduler(database)
connectivity = ConnectivityMonitor()


class RateLimiter:
    """A small in-memory fixed-window limiter for one application process."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client_key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._requests.setdefault(client_key, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self.max_requests:
                retry_after = max(1, int(timestamps[0] + self.window_seconds - now + 0.999))
                return False, retry_after
            timestamps.append(now)
            return True, 0


rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def validate_security_configuration() -> None:
    """Prevent an explicitly public/production server from starting without a key."""
    if ENVIRONMENT in {"production", "prod"} and not API_KEY:
        raise RuntimeError("PRICE_MONITOR_API_KEY est obligatoire en production")
    if not _is_loopback_host(HOST) and not API_KEY:
        raise RuntimeError("PRICE_MONITOR_API_KEY est obligatoire lorsque PRICE_MONITOR_HOST est public")


def _authorization_value(handler: BaseHTTPRequestHandler) -> str:
    api_key = handler.headers.get("X-API-Key", "").strip()
    if api_key:
        return api_key
    authorization = handler.headers.get("Authorization", "").strip()
    scheme, separator, value = authorization.partition(" ")
    if separator and scheme.lower() == "bearer":
        return value.strip()
    return ""


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def history_filters(query: dict[str, list[str]]) -> dict[str, object]:
    """Extract shared history filters for the API and filtered exports."""
    return {
        "product": query.get("product", [None])[0] or None,
        "supplier": query.get("supplier", [None])[0] or None,
        "source": query.get("source", [None])[0] or None,
        "date_from": query.get("date_from", [None])[0] or None,
        "date_to": query.get("date_to", [None])[0] or None,
        "min_price": query.get("min_price", [None])[0] or None,
        "max_price": query.get("max_price", [None])[0] or None,
        "data_kind": query.get("data_kind", [None])[0] or None,
        "instrument": query.get("instrument", [None])[0] or None,
    }


def alert_channel_status() -> dict[str, bool]:
    webhook = bool(ALERT_WEBHOOK_URL)
    email = bool(ALERT_EMAIL_TO and ALERT_SMTP_HOST and (ALERT_SMTP_FROM or ALERT_SMTP_USER) and (not ALERT_SMTP_USER or ALERT_SMTP_PASSWORD))
    return {"webhook": webhook, "email": email, "both": webhook and email}


def alert_rule_id(path: str) -> int | None:
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[:2] != ["api", "alerts"] or not parts[2].isdigit():
        return None
    return int(parts[2])


def refresh_alert_states() -> None:
    """Evaluate the newly saved rules against the latest known observations."""
    latest = getattr(database, "latest_by_instrument", None) or getattr(database, "latest", None)
    if not callable(latest):
        return
    manager = AlertManager(database)
    manager.evaluate_prices(latest())
    benchmarks = getattr(database, "latest_benchmarks", None)
    if callable(benchmarks):
        manager.evaluate_prices([dict(row, price=row["value"], data_kind="index") for row in benchmarks("bitume")])
    manager.evaluate_live_freshness(database.source_health())


class Handler(BaseHTTPRequestHandler):
    server_version = "PriceMonitor/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Access logging is emitted once, as structured JSON, by handle_one_request.
        return

    def handle_one_request(self) -> None:
        started = time.perf_counter()
        self.request_id = uuid.uuid4().hex
        self._response_status = 500
        try:
            super().handle_one_request()
        finally:
            duration = time.perf_counter() - started
            method = getattr(self, "command", "UNKNOWN")
            path = urlparse(getattr(self, "path", "")).path or "-"
            status = int(getattr(self, "_response_status", 500))
            metrics.increment(
                "price_monitor_http_requests_total",
                labels={"method": method, "path": path, "status": status},
            )
            metrics.observe_duration("price_monitor_http_request_duration_seconds", duration)
            # Page refreshes and polling stay quiet; metrics still record every
            # response and detailed request logs remain available at DEBUG.
            logger.debug(
                "HTTP request completed",
                extra={
                    "event": "http_request",
                    "request_id": self.request_id,
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": round(duration * 1000, 3),
                    "client_ip": self.client_address[0] if self.client_address else None,
                },
            )

    def send_response(self, code: int, message: str | None = None) -> None:
        self._response_status = code
        super().send_response(code, message)

    def send_data(self, body: bytes, content_type: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-ID", self.request_id)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value: object, status: int = 200) -> None:
        self.send_data(json_bytes(value), "application/json; charset=utf-8", status)

    def _rate_limit(self) -> bool:
        if not RATE_LIMIT_ENABLED:
            return True
        allowed, retry_after = rate_limiter.check(self.client_address[0])
        if allowed:
            return True
        self.send_data(
            json_bytes({"error": "Trop de requêtes. Réessayez plus tard."}),
            "application/json; charset=utf-8",
            HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )
        return False

    def _authenticate_api(self) -> bool:
        if not API_KEY:
            self.send_json(
                {"error": "API non configurée. Définissez PRICE_MONITOR_API_KEY."},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return False
        if not hmac.compare_digest(_authorization_value(self), API_KEY):
            self.send_data(
                json_bytes({"error": "Authentification requise."}),
                "application/json; charset=utf-8",
                HTTPStatus.UNAUTHORIZED,
                headers={"WWW-Authenticate": 'Bearer realm="price-monitor"'},
            )
            return False
        return True

    def _protect_api(self) -> bool:
        return self._authenticate_api()

    def _protect_collection(self) -> bool:
        """Let the local dashboard trigger its own refresh without a secret.

        A collection only contacts configured data providers and writes their
        responses to the local database.  It is safe to make that one action
        convenient on a loopback-only development server. Public/production
        deployments remain key-protected.
        """
        if not API_KEY and ENVIRONMENT in {"development", "dev", "local"} and _is_loopback_host(HOST):
            return True
        return self._protect_api()

    def _protect_local_alerts(self) -> bool:
        # The local dashboard can manage its own alert rules. Public and
        # production deployments still require the configured API key.
        if not API_KEY and ENVIRONMENT in {"development", "dev", "local"} and _is_loopback_host(HOST):
            return True
        return self._protect_api()

    def _protect_local_records(self) -> bool:
        """Allow the local dashboard to read and save quotes and estimates."""
        if (
            not API_KEY
            and ENVIRONMENT in {"development", "dev", "local"}
            and _is_loopback_host(HOST)
            and _is_loopback_host(self.client_address[0])
        ):
            origin = self.headers.get("Origin", "")
            if origin:
                try:
                    parsed_origin = urlparse(origin)
                    same_origin = (
                        parsed_origin.scheme == "http"
                        and _is_loopback_host(parsed_origin.hostname or "")
                        and (parsed_origin.port or 80) == self.server.server_port
                    )
                except ValueError:
                    same_origin = False
            else:
                same_origin = True
            if not same_origin or self.headers.get("Sec-Fetch-Site", "") not in {"", "same-origin", "none"}:
                self.send_json({"error": "Accès réservé au tableau de bord local."}, HTTPStatus.FORBIDDEN)
                return False
            return True
        return self._protect_api()

    def _health(self, live: bool = False) -> None:
        if live:
            return self.send_json({"status": "ok", "service": "price-monitor"})
        check = getattr(database, "healthcheck", None)
        try:
            database_check = check() if callable(check) else {"status": "ok", "backend": "unknown"}
            payload = {
                "status": "ok",
                "service": "price-monitor",
                "checks": {"database": database_check, "scheduler": {"status": "ok", "running": scheduler.is_running}},
            }
            return self.send_json(payload, HTTPStatus.OK)
        except Exception as exc:
            logger.exception("Readiness check failed", extra={"event": "readiness_failed"})
            return self.send_json(
                {"status": "not_ready", "service": "price-monitor", "checks": {"database": {"status": "error", "error": str(exc)}}},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/health/live", "/healthz"}:
            return self._health(live=True)
        if parsed.path in {"/health", "/health/ready", "/readyz"}:
            return self._health()
        if parsed.path == "/metrics":
            return self.send_data(metrics.prometheus().encode("utf-8"), "text/plain; version=0.0.4; charset=utf-8")
        if parsed.path == "/openapi.json":
            return self.send_json(OPENAPI_SPEC)
        if parsed.path == "/docs":
            return self.send_data(
                b"<!doctype html><html lang='fr'><meta charset='utf-8'><title>Price Monitor API</title>"
                b"<h1>Price Monitor API</h1><p>Specification OpenAPI disponible sur "
                b"<a href='/openapi.json'>/openapi.json</a>.</p></html>",
                "text/html; charset=utf-8",
            )
        if parsed.path == "/" or parsed.path == "/index.html":
            return self.serve_file(ROOT / "templates" / "index.html", "text/html; charset=utf-8")
        if parsed.path.startswith("/static/"):
            candidate = (ROOT / parsed.path.lstrip("/")).resolve()
            if ROOT / "static" in candidate.parents:
                return self.serve_file(candidate, mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        query = parse_qs(parsed.query)
        try:
            if parsed.path.startswith("/api/") and not self._rate_limit():
                return
            if parsed.path == "/api/connectivity":
                payload = connectivity.snapshot()
                logs = database.logs(limit=1)
                payload["collection"] = (
                    {"status": logs[0]["status"], "finished_at": logs[0]["finished_at"]}
                    if logs else None
                )
                payload["demo_mode"] = DEMO_MODE
                return self.send_json(payload)
            if parsed.path == "/api/dashboard":
                days = max(1, min(1825, int(query.get("days", [30])[0])))
                instruments = {product: query[f"{product}_instrument"][0] for product in ("brent", "gasoil") if query.get(f"{product}_instrument")}
                return self.send_json(database.dashboard(days, instruments=instruments))
            if parsed.path == "/api/source-comparison":
                return self.send_json(database.source_comparison())
            if parsed.path == "/api/forecast":
                history_days = max(90, min(1825, int(query.get("history_days", [1825])[0])))
                return self.send_json(build_forecast(database, history_days=history_days))
            if parsed.path == "/api/alerts":
                return self.send_json({"alerts": database.alert_rules(), "channels": alert_channel_status()})
            if parsed.path == "/api/observations":
                if not self._protect_local_records():
                    return
                product = query.get("product", [None])[0]
                days = max(1, min(1825, int(query.get("days", [30])[0])))
                instrument = query.get("instrument", [None])[0]
                return self.send_json(database.observations(product, days, instrument=instrument)) if instrument else self.send_json(database.observations(product, days))
            if parsed.path == "/api/history":
                filters = history_filters(query)
                page = max(1, int(query.get("page", [1])[0]))
                page_size = max(1, min(100, int(query.get("page_size", [50])[0])))
                return self.send_json(database.history(**filters, page=page, page_size=page_size))
            if parsed.path == "/api/history/compare":
                required = ("period_a_from", "period_a_to", "period_b_from", "period_b_to")
                missing = [name for name in required if not query.get(name, [""])[0]]
                if missing:
                    return self.send_json({"error": f"Paramètres manquants : {', '.join(missing)}"}, 400)
                filters = history_filters(query)
                filters.pop("date_from", None)
                filters.pop("date_to", None)
                return self.send_json(
                    database.compare_periods(
                        query["period_a_from"][0], query["period_a_to"][0],
                        query["period_b_from"][0], query["period_b_to"][0], **filters,
                    )
                )
            if parsed.path == "/api/logs":
                return self.send_json({"logs": database.logs()})
            if parsed.path == "/api/benchmarks":
                product = query.get("product", [None])[0] or None
                return self.send_json({"benchmarks": database.latest_benchmarks(product=product)})
            if parsed.path == "/api/suppliers":
                product = query.get("product", [None])[0] or None
                region = query.get("region", [None])[0] or None
                return self.send_json({"suppliers": database.supplier_channels(product, region)})
            if parsed.path == "/api/procurement-guide":
                product = query.get("product", ["gasoil"])[0]
                region = query.get("region", ["all"])[0]
                return self.send_json(build_procurement_guide(database, product, region))
            if parsed.path == "/api/procurements":
                if not self._protect_local_records():
                    return
                limit = max(1, min(1000, int(query.get("limit", [100])[0])))
                return self.send_json({"purchases": database.procurements(limit)})
            if parsed.path == "/export.csv":
                body = csv_bytes(database.iter_history(**history_filters(query)))
                return self.send_data(body, "text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=price-observations.csv"})
            if parsed.path == "/export.xlsx":
                body = xlsx_bytes(database.iter_history(**history_filters(query)))
                return self.send_data(body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=price-observations.xlsx"})
            return self.send_json({"error": "Route introuvable"}, 404)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            logger.exception("GET request failed", extra={"event": "http_handler_error", "path": parsed.path})
            return self.send_json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/") and not self._rate_limit():
                return
            if parsed.path == "/api/collect":
                if not self._protect_collection():
                    return
            elif parsed.path == "/api/alerts":
                if not self._protect_local_alerts():
                    return
            elif parsed.path == "/api/procurements":
                if not self._protect_local_records():
                    return
            elif parsed.path == "/api/observations" and not self._protect_local_records():
                return
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if parsed.path == "/api/collect":
                return self.send_json(run_collection(database))
            if parsed.path == "/api/observations":
                record = database.insert_observation(payload)
                return self.send_json({"status": "success", "observation": record}, 201)
            if parsed.path == "/api/procurements":
                record = database.create_procurement(payload)
                return self.send_json({"status": "success", "purchase": record}, 201)
            if parsed.path == "/api/alerts":
                record = database.create_alert_rule(payload)
                refresh_alert_states()
                return self.send_json({"status": "success", "alert": record}, 201)
            return self.send_json({"error": "Route introuvable"}, 404)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            logger.exception("POST request failed", extra={"event": "http_handler_error", "path": parsed.path})
            return self.send_json({"error": str(exc)}, 500)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/") and not self._rate_limit():
                return
            if alert_rule_id(parsed.path) is None or not self._protect_local_alerts():
                return self.send_json({"error": "Route introuvable"}, 404) if alert_rule_id(parsed.path) is None else None
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            record = database.update_alert_rule(alert_rule_id(parsed.path), payload)
            refresh_alert_states()
            return self.send_json({"status": "success", "alert": record})
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            logger.exception("PUT request failed", extra={"event": "http_handler_error", "path": parsed.path})
            return self.send_json({"error": str(exc)}, 500)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/") and not self._rate_limit():
                return
            rule_id = alert_rule_id(parsed.path)
            if rule_id is None:
                return self.send_json({"error": "Route introuvable"}, 404)
            if not self._protect_local_alerts():
                return
            database.delete_alert_rule(rule_id)
            return self.send_json({"status": "success", "deleted": rule_id})
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            logger.exception("DELETE request failed", extra={"event": "http_handler_error", "path": parsed.path})
            return self.send_json({"error": str(exc)}, 500)

    def serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            return self.send_json({"error": "Fichier introuvable"}, 404)
        self.send_data(path.read_bytes(), content_type)


def main() -> None:
    validate_security_configuration()
    connectivity.start()
    scheduler.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    logger.info("Price Monitor started", extra={"event": "service_started", "host": HOST, "port": PORT})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutdown requested", extra={"event": "service_shutdown"})
    finally:
        connectivity.stop()
        scheduler.stop()
        server.server_close()


if __name__ == "__main__":
    main()
