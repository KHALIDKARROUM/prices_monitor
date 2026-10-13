from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from price_monitor import app as app_module
from tests.test_api_security import request
from tests.test_quotes import quote_input, document_input


@pytest.fixture
def quote_server(empty_database, monkeypatch):
    monkeypatch.setattr(app_module, "database", empty_database)
    monkeypatch.setattr(app_module, "API_KEY", "quote-test-key")
    monkeypatch.setattr(app_module, "RATE_LIMIT_ENABLED", False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, empty_database
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def quote_request(server, method, path, payload=None):
    status, headers, body = request(server, method, path,
        {"X-API-Key": "quote-test-key", "Content-Type": "application/json"},
        json.dumps(payload) if payload is not None else None)
    return status, headers, body


def test_quote_api_round_trip_preserves_corrections_documents_and_budget_snapshot(quote_server):
    server, db = quote_server
    status, _, body = quote_request(server, "POST", "/api/quotes", quote_input(insurance_cost=50))
    assert status == 201
    quote = json.loads(body)["quote"]
    id = quote["id"]
    status, _, body = quote_request(server, "PUT", f"/api/quotes/{id}", {"expected_version": 1, "unit_price": 510, "change_reason": "Prix corrigé"})
    assert status == 200 and json.loads(body)["quote"]["version"] == 2
    status, _, _ = quote_request(server, "PUT", f"/api/quotes/{id}", {"expected_version": 1, "unit_price": 520, "change_reason": "Correction ancienne"})
    assert status == 409
    status, _, body = quote_request(server, "POST", f"/api/quotes/{id}/documents", document_input(2))
    assert status == 201
    doc = json.loads(body)["quote"]["documents"][0]
    status, headers, content = quote_request(server, "GET", f"/api/quotes/{id}/documents/{doc['id']}")
    assert status == 200 and content.startswith(b"%PDF-")
    assert headers["Content-Disposition"].startswith("attachment;")
    assert headers["Cache-Control"] == "private, no-store"
    status, _, body = quote_request(server, "POST", "/api/procurements", {"quote_id": id, "quote_version": 3})
    assert status == 201
    purchase = json.loads(body)["purchase"]
    assert purchase["total_cost"] == 5150 and purchase["quote_version"] == 3
    assert purchase["quote_snapshot"]["documents"][0]["sha256"] == doc["sha256"]
    status, _, body = quote_request(server, "GET", f"/api/quotes/{id}/history")
    assert status == 200
    revisions = json.loads(body)["revisions"]
    assert len(revisions) == 3 and revisions[-1]["snapshot"]["unit_price"] == 500
    status, _, _ = quote_request(server, "DELETE", f"/api/quotes/{id}/documents/{doc['id']}", {"expected_version": 3, "change_reason": "Pièce remplacée"})
    assert status == 200
    assert db.quote(id)["version"] == 4
    assert db.procurements()[0]["quote_version"] == 3


@pytest.mark.parametrize("method,path", [("GET", "/api/quotes"), ("GET", "/api/quotes/1"),
    ("GET", "/api/quotes/1/history"), ("GET", "/api/quotes/compare?ids=1,2"),
    ("GET", "/api/quotes/1/documents/1"), ("POST", "/api/quotes"),
    ("PUT", "/api/quotes/1"), ("POST", "/api/quotes/1/documents"), ("DELETE", "/api/quotes/1/documents/1")])
def test_quotes_history_and_documents_are_private(quote_server, method, path):
    server, db = quote_server
    status, _, _ = request(server, method, path)
    assert status == 401
    assert db.quotes()["total"] == 0


def test_local_quotes_deny_cross_site_requests(quote_server, monkeypatch):
    server, _ = quote_server
    monkeypatch.setattr(app_module, "API_KEY", "")
    monkeypatch.setattr(app_module, "ENVIRONMENT", "development")
    monkeypatch.setattr(app_module, "HOST", "127.0.0.1")
    status, _, _ = request(server, "GET", "/api/quotes", {"Origin": "https://example.org", "Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, _, _ = request(server, "GET", "/api/quotes", {"Origin": f"http://127.0.0.1:{server.server_port}", "Sec-Fetch-Site": "same-origin"})
    assert status == 200


def test_document_request_size_is_rejected_before_reading_the_body(quote_server):
    server, _ = quote_server
    status, _, _ = request(server, "POST", "/api/quotes/1/documents",
                          {"X-API-Key": "quote-test-key", "Content-Type": "application/json", "Content-Length": str(9 * 1024 * 1024)})
    assert status == 413


def test_api_comparison_rejects_incomparable_terms_without_a_false_winner(quote_server):
    server, db = quote_server
    a = db.create_quote(quote_input())
    b = db.create_quote(quote_input(grade="70/100", supplier="Other", unit_price=100))
    status, _, body = quote_request(server, "GET", f"/api/quotes/compare?ids={a['id']},{b['id']}")
    assert status == 200
    data = json.loads(body)
    assert not data["equivalent"] and data["ranking"] == []
    assert "Grade" in data["differences"][1]["reasons"]


@pytest.mark.parametrize("payload", [[], {"product": []}, quote_input(grade=""), quote_input(document_type=[]), quote_input(exchange_rate=0)])
def test_invalid_json_and_fields_are_reported_as_input_errors(quote_server, payload):
    server, _ = quote_server
    status, _, _ = quote_request(server, "POST", "/api/quotes", payload)
    assert status == 400
