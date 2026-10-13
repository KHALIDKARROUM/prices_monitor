from datetime import datetime, timedelta, timezone

import pytest

from price_monitor import config
from price_monitor.services.comparison import SOURCE_DEFINITIONS, compare_api_prices
from price_monitor.services import scheduler as scheduler_module
from price_monitor.services.collectors import CollectionBatch
from price_monitor.services.connectivity import WebsiteStatus


@pytest.fixture(autouse=True)
def configured_apis(monkeypatch):
    monkeypatch.setattr(config, "EIA_API_KEY", "test-key")
    monkeypatch.setattr(config, "ALPHA_VANTAGE_API_KEY", "test-key")
    monkeypatch.setattr(config, "LIVE_QUOTES_ENABLED", True)


def quote(code, price, source_date="2026-10-05", **extra):
    definition = next(row for row in SOURCE_DEFINITIONS if row[0] == code)
    return {
        "product": definition[1], "source": definition[3], "price": price,
        "unit": definition[6], "currency": "USD", "source_date": source_date,
        "collected_at": f"{source_date}T12:00:00+00:00", **extra,
    }


def pair(data, left, right):
    return next(row for row in data["pairs"] if row["left"] == left and row["right"] == right)


@pytest.mark.parametrize("code,other,price,tolerance", [
    ("fred_brent", "eia_brent", 100.0, 0.005),
    ("fred_diesel", "eia_diesel", 2.5, 0.0005),
])
def test_equivalent_feeds_flag_only_differences_beyond_rounding(code, other, price, tolerance):
    for delta, verdict in ((0, "match"), (tolerance, "match"), (tolerance * 2, "mismatch")):
        result = compare_api_prices([quote(code, price), quote(other, price + delta)])
        comparison = pair(result, code, other)
        assert comparison["status"] == verdict
        assert comparison["difference"] == pytest.approx(delta)
        assert comparison["difference_pct"] == pytest.approx(delta / price * 100, abs=0.00005)
        assert result["mismatch_count"] == (1 if verdict == "mismatch" else 0)
        assert result["comparable_pair_count"] == 1


def test_three_brent_mirrors_compare_pairwise_without_assuming_one_is_correct():
    result = compare_api_prices([
        quote("fred_brent", 100), quote("eia_brent", 100), quote("alpha_brent", 110),
    ])
    assert result["status"] == "warning"
    assert result["comparable_pair_count"] == 3
    assert result["mismatch_count"] == 2
    assert "un problème est possible" in result["message"]
    assert len(result["warnings"]) == 2
    assert "FRED = 100.0000" in result["warnings"][0]
    assert "Alpha Vantage = 110.0000" in result["warnings"][0]
    assert "2026-10-05" in result["warnings"][0]
    assert "10.000000 USD/baril (10.0000 %)" in result["warnings"][0]
    assert pair(result, "fred_brent", "eia_brent")["status"] == "match"


@pytest.mark.parametrize("left,right", [
    ("fred_brent", "live_brent"), ("fred_diesel", "live_gasoil_proxy"),
])
def test_spot_and_futures_or_proxy_differences_are_context_not_errors(left, right):
    result = compare_api_prices([quote(left, 100), quote(right, 150)])
    comparison = pair(result, left, right)
    assert comparison["status"] == "not_comparable"
    assert comparison["difference_pct"] == 50
    assert result["mismatch_count"] == 0
    assert result["status"] == "unverified"


def test_publication_lags_cannot_be_flagged_as_a_same_day_price_mismatch():
    result = compare_api_prices([quote("fred_brent", 100), quote("eia_brent", 110, "2026-10-02")])
    comparison = pair(result, "fred_brent", "eia_brent")
    assert comparison["status"] == "not_comparable"
    assert "Dates" in comparison["reason"]
    assert result["mismatch_count"] == 0


@pytest.mark.parametrize("extra", [{"unit": "USD/litre"}, {"currency": "EUR"}])
def test_units_or_currencies_are_never_silently_compared(extra):
    result = compare_api_prices([quote("fred_brent", 100), quote("eia_brent", 100, **extra)])
    comparison = pair(result, "fred_brent", "eia_brent")
    assert comparison["status"] == "not_comparable"
    assert comparison["difference"] is None
    assert comparison["difference_pct"] is None


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), "invalid"])
def test_missing_or_invalid_values_are_unverified_even_with_one_healthy_api(value):
    result = compare_api_prices([quote("fred_brent", 100), quote("eia_brent", value)])
    assert pair(result, "fred_brent", "eia_brent")["status"] == "unverified"
    assert result["status"] == "unverified"
    assert result["comparable_pair_count"] == 0


def test_saved_price_from_a_failed_or_disabled_api_cannot_confirm_agreement(monkeypatch):
    observations = [quote("fred_brent", 100), quote("eia_brent", 100)]
    result = compare_api_prices(observations, source_health=[{"code": "eia_brent", "last_error": "private details"}])
    assert pair(result, "fred_brent", "eia_brent")["status"] == "unverified"
    assert "private details" not in str(result)
    monkeypatch.setattr(config, "EIA_API_KEY", "")
    result = compare_api_prices(observations)
    assert pair(result, "fred_brent", "eia_brent")["status"] == "unverified"
    source = next(row for row in result["sources"] if row["code"] == "eia_brent")
    assert source["availability"] == "not_configured"
    assert source["price"] == 100


def test_bls_index_is_displayed_without_comparing_it_to_a_bitumen_price():
    result = compare_api_prices(
        [{"product": "bitume", "source": "Supplier quote", "price": 500}],
        [{"source": SOURCE_DEFINITIONS[-1][3], "value": 546.938, "source_date": "2026-08-01", "unit": SOURCE_DEFINITIONS[-1][6]}],
    )
    index = next(row for row in result["sources"] if row["code"] == "bls_asphalt_ppi")
    assert index["kind"] == "index"
    assert index["price"] == 546.938
    assert not [row for row in result["pairs"] if row["product"] == "bitume"]


def test_comparison_uses_latest_publication_per_api_independently_of_chart_window(empty_database):
    now = datetime.now(timezone.utc)
    publication = (now - timedelta(days=60)).date().isoformat()
    older = (now - timedelta(days=61)).date().isoformat()
    for row in (
        quote("fred_brent", 100, publication, collected_at=(now - timedelta(minutes=2)).isoformat()),
        quote("eia_brent", 100, publication, collected_at=(now - timedelta(minutes=1)).isoformat()),
        quote("fred_brent", 90, older, collected_at=now.isoformat()),
    ):
        empty_database.insert_observation(row)
    dashboard = empty_database.dashboard(days=7)
    assert dashboard["series"]["brent"] == []
    comparison = dashboard["source_comparison"]
    assert pair(comparison, "fred_brent", "eia_brent")["status"] == "match"
    assert next(row for row in comparison["sources"] if row["code"] == "fred_brent")["price"] == 100


def test_same_day_latest_quote_uses_collection_order_not_highest_price(empty_database):
    for price, minute in ((110, 1), (100, 2)):
        empty_database.insert_observation(quote("fred_brent", price, collected_at=f"2026-10-05T12:0{minute}:00+00:00"))
    empty_database.insert_observation(quote("eia_brent", 100))
    assert pair(empty_database.source_comparison(), "fred_brent", "eia_brent")["status"] == "match"


def test_api_revision_back_to_a_previous_price_clears_a_saved_mismatch(empty_database):
    first = empty_database.insert_observation(quote("fred_brent", 100, collected_at="2026-10-05T12:01:00+00:00"))
    empty_database.insert_observation(quote("eia_brent", 100))
    empty_database.insert_observation(quote("fred_brent", 101, collected_at="2026-10-05T12:02:00+00:00"))
    assert empty_database.source_comparison()["mismatch_count"] == 1
    reverted = empty_database.insert_observation(quote("fred_brent", 100, collected_at="2026-10-05T12:03:00+00:00"))
    assert reverted["id"] == first["id"]
    assert reverted["collected_at"] == "2026-10-05T12:03:00+00:00"
    assert len(empty_database.observations(days=3650)) == 3
    assert empty_database.source_comparison()["mismatch_count"] == 0
    # Replaying an older response must not restore the wrong quote ordering.
    empty_database.insert_observation(quote("fred_brent", 101, collected_at="2026-10-05T12:01:00+00:00"))
    assert empty_database.source_comparison()["mismatch_count"] == 0


def collection_batch(observations, failed_code=None):
    codes = {row[3]: row[0] for row in SOURCE_DEFINITIONS}
    outcomes = [{"code": codes[observation["source"]], "success": True} for observation in observations]
    if failed_code:
        outcomes.append({"code": failed_code, "success": False, "error": "API unavailable"})
    return CollectionBatch(observations, ["API unavailable"] if failed_code else [], outcomes)


def test_every_collection_warns_logs_details_and_clears_when_prices_agree(empty_database, monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "website_status", WebsiteStatus())
    monkeypatch.setattr(scheduler_module, "_evaluate_collection_alerts", lambda *args: None)
    monkeypatch.setattr(scheduler_module, "collect_all", lambda: collection_batch([quote("fred_brent", 100), quote("eia_brent", 101)]))

    with caplog.at_level("WARNING"):
        for _ in range(3):
            result = scheduler_module.run_collection(empty_database)
            assert result["status"] == "success"
            assert result["rows"] == 2
            assert result["source_comparison"]["status"] == "warning"
            assert result["source_comparison"]["mismatch_count"] == 1
            assert "un problème est possible" in result["message"]
            assert empty_database.logs(limit=1)[0]["message"] == result["message"]

    assert len([record for record in caplog.records if getattr(record, "event", None) == "api_price_mismatch"]) == 3
    assert empty_database.dashboard()["source_comparison"]["mismatch_count"] == 1

    monkeypatch.setattr(scheduler_module, "collect_all", lambda: collection_batch([quote("fred_brent", 101), quote("eia_brent", 101)]))
    result = scheduler_module.run_collection(empty_database)
    assert result["source_comparison"]["status"] == "consistent"
    assert result["source_comparison"]["warnings"] == []
    assert "un problème est possible" not in result["message"]
    assert empty_database.source_comparison()["mismatch_count"] == 0


def test_partial_run_compares_successful_apis_and_warns_alongside_errors(empty_database, monkeypatch):
    monkeypatch.setattr(scheduler_module, "_evaluate_collection_alerts", lambda *args: None)
    monkeypatch.setattr(scheduler_module, "collect_all", lambda: collection_batch(
        [quote("fred_brent", 100), quote("alpha_brent", 101)], failed_code="eia_brent",
    ))
    result = scheduler_module.run_collection(empty_database)
    assert result["status"] == "partial"
    assert result["source_comparison"]["mismatch_count"] == 1
    assert result["message"].startswith("API unavailable")
    assert "un problème est possible" in result["message"]


def test_current_collection_does_not_compare_with_a_saved_missing_api_quote(empty_database, monkeypatch):
    empty_database.insert_observation(quote("eia_brent", 110))
    monkeypatch.setattr(scheduler_module, "_evaluate_collection_alerts", lambda *args: None)
    monkeypatch.setattr(scheduler_module, "collect_all", lambda: collection_batch([quote("fred_brent", 100)], failed_code="eia_brent"))
    result = scheduler_module.run_collection(empty_database)
    assert result["source_comparison"]["status"] == "unverified"
    assert result["source_comparison"]["warnings"] == []
    assert pair(empty_database.source_comparison(), "fred_brent", "eia_brent")["status"] == "unverified"


@pytest.mark.parametrize("left,right", [
    (quote("fred_brent", 100), quote("eia_brent", 100.005)),
    (quote("fred_brent", 100), quote("eia_brent", 101, "2026-10-02")),
    (quote("fred_brent", 100), quote("live_brent", 110)),
])
def test_collection_does_not_warn_on_rounding_publication_lags_or_futures(empty_database, monkeypatch, left, right):
    monkeypatch.setattr(scheduler_module, "_evaluate_collection_alerts", lambda *args: None)
    monkeypatch.setattr(scheduler_module, "collect_all", lambda: collection_batch([left, right]))
    result = scheduler_module.run_collection(empty_database)
    assert result["source_comparison"]["warnings"] == []
    assert "un problème est possible" not in result["message"]
