"""Leakage, calendar alignment and reproducibility contracts for offline research."""
from __future__ import annotations

import pytest
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")

from price_monitor.analysis.data import load_market_data
from price_monitor.analysis.features import make_features, make_supervised, purged_splits
from price_monitor.analysis import modeling


@pytest.fixture
def market():
    dates = pd.bdate_range("2021-01-01", periods=800)
    t = np.arange(len(dates))
    rng = np.random.default_rng(42)
    return pd.DataFrame({"brent": 80 + .01*t + np.sin(t/8) + rng.normal(0,.1,len(t)),
                         "gasoil": 2 + .001*t + .03*np.cos(t/9)}, index=dates)


def test_features_do_not_change_when_future_prices_change(market):
    cutoff = market.index[500]
    expected = make_features(market, "brent").loc[:cutoff]
    altered = market.copy()
    altered.loc[altered.index > cutoff] *= 100
    pd.testing.assert_frame_equal(expected, make_features(altered, "brent").loc[:cutoff])


def test_peer_feature_excludes_same_day_and_marks_stale(market):
    day = market.index[200]
    original = make_features(market, "brent").loc[day]
    changed = market.copy()
    changed.loc[day, "gasoil"] = 900
    pd.testing.assert_series_equal(original, make_features(changed, "brent").loc[day])
    changed.loc[(changed.index > day-pd.Timedelta(days=15)) & (changed.index < day), "gasoil"] = np.nan
    assert pd.isna(make_features(changed, "brent").loc[day, "peer_log_price"])


def test_target_is_real_future_publication_and_not_imputed(market):
    market.loc[market.index[100:104], "brent"] = np.nan
    x, labels = make_supervised(market, "brent", 7)
    assert x.index.equals(labels.index)
    assert (labels.target_date >= labels.index + pd.Timedelta(days=7)).all()
    assert (labels.target_date <= labels.index + pd.Timedelta(days=11)).all()
    np.testing.assert_array_equal(labels.target_price, market.loc[labels.target_date, "brent"])
    assert not set(["target_date", "target_price", "target_log_return"]) & set(x.columns)
    assert labels.index[-1] < market.index[-1]


def test_long_horizon_labels_are_purged_from_validation(market):
    _, labels = make_supervised(market, "gasoil", 90)
    for train, valid in purged_splits(labels):
        assert labels.iloc[train].target_date.max() < labels.index[valid[0]]
        assert max(train) < min(valid)


@pytest.mark.parametrize("bad", ["0", "-1", "inf", "not-a-price"])
def test_bad_prices_fail_instead_of_silently_training(tmp_path, bad):
    path = tmp_path / "bad.csv"
    path.write_text(f"date,brent,gasoil\n2025-01-01,80,2\n2025-01-02,{bad},3\n")
    with pytest.raises(ValueError):
        load_market_data(path)


def test_duplicate_dates_fail(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("date,brent,gasoil\n2025-01-01,80,2\n2025-01-01,81,3\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_market_data(path)


def test_holdout_cannot_change_model_selection_or_scaler(market, monkeypatch):
    candidates = modeling.candidates()
    monkeypatch.setattr(modeling, "candidates", lambda: {"naive": None, "ridge_1": candidates["ridge_1"]})
    before = modeling.run_experiment(market, "brent", 7)
    altered = market.copy()
    altered.loc[altered.index >= pd.Timestamp(before["test_start"]), "brent"] *= 2
    after = modeling.run_experiment(altered, "brent", 7)
    assert before["selected"] == after["selected"]
    pd.testing.assert_frame_equal(before["cv"], after["cv"])
    for result in (before, after):
        labels = result["labels"]
        assert labels.loc[result["development"]].target_date.max() < result["calibration"].min()
        assert labels.loc[result["calibration"]].target_date.max() < result["holdout"].min()
    np.testing.assert_array_equal(before["models"]["ridge_1"][1].mean_, after["models"]["ridge_1"][1].mean_)


def test_calibration_quantile_requires_enough_data():
    with pytest.raises(ValueError):
        modeling.interval_radius([.1, .2])
    assert modeling.interval_radius(np.arange(100)/100) == pytest.approx(.95)


def test_error_metrics_reward_only_real_improvements():
    perfect = modeling.metrics([10, 12], [10, 12], [9, 10])
    assert perfect["mae"] == 0 and perfect["mae_skill_vs_naive"] == 1
    naive = modeling.metrics([10, 12], [9, 10], [9, 10])
    assert naive["mae_skill_vs_naive"] == 0
