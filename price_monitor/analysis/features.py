"""Causal features at the close of an observed source date.

Own prices at t are assumed known at forecast origin t. Other-product features
use strictly earlier source dates, with a seven-day freshness limit. Historical
release timestamps/vintages are not available: this is a source-date experiment,
not a point-in-time trading simulation. No target is imputed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from ..forecast_evaluation import evaluation_plan, expanding_folds


def make_features(frame: pd.DataFrame, product: str) -> pd.DataFrame:
    price = frame[product].dropna()
    log_price = np.log(price)
    returns = log_price.diff()
    x = pd.DataFrame(index=price.index)
    x["log_price"] = log_price
    for lag in (1, 2, 5, 10, 20, 60):
        x[f"log_return_{lag}"] = log_price.diff(lag)
    for window in (5, 20, 60):
        x[f"price_to_mean_{window}"] = price / price.rolling(window).mean() - 1
        x[f"volatility_{window}"] = returns.rolling(window).std()
        x[f"momentum_{window}"] = returns.rolling(window).mean()
    x["gap_days"] = price.index.to_series().diff().dt.days
    for name, values, period in [
        ("weekday", price.index.dayofweek, 7),
        ("month", price.index.month - 1, 12),
        ("day_of_year", price.index.dayofyear - 1, 365.25),
    ]:
        x[f"{name}_sin"] = np.sin(2 * np.pi * values / period)
        x[f"{name}_cos"] = np.cos(2 * np.pi * values / period)
    peer = frame["gasoil" if product == "brent" else "brent"].dropna()
    peer_features = pd.DataFrame({
        "peer_log_price": np.log(peer),
        "peer_log_return_1": np.log(peer).diff(),
        "peer_log_return_5": np.log(peer).diff(5),
        "peer_date": peer.index,
    }, index=peer.index)
    joined = pd.merge_asof(x, peer_features, left_index=True, right_index=True,
                           allow_exact_matches=False, tolerance=pd.Timedelta(days=7))
    joined["peer_age_days"] = (joined.index.to_series() - joined.pop("peer_date")).dt.days
    joined["relative_momentum_5"] = joined["log_return_5"] - joined["peer_log_return_5"]
    # Require a genuine 60-observation own-price warm-up; peer gaps are imputed
    # inside each fitted training pipeline, never from the full dataset.
    return joined.loc[x["log_return_60"].notna()].replace([np.inf, -np.inf], np.nan)


def make_supervised(frame: pd.DataFrame, product: str, horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Predict the first actual publication on/after t+h, at most four days late."""
    if horizon < 1:
        raise ValueError("Horizon must be a positive number of calendar days")
    x = make_features(frame, product)
    price = frame[product].dropna()
    plan = evaluation_plan([day.toordinal() for day in price.index], horizon)
    origins = price.index[[pair["origin"] for pair in plan["pairs"]]]
    targets = price.index[[pair["target"] for pair in plan["pairs"]]]
    labels = pd.DataFrame(index=origins)
    labels["target_date"] = targets
    labels["current_price"] = price.loc[origins].to_numpy()
    labels["target_price"] = price.loc[targets].to_numpy()
    labels["target_log_return"] = np.log(labels.target_price / labels.current_price)
    labels["target_delay_days"] = (targets - origins).days - horizon
    return x.loc[origins], labels


def purged_splits(labels: pd.DataFrame, n_splits: int = 3):
    """Expanding folds with all train labels strictly before validation origins."""
    if n_splits != 3:
        raise ValueError("The shared production protocol uses three validation folds")
    # Express label dates as positions in a common timeline, as the live service does.
    dates = sorted(set(labels.index) | set(labels.target_date))
    positions = {day: index for index, day in enumerate(dates)}
    pairs = [{"origin": positions[day], "target": positions[target]} for day, target in zip(labels.index, labels.target_date)]
    folds = expanding_folds(pairs, list(range(len(labels))), [day.toordinal() for day in dates])
    if not folds:
        raise ValueError("Insufficient training history after horizon purging")
    for fold in folds:
        yield np.array(fold["train"]), np.array(fold["validation"])
