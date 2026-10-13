from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from price_monitor import app as app_module


class StubDatabase:
    def __init__(self) -> None:
        self.observation_calls = 0
        self.history_calls = 0
        self.alerts = []
        self.collection_logs = []
        self.purchases = []
        self.procurement_calls = 0

    def logs(self, limit=12):
        return self.collection_logs[:limit]

    def source_comparison(self):
        return {"status": "unverified", "sources": [], "pairs": [], "mismatch_count": 0}

    def observations(self, product=None, days=30, limit=600, instrument=None):
        self.observation_calls += 1
        return [{"product": product or "bitume", "days": days}]

    def insert_observation(self, payload):
        self.observation_calls += 1
        return payload

    def history(self, **kwargs):
        self.history_calls += 1
        return {"items": [{"product": "gasoil"}], "page": 1, "pages": 1, "total": 1}

    def compare_periods(self, *args, **kwargs):
        self.history_calls += 1
        return {"products": {}}

    def alert_rules(self):
        return self.alerts

    def create_alert_rule(self, payload):
        alert = {"id": 1, **payload, "status": "normal"}
        self.alerts = [alert]
        return alert

    def update_alert_rule(self, rule_id, payload):
        self.alerts[0].update(payload)
        return self.alerts[0]

    def delete_alert_rule(self, rule_id):
        self.alerts = []

    def procurements(self, limit=100):
        self.procurement_calls += 1
        return self.purchases[:limit]

    def create_procurement(self, payload):
        self.procurement_calls += 1
        purchase = {"id": len(self.purchases) + 1, **payload}
        self.purchases.insert(0, purchase)
        return purchase


@pytest.fixture
def api_server(monkeypatch):
    database = StubDatabase()
    monkeypatch.setattr(app_module, "database", database)
    monkeypatch.setattr(app_module, "API_KEY", "test-api-key")
    monkeypatch.setattr(app_module, "RATE_LIMIT_ENABLED", False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, database
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(server, method, path, headers=None, body=None):
    connection = http.client.HTTPConnection(*server.server_address)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = response.read()
    headers = dict(response.getheaders())
    connection.close()
    return response.status, headers, result


def test_connectivity_route_reports_external_status_separately_from_collection(api_server, monkeypatch):
    server, database = api_server
    monkeypatch.setattr(app_module.connectivity, "snapshot", lambda: {
        "status": "offline", "checked_at": "2026-10-06T12:00:00+00:00", "changed_at": "2026-10-06T11:59:00+00:00",
    })
    database.collection_logs = [{"status": "error", "finished_at": "2026-10-06T11:59:30+00:00", "message": "private details"}]

    status, _, body = request(server, "GET", "/api/connectivity")

    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "offline"
    assert payload["collection"] == {"status": "error", "finished_at": "2026-10-06T11:59:30+00:00"}
    assert "private details" not in body.decode()
    assert database.observation_calls == 0


def test_connectivity_route_handles_startup_without_a_collection(api_server, monkeypatch):
    server, _ = api_server
    monkeypatch.setattr(app_module.connectivity, "snapshot", lambda: {
        "status": "checking", "checked_at": None, "changed_at": None,
    })

    status, _, body = request(server, "GET", "/api/connectivity")

    assert status == 200
    assert json.loads(body)["collection"] is None


def test_source_comparison_is_public_and_keeps_missing_data_unverified(api_server):
    server, _ = api_server
    status, _, body = request(server, "GET", "/api/source-comparison")
    assert status == 200
    assert json.loads(body) == {"status": "unverified", "sources": [], "pairs": [], "mismatch_count": 0}


def test_page_refreshes_and_status_polls_do_not_repeat_console_messages(api_server, caplog):
    server, _ = api_server
    with caplog.at_level("INFO"):
        for _ in range(3):
            for path in ("/", "/static/connectivity.js", "/api/connectivity", "/api/logs"):
                status, _, _ = request(server, "GET", path)
                assert status == 200
    assert not [record for record in caplog.records if getattr(record, "event", None) == "http_request"]


def test_protected_routes_reject_missing_api_key(api_server):
    server, database = api_server

    status, headers, _ = request(server, "GET", "/api/observations")

    assert status == 401
    assert headers["WWW-Authenticate"] == 'Bearer realm="price-monitor"'
    assert database.observation_calls == 0


def test_protected_routes_accept_api_key_headers(api_server, monkeypatch):
    server, database = api_server
    monkeypatch.setattr(
        app_module,
        "run_collection",
        lambda _database: {"status": "success", "rows": 0, "message": "ok"},
    )

    status, _, _ = request(server, "GET", "/api/observations", {"X-API-Key": "test-api-key"})
    assert status == 200

    status, _, _ = request(
        server,
        "POST",
        "/api/collect",
        {"Authorization": "Bearer test-api-key", "Content-Length": "0"},
    )
    assert status == 200
    assert database.observation_calls == 1


def test_forecast_route_returns_public_market_forecast(api_server):
    server, database = api_server

    status, _, body = request(server, "GET", "/api/forecast?history_days=90")

    assert status == 200
    assert database.observation_calls == 3
    assert body and b'"products"' in body
    assert set(json.loads(body)["products"]) == {"gasoil", "brent", "bitume"}


def test_alert_page_reports_email_readiness(api_server, monkeypatch):
    server, _ = api_server
    monkeypatch.setattr(app_module, "ALERT_EMAIL_TO", ("person@example.com",))
    monkeypatch.setattr(app_module, "ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(app_module, "ALERT_SMTP_FROM", "person@example.com")
    monkeypatch.setattr(app_module, "ALERT_SMTP_USER", "person@example.com")
    monkeypatch.setattr(app_module, "ALERT_SMTP_PASSWORD", "")

    status, _, body = request(server, "GET", "/api/alerts")
    assert status == 200
    assert json.loads(body)["channels"]["email"] is False

    monkeypatch.setattr(app_module, "ALERT_SMTP_PASSWORD", "test-secret")
    status, _, body = request(server, "GET", "/api/alerts")
    assert status == 200
    assert json.loads(body)["channels"]["email"] is True


def test_read_only_history_is_public_while_observation_writes_stay_protected(api_server):
    server, database = api_server

    status, _, body = request(server, "GET", "/api/history?page=1&page_size=50")

    assert status == 200
    assert database.history_calls == 1
    assert b'"total": 1' in body

    payload = json.dumps({"product": "bitume", "price": 500})
    status, _, _ = request(
        server,
        "POST",
        "/api/observations",
        {"Content-Type": "application/json", "Content-Length": str(len(payload))},
        payload,
    )
    assert status == 401


def test_alert_crud_requires_key_for_writes_and_supports_edit_and_delete(api_server):
    server, _ = api_server
    body = json.dumps({"product": "brent", "direction": "above", "threshold": 90, "channel": "webhook"})
    headers = {"X-API-Key": "test-api-key", "Content-Type": "application/json", "Content-Length": str(len(body))}

    status, _, _ = request(server, "POST", "/api/alerts", body=body)
    assert status == 401

    status, _, _ = request(server, "POST", "/api/alerts", headers=headers, body=body)
    assert status == 201
    status, _, body = request(server, "PUT", "/api/alerts/1", headers={**headers, "Content-Length": str(len('{"muted": true}'))}, body='{"muted": true}')
    assert status == 200
    assert b'"muted": true' in body
    status, _, _ = request(server, "DELETE", "/api/alerts/1", headers={"X-API-Key": "test-api-key"})
    assert status == 200


def test_rate_limiter_returns_retry_after_for_production_requests(api_server, monkeypatch):
    server, _ = api_server
    monkeypatch.setattr(app_module, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(app_module, "rate_limiter", app_module.RateLimiter(1, 60))
    headers = {"X-API-Key": "test-api-key"}

    first_status, _, _ = request(server, "GET", "/api/observations", headers)
    second_status, second_headers, _ = request(server, "GET", "/api/observations", headers)

    assert first_status == 200
    assert second_status == 429
    assert int(second_headers["Retry-After"]) >= 1


def test_missing_api_key_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "")
    monkeypatch.setattr(app_module, "ENVIRONMENT", "production")
    monkeypatch.setattr(app_module, "HOST", "127.0.0.1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, _ = request(server, "GET", "/api/observations")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 503


def test_local_manual_collection_works_without_an_api_key(monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "")
    monkeypatch.setattr(app_module, "ENVIRONMENT", "development")
    monkeypatch.setattr(app_module, "HOST", "127.0.0.1")
    monkeypatch.setattr(
        app_module,
        "run_collection",
        lambda _database: {"status": "success", "rows": 2, "message": "ok"},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = request(server, "POST", "/api/collect", {"Content-Length": "0"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 200
    assert b'"rows": 2' in body


def test_local_alert_rule_can_be_created_without_an_api_key(monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "")
    monkeypatch.setattr(app_module, "ENVIRONMENT", "development")
    monkeypatch.setattr(app_module, "HOST", "127.0.0.1")
    database = StubDatabase()
    monkeypatch.setattr(app_module, "database", database)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = json.dumps({"product": "brent", "direction": "stale", "threshold": 15, "channel": "email"})
    try:
        status, _, body = request(server, "POST", "/api/alerts", {"Content-Type": "application/json"}, payload)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 201
    assert b'"direction": "stale"' in body


def test_local_budget_reads_and_saves_real_estimates_without_a_key(api_server, empty_database, monkeypatch):
    server, _ = api_server
    monkeypatch.setattr(app_module, "database", empty_database)
    monkeypatch.setattr(app_module, "API_KEY", "")
    monkeypatch.setattr(app_module, "ENVIRONMENT", "development")
    monkeypatch.setattr(app_module, "HOST", "127.0.0.1")
    headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.server_port}", "Sec-Fetch-Site": "same-origin"}
    status, _, body = request(server, "GET", "/api/procurements?limit=20", headers)
    assert status == 200 and json.loads(body) == {"purchases": []}

    payload = {"product": "bitume", "supplier": "Test supplier", "quantity": 10, "currency": "USD", "unit_price": 500, "transport_cost": 100, "exchange_rate": 1, "budget_amount": 5000}
    status, _, body = request(server, "POST", "/api/procurements", headers, json.dumps(payload))
    assert status == 201
    purchase = json.loads(body)["purchase"]
    assert purchase["total_cost"] == 5100 and purchase["budget_variance"] == 100
    status, _, body = request(server, "GET", "/api/procurements?limit=20", headers)
    assert status == 200
    assert json.loads(body)["purchases"][0]["id"] == purchase["id"]


def test_local_observation_api_reads_and_saves_valid_supplier_quotes(api_server, empty_database, monkeypatch):
    server, _ = api_server
    monkeypatch.setattr(app_module, "database", empty_database)
    monkeypatch.setattr(app_module, "API_KEY", "")
    monkeypatch.setattr(app_module, "ENVIRONMENT", "development")
    monkeypatch.setattr(app_module, "HOST", "127.0.0.1")
    headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.server_port}", "Sec-Fetch-Site": "same-origin"}
    payload = {"product": "bitume", "source": "Test supplier quote", "price": 500, "unit": "USD/tonne"}
    status, _, body = request(server, "POST", "/api/observations", headers, json.dumps(payload))
    assert status == 201
    status, _, body = request(server, "GET", "/api/observations?product=bitume", headers)
    assert status == 200
    assert json.loads(body)[0]["price"] == 500
    payload["unit"] = "indice"
    status, _, _ = request(server, "POST", "/api/observations", headers, json.dumps(payload))
    assert status == 400


@pytest.mark.parametrize(("environment", "host", "key", "headers", "status"), [
    ("development", "127.0.0.1", "configured", {}, 401),
    ("development", "127.0.0.1", "configured", {"X-API-Key": "wrong"}, 401),
    ("production", "127.0.0.1", "configured", {}, 401),
    ("production", "127.0.0.1", "", {}, 503),
    ("development", "0.0.0.0", "", {}, 503),
    ("development", "0.0.0.0", "configured", {}, 401),
    ("production", "0.0.0.0", "configured", {"X-API-Key": "configured"}, 200),
])
@pytest.mark.parametrize("path", ["/api/procurements", "/api/observations"])
def test_record_authentication_remains_required_outside_keyless_local_mode(api_server, monkeypatch, environment, host, key, headers, status, path):
    server, database = api_server
    monkeypatch.setattr(app_module, "ENVIRONMENT", environment)
    monkeypatch.setattr(app_module, "HOST", host)
    monkeypatch.setattr(app_module, "API_KEY", key)
    get_status, _, _ = request(server, "GET", path, headers)
    post_status, _, _ = request(server, "POST", path, headers, '{}')
    assert get_status == status
    assert post_status == (201 if status == 200 else status)
    assert (database.procurement_calls if path == "/api/procurements" else database.observation_calls) == (2 if status == 200 else 0)


@pytest.mark.parametrize("headers", [
    {"Origin": "https://other-site.example"},
    {"Origin": "null"},
    {"Origin": "http://127.0.0.1:invalid"},
    {"Origin": "http://localhost:1"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
@pytest.mark.parametrize("path", ["/api/procurements", "/api/observations"])
def test_local_records_reject_requests_from_other_sites(api_server, monkeypatch, headers, path):
    server, database = api_server
    monkeypatch.setattr(app_module, "API_KEY", "")
    monkeypatch.setattr(app_module, "ENVIRONMENT", "development")
    monkeypatch.setattr(app_module, "HOST", "127.0.0.1")
    for method in ("GET", "POST"):
        status, _, _ = request(server, method, path, headers, '{}' if method == "POST" else None)
        assert status == 403
    assert database.procurement_calls == 0
    assert database.observation_calls == 0


@pytest.mark.parametrize(
    ("environment", "host", "api_key", "should_fail"),
    [
        ("production", "127.0.0.1", "", True),
        ("development", "0.0.0.0", "", True),
        ("production", "127.0.0.1", "configured", False),
    ],
)
def test_security_configuration_requires_key_for_production_or_public_bind(
    monkeypatch, environment, host, api_key, should_fail
):
    monkeypatch.setattr(app_module, "ENVIRONMENT", environment)
    monkeypatch.setattr(app_module, "HOST", host)
    monkeypatch.setattr(app_module, "API_KEY", api_key)

    if should_fail:
        with pytest.raises(RuntimeError):
            app_module.validate_security_configuration()
    else:
        app_module.validate_security_configuration()
