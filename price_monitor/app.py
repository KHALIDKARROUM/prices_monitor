from __future__ import annotations

import json
import hmac
import mimetypes
import re
import threading
import time
import uuid
import ipaddress
from collections import deque
from http import HTTPStatus
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import parse_qs, urlparse, quote as url_quote

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
    TEAM_MODE, PUBLIC_ORIGIN, SECURE_COOKIES, SESSION_HOURS, TRUSTED_PROXY, URL_SCHEME,
)
from .http_wsgi import WSGIRequest, WSGIServer
from .access_store import ROLES, LoginThrottled, public_user
from .database import create_database
from .observability import logger, metrics
from .openapi import OPENAPI_SPEC
from .services.export import csv_bytes, xlsx_bytes
from .services.forecasting import build_forecast
from .services.alerts import AlertManager
from .services.procurement import build_procurement_guide
from .services.scheduler import CollectionScheduler, CollectionCoordinator
from .services.connectivity import ConnectivityMonitor
from .quotes import INCOTERMS, DOCUMENT_TYPES, PACKAGING, COST_FIELDS, QuoteConflict, QuoteNotFound
from .quote_store import MAX_DOCUMENT_BYTES, DOCUMENT_FORMATS


ROOT = Path(__file__).resolve().parent
database = create_database()
scheduler = CollectionScheduler(database)
connectivity = ConnectivityMonitor()


def run_collection(database):
    return CollectionCoordinator(database).collect_now()


class QuotePayloadTooLarge(ValueError):
    pass


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
    public = ENVIRONMENT in {"production", "prod"} or not _is_loopback_host(HOST)
    if public and not TEAM_MODE:
        raise RuntimeError("Enable PRICE_MONITOR_TEAM_MODE for a shared or public deployment.")
    if TEAM_MODE and not database.active_admin_count():
        raise RuntimeError("Create the first administrator with price-monitor-users bootstrap before starting.")
    if public and (not SECURE_COOKIES or not PUBLIC_ORIGIN):
        raise RuntimeError("Configure PRICE_MONITOR_PUBLIC_ORIGIN with HTTPS and secure cookies.")
    if PUBLIC_ORIGIN:
        parsed = urlparse(PUBLIC_ORIGIN)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise RuntimeError("PRICE_MONITOR_PUBLIC_ORIGIN must be an HTTP(S) origin without a path.")
        if public and parsed.scheme != "https":
            raise RuntimeError("An HTTPS origin is required for public deployment.")
    if URL_SCHEME not in {"http", "https"}:
        raise RuntimeError("PRICE_MONITOR_URL_SCHEME must be http or https.")
    if TRUSTED_PROXY:
        try:
            ipaddress.ip_address(TRUSTED_PROXY)
        except ValueError:
            raise RuntimeError("PRICE_MONITOR_TRUSTED_PROXY must be the exact IP address of the trusted proxy.") from None


def _authorization_value(handler) -> str:
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


class Handler(WSGIRequest):
    server_version = "PriceMonitor/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Access logging is emitted once, as structured JSON, by handle_one_request.
        return

    def handle_request(self) -> None:
        started = time.perf_counter()
        self.request_id = uuid.uuid4().hex
        self._response_status = 500
        try:
            if not self._access():
                return
            method = "GET" if self.command == "HEAD" else self.command
            route = getattr(self, f"do_{method}", None)
            if route is None:
                return self.send_data(json_bytes({"error": "Method not allowed."}), "application/json", 405,
                                      {"Allow": "GET, HEAD, POST, PUT, DELETE"})
            route()
        except Exception:
            logger.exception("Request failed", extra={"event": "http_handler_error"})
            self.send_json({"error": "Internal error. Try again later."}, 500)
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
        self.response_headers = []

    def send_data(self, body: bytes, content_type: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-ID", self.request_id)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.response_body = body

    def send_json(self, value: object, status: int = 200) -> None:
        self.send_data(json_bytes(value), "application/json; charset=utf-8", status)

    def _origin_allowed(self) -> bool:
        try:
            scheme = self.environ.get("wsgi.url_scheme", "http")
            host = urlparse(f"{scheme}://{self.headers.get('Host', '')}")
            if PUBLIC_ORIGIN:
                expected = urlparse(PUBLIC_ORIGIN)
                if (host.hostname, host.port or (443 if scheme == "https" else 80)) != (expected.hostname, expected.port or (443 if expected.scheme == "https" else 80)):
                    return False
            else:
                if not _is_loopback_host(host.hostname or "") or (host.port or 80) != self.server.server_port:
                    return False
                expected = host
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                    return False
                if (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)) != (
                    expected.scheme, expected.hostname, expected.port or (443 if expected.scheme == "https" else 80)
                ):
                    return False
            return self.headers.get("Sec-Fetch-Site", "") in {"", "same-origin", "none"}
        except ValueError:
            return False

    def _identity(self):
        credentials = _authorization_value(self)
        if credentials:
            lookup = getattr(database, "authenticate_user_token", None)
            user = lookup(credentials) if lookup else None
            if user:
                return user
            if not self._team_required() and API_KEY and hmac.compare_digest(credentials.encode(), API_KEY.encode()):
                return {"id": None, "username": "service-key", "role": "admin", "kind": "api_key"}
            return None
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            session = cookies.get("pm_session")
            if session and hasattr(database, "authenticate_session"):
                user = database.authenticate_session(session.value)
                if user:
                    return user
        except CookieError:
            pass
        if (not self._team_required() and not API_KEY and ENVIRONMENT in {"development", "dev", "local"}
                and _is_loopback_host(HOST) and _is_loopback_host(self.client_address[0])):
            return {"id": None, "username": "local-owner", "role": "admin", "kind": "local"}
        return None

    @staticmethod
    def _team_required():
        return TEAM_MODE or ENVIRONMENT in {"production", "prod"} or not _is_loopback_host(HOST)

    def _audit(self, action, resource, resource_id="", **context):
        if hasattr(database, "audit_action"):
            database.audit_action(self.principal, action, resource, resource_id, request_id=self.request_id,
                                  client_ip=self.client_address[0], **context)

    def _deny(self, status, message):
        self._audit("access.denied", "http", outcome="denied", details={"method": self.command, "path": urlparse(self.path).path[:200]})
        self.send_data(json_bytes({"error": message}), "application/json; charset=utf-8", status,
                       {"WWW-Authenticate": 'Bearer realm="price-monitor"'} if status == 401 else None)
        return False

    def _access(self):
        path = urlparse(self.path).path
        public = path in {"/", "/index.html", "/docs", "/openapi.json", "/health", "/healthz", "/health/live", "/health/ready", "/readyz", "/api/connectivity"} or path.startswith("/static/")
        if public and self.command in {"GET", "HEAD"}:
            return True
        if not self._rate_limit():
            return False
        if not self._origin_allowed():
            return self._deny(403, "Access is restricted to the same-origin application.")
        if self._team_required() and PUBLIC_ORIGIN.startswith("https:") and self.environ.get("wsgi.url_scheme") != "https":
            return self._deny(403, "An HTTPS connection is required.")
        self.principal = self._identity()
        if (path == "/api/auth/session" and self.command in {"GET", "HEAD"}) or (path == "/api/auth/login" and self.command == "POST"):
            return True
        if not self.principal:
            return self._deny(401, "Authentification requise.")
        if self.command not in {"GET", "HEAD"} and self.principal.get("kind") == "session":
            if not hmac.compare_digest(self.headers.get("X-CSRF-Token", "").encode(), self.principal["csrf_token"].encode()):
                return self._deny(403, "Invalid session. Reload the page and try again.")
        permission = "admin" if path in {"/metrics", "/api/users", "/api/audit"} or path.startswith("/api/users/") else (
            "viewer" if self.command in {"GET", "HEAD"} or path.startswith("/api/auth/") else "editor")
        if ROLES.get(self.principal.get("role"), 0) < ROLES[permission]:
            return self._deny(403, "Your role does not allow this action.")
        return True

    def _mutation(self, action, resource, operation, payload=None, resource_id=""):
        if hasattr(database, "audited_write"):
            fields = [key[:80] for key in (payload or {}) if key not in {"password", "current_password", "new_password", "content_base64", "actor"}][:50]
            return database.audited_write(self.principal, action, resource, operation, resource_id,
                request_id=self.request_id, client_ip=self.client_address[0], details={"fields": fields})
        return operation()

    def _trusted_payload(self, payload):
        if self.principal and self.principal.get("id"):
            payload["actor"] = self.principal["username"]
        return payload

    def _json_payload(self, limit=1024 * 1024):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > limit:
            raise QuotePayloadTooLarge("The request is too large.")
        if length and self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ValueError("Envoyez un objet JSON.")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Envoyez un objet JSON.")
        return value

    @staticmethod
    def _cookie(value, clear=False):
        cookie = SimpleCookie()
        cookie["pm_session"] = value
        cookie["pm_session"]["path"] = "/"
        cookie["pm_session"]["httponly"] = True
        cookie["pm_session"]["samesite"] = "Strict"
        cookie["pm_session"]["max-age"] = 0 if clear else SESSION_HOURS * 3600
        if SECURE_COOKIES:
            cookie["pm_session"]["secure"] = True
        return cookie.output(header="").strip()

    def _session_payload(self):
        user = public_user(self.principal) if self.principal and self.principal.get("id") else None
        return {"team_mode": self._team_required(), "api_key_mode": bool(API_KEY and not self._team_required()),
                "user": user, "role": self.principal.get("role") if self.principal else None,
                "csrf_token": self.principal.get("csrf_token") if self.principal else None}

    def _auth_post(self, path):
        payload = self._json_payload(64 * 1024)
        if path == "/api/auth/login":
            try:
                result = database.login(payload.get("username"), payload.get("password"), self.client_address[0], self.request_id)
            except LoginThrottled as exc:
                return self.send_data(json_bytes({"error": str(exc)}), "application/json", 429, {"Retry-After": "900"})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 401)
            if self.principal and self.principal.get("kind") == "session":
                database.logout(self.principal, self.request_id, self.client_address[0])
            return self.send_data(json_bytes({"user": result["user"], "csrf_token": result["csrf_token"]}), "application/json",
                                  headers={"Set-Cookie": self._cookie(result["session"])})
        if path == "/api/auth/logout":
            database.logout(self.principal, self.request_id, self.client_address[0])
            return self.send_data(json_bytes({"status": "success"}), "application/json", headers={"Set-Cookie": self._cookie("", True)})
        user_id = self.principal.get("id")
        if not user_id:
            return self.send_json({"error": "Sign in to an individual account for this action."}, 403)
        if path == "/api/auth/password":
            self._mutation("password.changed", "account", lambda: database.change_password(user_id, payload.get("current_password"), payload.get("new_password")), resource_id=user_id)
            return self.send_data(json_bytes({"status": "success", "message": "Password changed. Sign in again."}), "application/json",
                                  headers={"Set-Cookie": self._cookie("", True)})
        if path == "/api/auth/tokens":
            result = self._mutation("token.created", "api_token", lambda: database.create_user_token(user_id, payload.get("label"), payload.get("days", 30)))
            return self.send_json(result, 201)
        return self.send_json({"error": "Route introuvable"}, 404)

    def _rate_limit(self) -> bool:
        if getattr(self, "_rate_checked", False):
            return True
        self._rate_checked = True
        if not RATE_LIMIT_ENABLED:
            return True
        allowed, retry_after = rate_limiter.check(self.client_address[0])
        if allowed:
            return True
        self.send_data(
            json_bytes({"error": "Too many requests. Try again later."}),
            "application/json; charset=utf-8",
            HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )
        return False

    def _protect_collection(self) -> bool:
        """Access, origin and role have already been checked by _access."""
        return self.principal is not None

    def _protect_local_alerts(self) -> bool:
        return self.principal is not None

    def _protect_local_records(self) -> bool:
        """Allow the local dashboard to read and save quotes and estimates."""
        return self.principal is not None

    def _quote_payload(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 8 * 1024 * 1024:
            self.close_connection = True
            raise QuotePayloadTooLarge("The request is too large. Each document is limited to 5 MB.")
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ValueError("Envoyez un objet JSON.")
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Envoyez un objet JSON.")
        return payload

    def _quote_get(self, path: str, query: dict) -> None:
        if not self._protect_local_records():
            return
        if path == "/api/quotes/options":
            return self.send_json({"incoterms": INCOTERMS, "document_types": DOCUMENT_TYPES, "packaging": PACKAGING,
                                   "cost_fields": COST_FIELDS, "max_document_bytes": MAX_DOCUMENT_BYTES,
                                   "document_formats": list(DOCUMENT_FORMATS)})
        if path == "/api/quotes":
            return self.send_json(database.quotes(page=int(query.get("page", [1])[0]), page_size=int(query.get("page_size", [20])[0]),
                                                   product=query.get("product", [None])[0] or None, supplier=query.get("supplier", [""])[0]))
        if path == "/api/quotes/compare":
            ids = [int(value) for value in query.get("ids", [""])[0].split(",") if value]
            return self.send_json(database.compare_quotes(ids, query.get("as_of", [None])[0]))
        match = re.fullmatch(r"/api/quotes/([1-9]\d*)(?:/(history|documents)/?([1-9]\d*)?)?", path)
        if match:
            quote_id, action, document_id = match.groups()
            if action == "history" and not document_id:
                return self.send_json({"revisions": database.quote_history(int(quote_id))})
            if action == "documents" and document_id:
                document = database.quote_document(int(quote_id), int(document_id))
                self._audit("document.prepared", "quote_document", document_id, details={"quote_id": int(quote_id)})
                return self.send_data(bytes(document["content"]), document["content_type"], headers={
                    "Content-Disposition": "attachment; filename=\"document\"; filename*=UTF-8''" + url_quote(document["filename"], safe=""),
                    "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
            if action is None:
                return self.send_json({"quote": database.quote(int(quote_id))})
        return self.send_json({"error": "Route introuvable"}, 404)

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
                {"status": "not_ready", "service": "price-monitor", "checks": {"database": {"status": "error", "error": "Base indisponible."}}},
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
                b"<!doctype html><html lang='en'><meta charset='utf-8'><title>Price Monitor API</title>"
                b"<h1>Price Monitor API</h1><p>OpenAPI specification available at "
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
            if parsed.path == "/api/auth/session":
                return self.send_json(self._session_payload())
            if parsed.path == "/api/users":
                return self.send_json({"users": database.team_users()})
            if parsed.path == "/api/audit":
                return self.send_json(database.audit_events(int(query.get("page", [1])[0]), int(query.get("page_size", [50])[0])))
            if parsed.path == "/api/auth/tokens":
                return self.send_json({"tokens": database.user_tokens(self.principal.get("id"))})
            if parsed.path == "/api/quotes" or parsed.path.startswith("/api/quotes/"):
                return self._quote_get(parsed.path, query)
            if parsed.path == "/api/connectivity":
                payload = connectivity.snapshot()
                logs = database.logs(limit=1)
                payload["collection"] = (
                    {"status": logs[0]["status"], "finished_at": logs[0]["finished_at"]}
                    if logs else None
                )
                if hasattr(database, "collection_state"):
                    payload["collection"] = CollectionCoordinator(database).availability()
                payload["demo_mode"] = DEMO_MODE
                return self.send_json(payload)
            if parsed.path == "/api/collection-schedules":
                return self.send_json({"providers": CollectionCoordinator(database).snapshot()})
            if parsed.path == "/api/dashboard":
                days = max(1, min(1825, int(query.get("days", [30])[0])))
                instruments = {product: query[f"{product}_instrument"][0] for product in ("brent", "gasoil") if query.get(f"{product}_instrument")}
                return self.send_json(database.dashboard(days, instruments=instruments))
            if parsed.path == "/api/source-comparison":
                return self.send_json(database.source_comparison())
            if parsed.path == "/api/forecast":
                history_days = max(90, min(1825, int(query.get("history_days", [1825])[0])))
                return self.send_json(build_forecast(database, history_days=history_days))
            if parsed.path == "/api/forecast-performance":
                window = max(1, min(365, int(query.get("window", [90])[0])))
                product = query.get("product", [None])[0]
                instrument = query.get("instrument", [None])[0]
                horizon = query.get("horizon", [None])[0]
                horizon_value = int(horizon) if horizon not in (None, "") else None
                model_version = query.get("model_version", [None])[0]
                active_version = getattr(database, "active_forecast_model_version", None)
                if not model_version and callable(active_version) and product and instrument and horizon_value is not None:
                    model_version = active_version(product=product, instrument=instrument, horizon=horizon_value)
                performance_args = {"product": product, "instrument": instrument, "horizon": horizon_value}
                if model_version:
                    performance_args["model_version"] = model_version
                return self.send_json(database.live_forecast_performance(window, **performance_args))
            if parsed.path == "/api/model-lifecycle":
                lifecycle = getattr(database, "model_lifecycle", None)
                if not callable(lifecycle):
                    return self.send_json({"criteria": {}, "candidates": [], "events": []})
                horizon = query.get("horizon", [None])[0]
                return self.send_json(lifecycle(
                    product=query.get("product", [None])[0], instrument=query.get("instrument", [None])[0],
                    horizon=int(horizon) if horizon not in (None, "") else None,
                    event_limit=max(1, min(1000, int(query.get("event_limit", [100])[0]))),
                ))
            if parsed.path == "/api/vintages":
                if not self._protect_local_records():
                    return
                return self.send_json({"vintages": database.provider_vintages(
                    product=query.get("product", [None])[0], instrument=query.get("instrument", [None])[0],
                    source=query.get("source", [None])[0], data_kind=query.get("data_kind", [None])[0],
                    limit=max(1, min(1000, int(query.get("limit", [100])[0]))),
                )})
            if parsed.path == "/api/vintages/as-of":
                if not self._protect_local_records():
                    return
                as_of = query.get("as_of", [None])[0]
                if not as_of:
                    raise ValueError("An as_of timestamp is required.")
                return self.send_json(database.point_in_time_observations(
                    as_of, product=query.get("product", [None])[0], instrument=query.get("instrument", [None])[0],
                    data_kind=query.get("data_kind", ["quote"])[0],
                ))
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
                    return self.send_json({"error": f"Missing parameters: {', '.join(missing)}"}, 400)
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
                self._audit("export.prepared", "history", details={"format": "csv"})
                return self.send_data(body, "text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=price-observations.csv"})
            if parsed.path == "/export.xlsx":
                body = xlsx_bytes(database.iter_history(**history_filters(query)))
                self._audit("export.prepared", "history", details={"format": "xlsx"})
                return self.send_data(body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=price-observations.xlsx"})
            return self.send_json({"error": "Route introuvable"}, 404)
        except QuoteNotFound as exc:
            return self.send_json({"error": str(exc)}, 404)
        except QuoteConflict as exc:
            return self.send_json({"error": str(exc)}, 409)
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
            if parsed.path.startswith("/api/auth/"):
                return self._auth_post(parsed.path)
            if parsed.path == "/api/users":
                payload = self._json_payload(64 * 1024)
                user = self._mutation("user.created", "account", lambda: database.create_team_user(payload), payload)
                return self.send_json({"user": user}, 201)
            if parsed.path == "/api/quotes" or parsed.path.startswith("/api/quotes/"):
                if not self._protect_local_records():
                    return
                payload = self._trusted_payload(self._quote_payload())
                if parsed.path == "/api/quotes":
                    quote = self._mutation("quote.created", "quote", lambda: database.create_quote(payload), payload)
                    return self.send_json({"quote": quote}, 201)
                match = re.fullmatch(r"/api/quotes/([1-9]\d*)/documents", parsed.path)
                if match:
                    quote = self._mutation("document.attached", "quote", lambda: database.attach_quote_document(int(match[1]), payload), payload, match[1])
                    return self.send_json({"quote": quote}, 201)
                return self.send_json({"error": "Route introuvable"}, 404)
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
            payload = self._trusted_payload(self._json_payload())
            if parsed.path == "/api/collect":
                self._audit("collection.requested", "collection")
                return self.send_json(run_collection(database))
            if parsed.path == "/api/observations":
                record = self._mutation("observation.created", "observation", lambda: database.insert_observation(payload), payload)
                return self.send_json({"status": "success", "observation": record}, 201)
            if parsed.path == "/api/procurements":
                record = self._mutation("budget.created", "budget", lambda: database.create_procurement(payload), payload)
                return self.send_json({"status": "success", "purchase": record}, 201)
            if parsed.path == "/api/alerts":
                record = self._mutation("alert.created", "alert", lambda: database.create_alert_rule(payload), payload)
                refresh_alert_states()
                return self.send_json({"status": "success", "alert": record}, 201)
            return self.send_json({"error": "Route introuvable"}, 404)
        except QuotePayloadTooLarge as exc:
            return self.send_json({"error": str(exc)}, 413)
        except QuoteNotFound as exc:
            return self.send_json({"error": str(exc)}, 404)
        except QuoteConflict as exc:
            return self.send_json({"error": str(exc)}, 409)
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
            user_match = re.fullmatch(r"/api/users/([1-9]\d*)", parsed.path)
            if user_match:
                payload = self._json_payload(64 * 1024)
                user = self._mutation("user.updated", "account", lambda: database.update_team_user(int(user_match[1]), payload), payload, user_match[1])
                return self.send_json({"user": user})
            match = re.fullmatch(r"/api/quotes/([1-9]\d*)", parsed.path)
            if match:
                if not self._protect_local_records():
                    return
                payload = self._trusted_payload(self._quote_payload())
                quote = self._mutation("quote.corrected", "quote", lambda: database.update_quote(int(match[1]), payload), payload, match[1])
                return self.send_json({"quote": quote})
            if alert_rule_id(parsed.path) is None or not self._protect_local_alerts():
                return self.send_json({"error": "Route introuvable"}, 404) if alert_rule_id(parsed.path) is None else None
            payload = self._json_payload()
            record = self._mutation("alert.updated", "alert", lambda: database.update_alert_rule(alert_rule_id(parsed.path), payload), payload, alert_rule_id(parsed.path))
            refresh_alert_states()
            return self.send_json({"status": "success", "alert": record})
        except QuotePayloadTooLarge as exc:
            return self.send_json({"error": str(exc)}, 413)
        except QuoteNotFound as exc:
            return self.send_json({"error": str(exc)}, 404)
        except QuoteConflict as exc:
            return self.send_json({"error": str(exc)}, 409)
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
            token_match = re.fullmatch(r"/api/auth/tokens/([1-9]\d*)", parsed.path)
            if token_match:
                self._mutation("token.revoked", "api_token", lambda: database.revoke_user_token(self.principal.get("id"), int(token_match[1])), resource_id=token_match[1])
                return self.send_json({"status": "success"})
            match = re.fullmatch(r"/api/quotes/([1-9]\d*)/documents/([1-9]\d*)", parsed.path)
            if match:
                if not self._protect_local_records():
                    return
                payload = self._trusted_payload(self._quote_payload())
                quote = self._mutation("document.retired", "quote", lambda: database.remove_quote_document(int(match[1]), int(match[2]), payload), payload, match[1])
                return self.send_json({"quote": quote})
            rule_id = alert_rule_id(parsed.path)
            if rule_id is None:
                return self.send_json({"error": "Route introuvable"}, 404)
            if not self._protect_local_alerts():
                return
            self._mutation("alert.deleted", "alert", lambda: database.delete_alert_rule(rule_id), resource_id=rule_id)
            return self.send_json({"status": "success", "deleted": rule_id})
        except QuotePayloadTooLarge as exc:
            return self.send_json({"error": str(exc)}, 413)
        except QuoteNotFound as exc:
            return self.send_json({"error": str(exc)}, 404)
        except QuoteConflict as exc:
            return self.send_json({"error": str(exc)}, 409)
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
    server = WSGIServer((HOST, PORT), Handler)
    try:
        connectivity.start()
        scheduler.start()
        logger.info("Price Monitor started", extra={"event": "service_started", "host": HOST, "port": PORT})
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutdown requested", extra={"event": "service_shutdown"})
    finally:
        connectivity.stop()
        scheduler.stop()
        server.server_close()


if __name__ == "__main__":
    main()
