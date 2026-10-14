from __future__ import annotations

from datetime import date, timedelta


def _candidates():
    return [
        {"model": "naive", "model_version": "test-implementation-v1", "validation_mae": 2, "validation_samples": 40},
        {"model": "holt", "model_version": "test-implementation-v1", "validation_mae": 1.8, "validation_samples": 40},
    ]


def _issue_pair(database, plan, *, offset: int, production_prediction: float, shadow_prediction: float, baseline: float) -> None:
    origin = date(2026, 1, 1) + timedelta(days=offset)
    target = origin + timedelta(days=7)
    for candidate, prediction in ((plan["production"], production_prediction), (plan["shadows"][0], shadow_prediction)):
        database.record_live_forecast({
            "product": "brent", "instrument": "brent_spot", "horizon": 7, "horizon_unit": "calendar_days",
            "model": candidate["model"], "model_version": candidate["forecast_model_version"],
            "origin_date": origin.isoformat(), "target_date": target.isoformat(),
            "issued_at": f"{origin.isoformat()}T12:00:00+00:00", "prediction": prediction,
            "baseline_prediction": baseline, "lower_bound": prediction - 1, "upper_bound": prediction + 1,
        })
    database.insert_observation({
        "product": "brent", "instrument": "brent_spot", "price": 100,
        "unit": "USD/baril", "currency": "USD", "source": "EIA/FRED - DCOILBRENTEU",
        "source_date": target.isoformat(), "collected_at": f"{target.isoformat()}T12:00:00+00:00",
    })


def test_shadow_model_is_promoted_only_after_paired_live_improvement(empty_database):
    plan = empty_database.prepare_model_lifecycle(
        product="brent", instrument="brent_spot", horizon=7, horizon_unit="calendar_days",
        candidates=_candidates(), preferred_model="naive",
    )
    assert plan["production"]["model"] == "naive"
    assert [candidate["model"] for candidate in plan["shadows"]] == ["holt"]
    for offset in range(30):
        _issue_pair(empty_database, plan, offset=offset, production_prediction=110, shadow_prediction=100, baseline=110)

    result = empty_database.evaluate_model_lifecycle()

    assert result["promoted"] == 1 and result["rolled_back"] == 0
    lifecycle = empty_database.model_lifecycle(product="brent", instrument="brent_spot", horizon=7)
    production = next(candidate for candidate in lifecycle["candidates"] if candidate["status"] == "production")
    assert production["model"] == "holt"
    promotion = next(event for event in lifecycle["events"] if event["event_type"] == "promoted")
    assert promotion["samples"] == 30
    assert promotion["improvement"] == 1


def test_underperforming_nonbaseline_production_rolls_back_to_persistence(empty_database):
    plan = empty_database.prepare_model_lifecycle(
        product="brent", instrument="brent_spot", horizon=7, horizon_unit="calendar_days",
        candidates=_candidates(), preferred_model="holt",
    )
    assert plan["production"]["model"] == "holt"
    for offset in range(30):
        _issue_pair(empty_database, plan, offset=offset, production_prediction=110, shadow_prediction=100, baseline=100)

    result = empty_database.evaluate_model_lifecycle()

    assert result["rolled_back"] == 1 and result["promoted"] == 0
    lifecycle = empty_database.model_lifecycle(product="brent", instrument="brent_spot", horizon=7)
    production = next(candidate for candidate in lifecycle["candidates"] if candidate["status"] == "production")
    retired = next(candidate for candidate in lifecycle["candidates"] if candidate["model"] == "holt")
    assert production["model"] == "naive"
    assert retired["status"] == "rolled_back" and retired["rolled_back_at"]
    rollback = next(event for event in lifecycle["events"] if event["event_type"] == "rolled_back")
    assert rollback["samples"] == 30
