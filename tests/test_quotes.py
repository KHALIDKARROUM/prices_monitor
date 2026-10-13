from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import hashlib
import sqlite3

import pytest

from price_monitor.quotes import COST_FIELDS, QuoteConflict, QuoteNotFound
from price_monitor.quote_store import MAX_DOCUMENT_BYTES
from price_monitor.backup import backup_sqlite
from price_monitor.database import Database, SCHEMA, SQLITE_MIGRATIONS_DIR, run_sqlite_migrations


def quote_input(**changes):
    today = date.today()
    return {"product": "bitume", "supplier": "Supplier A", "reference": "Q-123", "grade": "50/70",
            "specification": "EN 12591", "origin": "Maroc", "incoterm": "DAP", "incoterm_place": "Casablanca",
            "destination": "Casablanca", "payment_terms": "30 jours", "packaging": "bulk", "document_type": "quotation",
            "quote_date": today.isoformat(), "valid_until": (today + timedelta(days=30)).isoformat(),
            "delivery_date": (today + timedelta(days=7)).isoformat(), "quantity": 10, "unit_price": 500,
            "currency": "USD", "exchange_rate": 1, **changes}


def document_input(version, content=b"%PDF-1.4\nSupporting test quote\n%%EOF", **changes):
    return {"expected_version": version, "filename": "offre.pdf", "document_type": "quotation",
            "content_base64": base64.b64encode(content).decode(), "change_reason": "Pièce fournisseur", **changes}


def test_structured_quote_and_landed_costs_do_not_become_public_market_observations(empty_database):
    quote = empty_database.create_quote(quote_input(insurance_cost=25, inspection_cost=50, duties_cost=100,
        taxes_cost=20, storage_cost=30, heating_cost=40, finance_cost=5, transport_cost=150,
        other_costs=[{"label": "Port", "amount": 80}]))
    assert quote["grade"] == "50/70" and quote["origin"] == "Maroc"
    assert quote["total_cost"] == quote["total_cost_usd"] == 5500
    assert quote["landed_unit_cost_usd"] == 550
    assert sum(item["amount"] for item in quote["cost_breakdown"]) == quote["total_cost"]
    assert empty_database.history(product="bitume", data_kind="quote")["total"] == 0
    assert empty_database.quote_history(quote["id"])[0]["snapshot"]["grade"] == "50/70"


def test_corrections_keep_before_after_values_and_require_reason_and_version(empty_database):
    quote = empty_database.create_quote(quote_input())
    for payload in ({"unit_price": 510, "expected_version": 1}, {"unit_price": 510, "change_reason": "Correction"}):
        with pytest.raises(ValueError):
            empty_database.update_quote(quote["id"], payload)
    updated = empty_database.update_quote(quote["id"], {"expected_version": 1, "unit_price": 510,
                                                        "grade": "70/100", "change_reason": "Devis corrigé", "actor": "Khalid"})
    assert updated["version"] == 2 and updated["total_cost"] == 5100
    revisions = empty_database.quote_history(quote["id"])
    assert revisions[0]["actor"] == "Khalid" and revisions[0]["reason"] == "Devis corrigé"
    assert set(revisions[0]["changed_fields"]) == {"grade", "unit_price"}
    assert revisions[1]["snapshot"]["grade"] == "50/70"
    assert revisions[1]["snapshot"]["unit_price"] == 500
    with pytest.raises(QuoteConflict):
        empty_database.update_quote(quote["id"], {"expected_version": 1, "unit_price": 520, "change_reason": "Autre correction"})
    assert len(empty_database.quote_history(quote["id"])) == 2


def test_concurrent_corrections_cannot_overwrite_each_other(empty_database):
    quote = empty_database.create_quote(quote_input())
    def change(price):
        try:
            return empty_database.update_quote(quote["id"], {"expected_version": 1, "unit_price": price, "change_reason": "Correction"})
        except QuoteConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [510, 520]))
    assert sum(result is not None for result in results) == 1
    assert empty_database.quote(quote["id"])["version"] == 2
    assert len(empty_database.quote_history(quote["id"])) == 2


def test_documents_are_versioned_and_retained_after_withdrawal_and_backup(empty_database, tmp_path):
    quote = empty_database.create_quote(quote_input())
    content = b"%PDF-1.4\nSupporting test quote\n%%EOF"
    updated = empty_database.attach_quote_document(quote["id"], document_input(1, content))
    document = updated["documents"][0]
    assert updated["version"] == 2 and document["sha256"] == hashlib.sha256(content).hexdigest()
    assert empty_database.quote_document(quote["id"], document["id"])["content"] == content
    other = empty_database.create_quote(quote_input(supplier="Other"))
    with pytest.raises(QuoteNotFound):
        empty_database.quote_document(other["id"], document["id"])
    retired = empty_database.remove_quote_document(quote["id"], document["id"], {"expected_version": 2, "change_reason": "Pièce remplacée"})
    assert retired["version"] == 3 and retired["documents"][0]["removed_at"]
    assert empty_database.quote_document(quote["id"], document["id"])["content"] == content
    revisions = empty_database.quote_history(quote["id"])
    assert revisions[1]["snapshot"]["documents"][0]["removed_at"] is None
    destination = backup_sqlite(tmp_path / "saved.db", empty_database.path)
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT content FROM quote_documents").fetchone()[0] == content
        assert conn.execute("SELECT COUNT(*) FROM quote_revisions").fetchone()[0] == 4  # other quote creation


@pytest.mark.parametrize("changes", [{"filename": "../offre.pdf"}, {"filename": "offre.html"},
    {"filename": "offre.pdf\r\nX-Test: bad"}, {"content_base64": "not base64"},
    {"content_base64": base64.b64encode(b"not a pdf").decode()}, {"document_type": ["quotation"]}])
def test_bad_documents_leave_the_record_and_history_unchanged(empty_database, changes):
    quote = empty_database.create_quote(quote_input())
    with pytest.raises(ValueError):
        empty_database.attach_quote_document(quote["id"], document_input(1, **changes))
    assert empty_database.quote(quote["id"])["version"] == 1
    assert empty_database.quote(quote["id"])["documents"] == []
    assert len(empty_database.quote_history(quote["id"])) == 1


def test_large_and_stale_document_uploads_are_atomic(empty_database):
    quote = empty_database.create_quote(quote_input())
    with pytest.raises(ValueError, match="5 Mo"):
        empty_database.attach_quote_document(quote["id"], document_input(1, b"%PDF-" + b"x" * MAX_DOCUMENT_BYTES))
    empty_database.update_quote(quote["id"], {"expected_version": 1, "notes": "Corrigé", "change_reason": "Précision"})
    with pytest.raises(QuoteConflict):
        empty_database.attach_quote_document(quote["id"], document_input(1))
    assert empty_database.quote(quote["id"])["documents"] == []


def test_equivalent_quotes_compare_common_units_and_currency_converted_full_costs(empty_database):
    first = empty_database.create_quote(quote_input(unit_price=500, transport_cost=200))
    second = empty_database.create_quote(quote_input(supplier="Supplier B", grade=" 50/70 ", currency="EUR", unit_price=450, exchange_rate=1.1, insurance_cost=100))
    result = empty_database.compare_quotes([first["id"], second["id"]])
    assert result["equivalent"]
    assert result["ranking"][0]["quote_id"] == second["id"]
    assert result["ranking"][0]["total_cost_usd"] == 5060


@pytest.mark.parametrize("changed", [{"grade": "70/100"}, {"origin": "France"}, {"incoterm": "FOB"},
    {"destination": "Tanger"}, {"quantity": 20}, {"payment_terms": "Comptant"},
    {"specification": "Autre norme"}, {"document_type": "invoice"}, {"status": "withdrawn"},
    {"delivery_date": (date.today() + timedelta(days=14)).isoformat()}, {"packaging": "drums"}])
def test_different_terms_are_visible_but_never_ranked_as_equivalent(empty_database, changed):
    first = empty_database.create_quote(quote_input())
    second = empty_database.create_quote(quote_input(supplier="Other", **changed))
    result = empty_database.compare_quotes([first["id"], second["id"]])
    assert not result["equivalent"] and result["ranking"] == []
    assert result["differences"][1]["reasons"]


def test_expired_quotes_can_be_compared_only_at_a_date_when_both_were_valid(empty_database):
    old = (date.today() - timedelta(days=10)).isoformat()
    expired = (date.today() - timedelta(days=1)).isoformat()
    first = empty_database.create_quote(quote_input(quote_date=old, valid_until=expired))
    second = empty_database.create_quote(quote_input(quote_date=old, valid_until=expired, supplier="Other"))
    assert not empty_database.compare_quotes([first["id"], second["id"]])["equivalent"]
    assert empty_database.compare_quotes([first["id"], second["id"]], old)["equivalent"]


def test_budget_includes_all_costs_and_preserves_source_quote_version(empty_database):
    quote = empty_database.create_quote(quote_input(currency="EUR", exchange_rate=1.1, transport_cost=100,
        insurance_cost=20, inspection_cost=30, duties_cost=40, storage_cost=50, heating_cost=60,
        taxes_cost=70, finance_cost=80, other_costs=[{"label": "Port", "amount": 90}]))
    purchase = empty_database.create_procurement({"quote_id": quote["id"], "quote_version": 1, "budget_amount": 6000})
    assert purchase["total_cost"] == 5540
    assert purchase["total_cost_usd"] == 6094
    assert purchase["budget_variance"] == -460
    assert purchase["quote_snapshot"]["grade"] == "50/70"
    empty_database.update_quote(quote["id"], {"expected_version": 1, "unit_price": 900, "change_reason": "Prix actualisé"})
    saved = empty_database.procurements()[0]
    assert saved["total_cost"] == 5540 and saved["quote_version"] == 1
    assert saved["quote_snapshot"]["unit_price"] == 500
    with pytest.raises(QuoteConflict):
        empty_database.create_procurement({"quote_id": quote["id"], "quote_version": 1})


def test_budget_rechecks_quote_version_before_saving(empty_database, monkeypatch):
    quote = empty_database.create_quote(quote_input())
    latest = empty_database.latest_by_instrument
    def concurrent_correction():
        empty_database.update_quote(quote["id"], {"expected_version": 1, "unit_price": 600, "change_reason": "Prix reçu pendant la préparation"})
        return latest()
    monkeypatch.setattr(empty_database, "latest_by_instrument", concurrent_correction)
    with pytest.raises(QuoteConflict):
        empty_database.create_procurement({"quote_id": quote["id"], "quote_version": 1})
    assert empty_database.procurements() == []


@pytest.mark.parametrize("field", COST_FIELDS)
@pytest.mark.parametrize("invalid", [-1, "nan", "inf", "not-a-number"])
def test_costs_must_be_finite_nonnegative_amounts(empty_database, field, invalid):
    with pytest.raises(ValueError):
        empty_database.create_procurement(quote_input(**{field: invalid}))


def test_quote_pagination_does_not_hide_records_and_migrations_repeat_safely(empty_database):
    for index in range(5):
        empty_database.create_quote(quote_input(supplier=f"Supplier {index}"))
    pages = [empty_database.quotes(page=page, page_size=2) for page in range(1, 4)]
    assert all(page["total"] == 5 and page["pages"] == 3 for page in pages)
    assert len({quote["id"] for page in pages for quote in page["items"]}) == 5
    empty_database.initialize()
    assert empty_database.quotes()["total"] == 5


def test_upgrade_keeps_preexisting_budget_amounts_and_notes(tmp_path, empty_database):
    migrations = tmp_path / "old_migrations"
    migrations.mkdir()
    for source in SQLITE_MIGRATIONS_DIR.glob("*.sql"):
        if source.name < "011":
            (migrations / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        run_sqlite_migrations(conn, migrations)
        conn.execute("""INSERT INTO procurement_purchases(product,supplier,quantity,unit,currency,unit_price,
                     exchange_rate,transport_cost,purchase_date,total_cost,total_cost_usd,notes,created_at)
                     VALUES('gasoil','Legacy supplier',1000,'gallon','EUR',2,1.1,100,'2026-10-13',2100,2310,'Original notes','2026-10-13T12:00:00+00:00')""")
    upgraded = Database(path)
    old = upgraded.procurements()[0]
    assert old["total_cost"] == 2100 and old["total_cost_usd"] == 2310
    assert old["notes"] == "Original notes"
    assert old["quote_id"] is None and old["quote_snapshot"] is None
    assert old["insurance_cost"] == old["heating_cost"] == 0
    assert old["other_costs"] == []
    upgraded.initialize()
    assert upgraded.procurements()[0]["total_cost"] == 2100
