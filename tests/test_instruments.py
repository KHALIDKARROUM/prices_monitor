from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from price_monitor import database as database_module
from price_monitor.database import SCHEMA, SQLiteDatabase, run_sqlite_migrations
from price_monitor.services.alerts import AlertManager
from price_monitor.services.forecasting import build_forecast, forecast_product


SOURCES = {
    "brent_spot": "EIA/FRED - DCOILBRENTEU",
    "brent_futures": "Yahoo Finance - Brent futures BZ=F",
    "diesel_spot": "EIA/FRED - DDFUELNYH",
    "heating_oil_futures": "Yahoo Finance - NY Harbor Heating Oil HO=F (proxy gasoil)",
}


def quote(instrument, price, day, second=0, source=None):
    return {
        "product": "brent" if instrument.startswith("brent") else "gasoil",
        "source": source or SOURCES[instrument], "price": price,
        "source_date": day.date().isoformat(),
        "collected_at": (day + timedelta(seconds=second)).isoformat(),
    }


@pytest.mark.parametrize("spot, futures", [("brent_spot", "brent_futures"), ("diesel_spot", "heating_oil_futures")])
def test_price_changes_compare_the_same_instrument_and_provider(empty_database, spot, futures):
    day = datetime.now(timezone.utc).replace(microsecond=0)
    first = empty_database.insert_observation(quote(spot, 80, day))
    other = empty_database.insert_observation(quote(futures, 90, day, 1))
    same = empty_database.insert_observation(quote(spot, 80, day, 2, source="Equivalent provider"))
    moved = empty_database.insert_observation(quote(futures, 99, day, 3))
    assert first["instrument"] == spot
    assert other["variation_pct"] is None
    assert same["variation_pct"] is None
    assert moved["variation_pct"] == 10
    assert moved["variation"] == 9


def test_same_source_can_store_two_explicit_instruments(empty_database):
    day = datetime.now(timezone.utc)
    base = quote("brent_spot", 80, day, source="Manual benchmark")
    a = empty_database.insert_observation(dict(base, instrument="brent_spot"))
    b = empty_database.insert_observation(dict(base, instrument="brent_futures"))
    assert a["id"] != b["id"]
    assert len(empty_database.history(instrument="brent_futures")["items"]) == 1
    with pytest.raises(ValueError, match="instrument"):
        empty_database.insert_observation(dict(base, source=SOURCES["brent_spot"], instrument="brent_futures"))


def test_dashboard_and_archives_keep_selected_histories_separate(empty_database):
    now = datetime.now(timezone.utc)
    for offset in (2, 1):
        day = now - timedelta(days=offset)
        empty_database.insert_observation(quote("brent_spot", 80, day))
        empty_database.insert_observation(quote("brent_futures", 90, day, 1))
        empty_database.insert_observation(quote("brent_spot", 800, day, 2, source="U.S. EIA - PET.RBRTE.D"))
    auto = empty_database.dashboard()
    spot = empty_database.dashboard(instruments={"brent": "brent_spot"})
    assert auto["metrics"]["brent"]["instrument"] == "brent_futures"
    assert auto["metrics"]["brent"]["average"] == 90
    assert spot["metrics"]["brent"]["average"] == 80
    assert spot["metrics"]["brent"]["count"] == 2
    assert {r["instrument"] for r in spot["series"]["brent"]} == {"brent_spot"}
    assert {r["price"] for r in spot["series"]["brent"]} == {80}
    assert empty_database.latest()[0]["instrument"] == "brent_spot"
    assert {r["instrument"] for r in empty_database.history(instrument="brent_futures")["items"]} == {"brent_futures"}
    with pytest.raises(ValueError, match="Instrument"):
        empty_database.dashboard(instruments={"brent": "diesel_spot"})


def test_period_comparison_does_not_average_spot_and_futures(empty_database):
    day = datetime.now(timezone.utc) - timedelta(days=2)
    for i in (0, 1):
        empty_database.insert_observation(quote("brent_spot", 80 + i, day + timedelta(days=i)))
        empty_database.insert_observation(quote("brent_futures", 90 + 2 * i, day + timedelta(days=i), 1))
    a, b = day.date().isoformat(), (day + timedelta(days=1)).date().isoformat()
    result = empty_database.compare_periods(a, a, b, b)["products"]
    assert result["brent"]["period_a"]["average"] == 80
    assert result["brent"]["average_change"] == 1
    assert result["brent_futures"]["period_a"]["average"] == 90
    assert result["brent_futures"]["average_change"] == 2


@pytest.mark.parametrize("spot, futures", [("brent_spot", "brent_futures"), ("diesel_spot", "heating_oil_futures")])
def test_unchanged_feeds_never_retrigger_each_others_alerts(empty_database, spot, futures):
    now = datetime.now(timezone.utc)
    product = "brent" if spot.startswith("brent") else "gasoil"
    for instrument in (spot, futures):
        empty_database.create_alert_rule({"product": product, "instrument": instrument, "direction": "above", "threshold": 85})
    manager = AlertManager(empty_database)
    rows = [quote(spot, 80, now), quote(futures, 90, now, 1)]
    first = manager.evaluate_prices(rows, dispatch=False)
    assert [(event.instrument, event.status) for event in first] == [(futures, "triggered")]
    assert manager.evaluate_prices(list(reversed(rows)), dispatch=False) == []
    rules = {rule["instrument"]: rule for rule in empty_database.alert_rules()}
    assert rules[spot]["active"] is False
    assert rules[futures]["active"] is True
    assert rules[spot]["alert_key"] != rules[futures]["alert_key"]
    rows[1]["price"] = 84
    assert [(event.instrument, event.status) for event in manager.evaluate_prices(rows, dispatch=False)] == [(futures, "recovered")]
    assert manager.evaluate_prices(rows, dispatch=False) == []


def test_equivalent_api_disagreement_is_evaluated_once_with_stable_priority(empty_database):
    day = datetime.now(timezone.utc)
    manager = AlertManager(empty_database, thresholds={"brent": {"above": 85}})
    rows = [quote("brent_spot", 80, day), quote("brent_spot", 90, day, 1, source="U.S. EIA - PET.RBRTE.D")]
    assert manager.evaluate_prices(rows, dispatch=False) == []
    assert manager.evaluate_prices(list(reversed(rows)), dispatch=False) == []
    rows[0]["price"] = 86
    assert [e.status for e in manager.evaluate_prices(rows, dispatch=False)] == ["triggered"]
    assert manager.evaluate_prices(list(reversed(rows)), dispatch=False) == []


def test_forecast_uses_only_spot_history():
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(40):
        day = now - timedelta(days=40 - i)
        rows += [quote("brent_spot", 80, day), quote("brent_futures", 900, day, 1)]
    result = forecast_product(rows, "brent")
    assert result["instrument"] == "brent_spot"
    assert result["history_count"] == 40
    assert result["last_actual"]["value"] == 80
    assert result["forecasts"]["7"][-1]["value"] == 80


def test_futures_records_cannot_crowd_spot_out_of_forecast_input(empty_database):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=100)
    conn = empty_database.connect()
    try:
        conn.executemany(
            """INSERT INTO price_observations(product,instrument,price,unit,source,source_date,collected_at)
            VALUES('brent','brent_futures',?,'USD/baril',?,?,?)""",
            [(900 + i / 1000, SOURCES["brent_futures"], old.date().isoformat(), (old + timedelta(seconds=i)).isoformat()) for i in range(5001)],
        )
        conn.commit()
    finally:
        conn.close()
    for i in range(40):
        empty_database.insert_observation(quote("brent_spot", 80, now - timedelta(days=40 - i)))
    result = build_forecast(empty_database)["products"]["brent"]
    assert result["status"] == "ready"
    assert result["history_count"] == 40
    assert result["last_actual"]["value"] == 80


def test_procurement_compares_against_spot_not_the_heating_oil_proxy(empty_database):
    now = datetime.now(timezone.utc)
    empty_database.insert_observation(quote("diesel_spot", 2, now))
    empty_database.insert_observation(quote("heating_oil_futures", 3, now, 1))
    purchase = empty_database.create_procurement({"product": "gasoil", "supplier": "Example", "quantity": 10, "unit_price": 2.5, "currency": "USD"})
    assert purchase["market_price_usd"] == 2
    assert purchase["price_impact_pct"] == 25
    from price_monitor.services.procurement import build_procurement_guide
    assert build_procurement_guide(empty_database)["market_context"]["latest_price"]["instrument"] == "diesel_spot"


def test_http_instrument_selection_filters_charts_history_and_exports(empty_database, monkeypatch):
    import csv
    import http.client
    import io
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from price_monitor import app

    now = datetime.now(timezone.utc)
    empty_database.insert_observation(quote("brent_spot", 80, now))
    empty_database.insert_observation(quote("brent_futures", 90, now, 1))
    monkeypatch.setattr(app, "database", empty_database)
    monkeypatch.setattr(app, "RATE_LIMIT_ENABLED", False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def get(path):
        client = http.client.HTTPConnection(*server.server_address)
        try:
            client.request("GET", path)
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()
    try:
        status, body = get("/api/dashboard?brent_instrument=brent_spot")
        assert status == 200
        assert json.loads(body)["metrics"]["brent"]["current"] == 80
        status, body = get("/api/history?instrument=brent_futures")
        assert status == 200
        assert [row["instrument"] for row in json.loads(body)["items"]] == ["brent_futures"]
        status, body = get("/export.csv?instrument=brent_futures")
        assert status == 200
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
        assert [row["instrument"] for row in rows] == ["brent_futures"]
        assert get("/api/dashboard?brent_instrument=heating_oil_futures")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_migration_preserves_legacy_records_and_corrects_mixed_changes(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for migration in database_module.SQLITE_MIGRATIONS_DIR.glob("*.sql"):
        if migration.name < "010":
            (migrations / migration.name).write_text(migration.read_text(encoding="utf-8"), encoding="utf-8")
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        run_sqlite_migrations(conn, migrations)
        for price, source, time in [(80, SOURCES["brent_spot"], "10:00:00"), (90, SOURCES["brent_futures"], "10:00:01"), (81, SOURCES["brent_spot"], "10:00:02")]:
            conn.execute("INSERT INTO price_observations(product,price,unit,source,source_date,collected_at,variation_pct) VALUES('brent',?,'USD/baril',?,'2026-10-12',?,12.5)", (price, source, f"2026-10-12T{time}+00:00"))
        conn.execute("INSERT INTO alert_rules(product,direction,threshold,created_at,updated_at) VALUES('brent','above',85,'2026-10-12','2026-10-12')")
        conn.execute("INSERT INTO alert_states(alert_key,active,updated_at) VALUES('price:brent:above:85',1,'2026-10-12')")
    monkeypatch.setattr(database_module, "REAL_DATA_PATH", tmp_path / "missing.csv")
    db = SQLiteDatabase(path)
    rows = db.history(product="brent", page_size=100)["items"]
    assert len(rows) == 3
    assert {r["id"] for r in rows} == {1, 2, 3}
    assert {r["instrument"] for r in rows} == {"brent_spot", "brent_futures"}
    assert next(r for r in rows if r["price"] == 81)["variation_pct"] == 1.25
    assert next(r for r in rows if r["price"] == 90)["variation_pct"] is None
    assert db.alert_rules()[0]["instrument"] == "brent_spot"
    assert db.alert_rules()[0]["id"] == 1
    assert db.get_alert_state("price:brent:above:85") is None
    db.initialize()
    assert db.history(product="brent")["total"] == 3
