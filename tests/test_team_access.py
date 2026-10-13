from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import http.client
from http.cookies import SimpleCookie
import json
import threading
import time

from argon2 import PasswordHasher
import pytest

from price_monitor import app, access_store
from price_monitor.http_wsgi import WSGIServer

PASSWORD = "private-test-password-123"


@pytest.fixture
def team_server(empty_database, monkeypatch):
    monkeypatch.setattr(access_store, "PASSWORDS", PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1))
    users = {}
    for name, role in (("owner", "admin"), ("editor", "editor"), ("reader", "viewer")):
        users[name] = empty_database.create_team_user({"username": name, "full_name": name, "role": role, "password": PASSWORD})
    monkeypatch.setattr(app, "database", empty_database)
    monkeypatch.setattr(app, "TEAM_MODE", True)
    monkeypatch.setattr(app, "ENVIRONMENT", "development")
    monkeypatch.setattr(app, "HOST", "127.0.0.1")
    monkeypatch.setattr(app, "PUBLIC_ORIGIN", "")
    monkeypatch.setattr(app, "SECURE_COOKIES", False)
    monkeypatch.setattr(app, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(app, "API_KEY", "legacy-key")
    monkeypatch.setattr(app, "refresh_alert_states", lambda: None)
    monkeypatch.setattr(app, "run_collection", lambda database: {"status": "success", "rows": 0})
    server = WSGIServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, empty_database, users
    server.shutdown()
    thread.join(2)


def request(server, method, path, headers=None, payload=None):
    headers = dict(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload)
        headers["Content-Type"] = "application/json"
    client = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        client.request(method, path, body=body, headers=headers)
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


def sign_in(server, name="owner"):
    status, headers, body = request(server, "POST", "/api/auth/login", payload={"username": name, "password": PASSWORD})
    assert status == 200
    cookie = SimpleCookie(next(value for key, value in headers.items() if key.lower() == "set-cookie"))
    session = cookie["pm_session"].value
    return {"Cookie": f"pm_session={session}", "X-CSRF-Token": json.loads(body)["csrf_token"]}, session


@pytest.mark.parametrize("path", ["/api/history", "/api/history/compare", "/export.csv", "/export.xlsx",
    "/api/observations", "/api/dashboard", "/api/procurements", "/api/procurement-guide", "/api/quotes",
    "/api/quotes/1/documents/1", "/api/quotes/1/history", "/api/logs", "/api/audit", "/api/users"])
def test_team_private_routes_reject_anonymous_and_legacy_shared_keys(team_server, path):
    server, database, _ = team_server
    for headers in ({}, {"X-API-Key": "legacy-key"}):
        status, _, body = request(server, "GET", path, headers)
        assert status == 401
        assert b"supplier" not in body.lower()


def test_sessions_are_opaque_hashed_and_cookie_is_not_readable_by_scripts(team_server):
    server, database, users = team_server
    headers, session = sign_in(server, "reader")
    status, _, body = request(server, "GET", "/api/auth/session", headers)
    assert status == 200
    assert json.loads(body)["user"]["role"] == "viewer"
    assert PASSWORD.encode() not in body and b"password_hash" not in body
    with database.connect() as conn:
        stored = conn.execute("SELECT token_hash FROM team_sessions").fetchone()["token_hash"]
        encoded = conn.execute("SELECT password_hash FROM team_users WHERE id=?", (users["reader"]["id"],)).fetchone()["password_hash"]
    assert stored != session and len(stored) == 64
    assert encoded.startswith("$argon2id$") and PASSWORD not in encoded
    status, response_headers, _ = request(server, "POST", "/api/auth/login", payload={"username": "reader", "password": PASSWORD})
    cookie = next(value for key, value in response_headers.items() if key.lower() == "set-cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie


@pytest.mark.parametrize(("method", "path", "payload"), [("POST", "/api/collect", {}),
    ("POST", "/api/alerts", {"product": "brent", "direction": "above", "threshold": 90}),
    ("PUT", "/api/alerts/1", {"threshold": 91}), ("DELETE", "/api/alerts/1", None),
    ("POST", "/api/observations", {"product": "bitume", "price": 500}),
    ("POST", "/api/quotes", {}), ("POST", "/api/procurements", {}), ("POST", "/api/users", {})])
def test_reader_cannot_mutate_data_or_manage_accounts(team_server, method, path, payload):
    server, database, _ = team_server
    headers, _ = sign_in(server, "reader")
    status, _, _ = request(server, method, path, headers, payload)
    assert status == 403
    assert database.audit_events()["events"][0]["action"] == "access.denied"


def test_editor_writes_are_attributed_and_administration_remains_restricted(team_server):
    server, database, _ = team_server
    headers, _ = sign_in(server, "editor")
    status, _, body = request(server, "POST", "/api/observations", headers,
                             {"product": "bitume", "price": 500, "source": "Private supplier", "source_date": date.today().isoformat()})
    assert status == 201
    events = database.audit_events()["events"]
    assert events[0]["actor"] == "editor" and events[0]["action"] == "observation.created"
    assert events[0]["resource_id"] == str(json.loads(body)["observation"]["id"])
    for path in ("/api/users", "/api/audit"):
        assert request(server, "GET", path, headers)[0] == 403


def test_reader_can_download_requested_history_and_export_is_audited(team_server):
    server, database, _ = team_server
    database.insert_observation({"product": "bitume", "price": 500, "source": "Private supplier", "supplier": "Confidential", "source_date": date.today().isoformat()})
    headers, _ = sign_in(server, "reader")
    assert request(server, "GET", "/api/history", headers)[0] == 200
    status, response_headers, body = request(server, "GET", "/export.csv", headers)
    assert status == 200 and b"Confidential" in body
    assert response_headers["Cache-Control"] == "no-store"
    event = database.audit_events()["events"][0]
    assert event["actor"] == "reader" and event["action"] == "export.prepared"


@pytest.mark.parametrize("path", ["/api/collect", "/api/alerts", "/api/users", "/api/auth/logout"])
def test_session_mutations_require_csrf_even_when_origin_is_absent(team_server, path):
    server, _, _ = team_server
    headers, _ = sign_in(server)
    headers.pop("X-CSRF-Token")
    assert request(server, "POST", path, headers, {})[0] == 403


@pytest.mark.parametrize(("method", "path"), [("POST", "/api/collect"), ("POST", "/api/alerts"),
    ("PUT", "/api/alerts/1"), ("DELETE", "/api/alerts/1")])
def test_origin_checks_cover_collection_and_every_alert_management_method(team_server, monkeypatch, method, path):
    server, database, _ = team_server
    monkeypatch.setattr(app, "TEAM_MODE", False)
    monkeypatch.setattr(app, "API_KEY", "")
    for headers in ({"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Host": "attacker.example"}):
        assert request(server, method, path, headers, {})[0] == 403
    assert database.alert_rules() == []


def test_account_disabling_revokes_existing_sessions_and_personal_tokens(team_server):
    server, database, users = team_server
    member, _ = sign_in(server, "editor")
    status, _, body = request(server, "POST", "/api/auth/tokens", member, {"label": "test tool", "days": 1})
    token = json.loads(body)["token"]
    assert status == 201
    admin, _ = sign_in(server)
    target = users["editor"]
    assert request(server, "PUT", f"/api/users/{target['id']}", admin, {"version": target["version"], "active": False})[0] == 200
    assert request(server, "GET", "/api/history", member)[0] == 401
    assert request(server, "GET", "/api/history", {"Authorization": f"Bearer {token}"})[0] == 401


def test_role_changes_apply_to_existing_sessions_and_personal_tokens(team_server):
    server, database, users = team_server
    member, _ = sign_in(server, "editor")
    _, _, body = request(server, "POST", "/api/auth/tokens", member, {"label": "test tool"})
    token = json.loads(body)["token"]
    admin, _ = sign_in(server)
    target = users["editor"]
    assert request(server, "PUT", f"/api/users/{target['id']}", admin, {"version": target["version"], "role": "viewer"})[0] == 200
    for credentials in (member, {"Authorization": f"Bearer {token}"}):
        assert request(server, "GET", "/api/history", credentials)[0] == 200
        assert request(server, "POST", "/api/collect", credentials, {})[0] == 403


def test_logout_and_password_change_revoke_sessions(team_server):
    server, database, _ = team_server
    headers, session = sign_in(server, "editor")
    assert request(server, "POST", "/api/auth/logout", headers, {})[0] == 200
    assert database.authenticate_session(session) is None
    headers, session = sign_in(server, "editor")
    assert request(server, "POST", "/api/auth/password", headers, {"current_password": PASSWORD, "new_password": "new-private-password-456"})[0] == 200
    assert database.authenticate_session(session) is None
    assert request(server, "POST", "/api/auth/login", payload={"username": "editor", "password": PASSWORD})[0] == 401


def test_personal_token_is_shown_once_revocable_and_bound_to_its_owner(team_server):
    server, _, _ = team_server
    headers, _ = sign_in(server, "editor")
    _, _, body = request(server, "POST", "/api/auth/tokens", headers, {"label": "test tool"})
    token = json.loads(body)
    bearer = {"Authorization": f"Bearer {token['token']}"}
    assert request(server, "POST", "/api/collect", bearer, {})[0] == 200
    _, _, listed = request(server, "GET", "/api/auth/tokens", headers)
    assert token["token"].encode() not in listed and b"token_hash" not in listed
    other, _ = sign_in(server, "reader")
    assert request(server, "DELETE", f"/api/auth/tokens/{token['id']}", other)[0] == 400
    assert request(server, "DELETE", f"/api/auth/tokens/{token['id']}", headers)[0] == 200
    assert request(server, "GET", "/api/history", bearer)[0] == 401


def test_last_administrator_cannot_be_disabled_or_demoted(team_server):
    server, database, users = team_server
    headers, _ = sign_in(server)
    owner = users["owner"]
    for payload in ({"role": "viewer"}, {"active": False}):
        payload["version"] = owner["version"]
        assert request(server, "PUT", f"/api/users/{owner['id']}", headers, payload)[0] == 400
    assert database.active_admin_count() == 1


def test_audit_failure_rolls_back_the_business_mutation(team_server, monkeypatch):
    server, database, _ = team_server
    headers, _ = sign_in(server, "editor")
    def fail(*args, **kwargs):
        raise OSError("audit disk unavailable")
    monkeypatch.setattr(database, "_audit_row", fail)
    assert request(server, "POST", "/api/observations", headers,
                   {"product": "bitume", "price": 500, "source": "Private", "source_date": date.today().isoformat()})[0] == 500
    assert database.history(product="bitume", data_kind="quote")["total"] == 0


def test_idle_and_absolute_session_expiry_are_enforced(team_server):
    server, database, _ = team_server
    _, session = sign_in(server)
    assert database.authenticate_session(session, now=time.time() + 31 * 60) is None
    assert database.authenticate_session(session, now=time.time() + 9 * 3600) is None


def test_login_failures_are_generic_rate_limited_and_never_log_credentials(team_server):
    server, database, _ = team_server
    for _ in range(10):
        status, _, body = request(server, "POST", "/api/auth/login", payload={"username": "editor", "password": "wrong-private-password"})
        assert status == 401 and PASSWORD.encode() not in body
    status, headers, _ = request(server, "POST", "/api/auth/login", payload={"username": "editor", "password": PASSWORD})
    assert status == 429 and headers["Retry-After"] == "900"
    assert "wrong-private-password" not in json.dumps(database.audit_events())


def test_public_startup_requires_accounts_https_and_secure_cookies(team_server, monkeypatch):
    server, database, _ = team_server
    monkeypatch.setattr(app, "HOST", "0.0.0.0")
    monkeypatch.setattr(app, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError):
        app.validate_security_configuration()
    monkeypatch.setattr(app, "PUBLIC_ORIGIN", "https://prices.example.com")
    monkeypatch.setattr(app, "SECURE_COOKIES", True)
    app.validate_security_configuration()
    # An untrusted forwarding header cannot make plain HTTP an HTTPS request.
    status, _, _ = request(server, "POST", "/api/auth/login", {"Host": "prices.example.com", "X-Forwarded-Proto": "https"},
                           {"username": "owner", "password": PASSWORD})
    assert status == 403


def test_quote_corrections_and_documents_use_the_verified_author(team_server):
    from tests.test_quotes import quote_input, document_input
    server, database, _ = team_server
    headers, _ = sign_in(server, "editor")
    status, _, body = request(server, "POST", "/api/quotes", headers, quote_input(actor="forged administrator"))
    assert status == 201
    quote_id = json.loads(body)["quote"]["id"]
    status, _, _ = request(server, "PUT", f"/api/quotes/{quote_id}", headers,
                           {"expected_version": 1, "actor": "forged author", "change_reason": "Correction", "unit_price": 510})
    assert status == 200
    assert request(server, "POST", f"/api/quotes/{quote_id}/documents", headers, document_input(2, actor="forged author"))[0] == 201
    assert {row["actor"] for row in database.quote_history(quote_id)} == {"editor"}
    download = request(server, "GET", f"/api/quotes/{quote_id}/documents/{database.quote(quote_id)['documents'][0]['id']}", headers)
    assert download[0] == 200
    audit = database.audit_events()["events"]
    assert audit[0]["action"] == "document.prepared" and audit[0]["actor"] == "editor"
    assert PASSWORD not in json.dumps(audit) and "content_base64" not in json.dumps(audit)


def test_simultaneous_admin_demotions_keep_one_active_admin(team_server):
    _, database, users = team_server
    other = database.create_team_user({"username": "second-admin", "role": "admin", "password": PASSWORD})
    def demote(user):
        try:
            database.update_team_user(user["id"], {"version": user["version"], "role": "viewer"})
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(demote, [users["owner"], other]))
    assert sum(results) == 1 and database.active_admin_count() == 1
