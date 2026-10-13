from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

PRODUCTS = ("brent", "gasoil")
UNITS = {"brent": "USD/barrel", "gasoil": "USD/gallon"}


def load_bitumen_data(path: Path) -> pd.Series:
    """Validate monthly index observations without filling daily price rows."""
    raw = pd.read_csv(path, na_values=["."])
    if not {"date", "bitume", "series", "unit"}.issubset(raw.columns):
        raise ValueError("Required columns: date, bitume, series, unit")
    dates = pd.to_datetime(raw["date"], format="%Y-%m-%d", errors="raise")
    values = pd.to_numeric(raw["bitume"], errors="raise")
    if dates.isna().any() or dates.duplicated().any() or not dates.dt.is_month_start.all():
        raise ValueError("Missing, duplicate or non-monthly Bitume dates")
    if values.isna().any() or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Bitume indices must be positive and finite")
    if not raw["series"].eq("WPU058").all() or not raw["unit"].eq("Index Dec 1984=100").all():
        raise ValueError("Bitume data must retain the BLS WPU058 index and its published unit")
    series = pd.Series(values.to_numpy(), index=pd.DatetimeIndex(dates, name="date"), name="bitume").sort_index()
    if series.empty or series.index[-1].date() > pd.Timestamp.now(tz="UTC").date():
        raise ValueError("Empty or future Bitume index history")
    return series


def load_market_data(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, na_values=["."])
    if not {"date", *PRODUCTS}.issubset(raw.columns):
        raise ValueError("Required columns: date, brent, gasoil")
    dates = pd.to_datetime(raw["date"], format="%Y-%m-%d", errors="raise")
    if dates.isna().any() or dates.duplicated().any():
        raise ValueError("Missing or duplicate source dates")
    frame = raw.loc[:, list(PRODUCTS)].apply(pd.to_numeric, errors="raise")
    if ((frame <= 0) | (frame.notna() & ~np.isfinite(frame))).any().any():
        raise ValueError("Observed prices must be positive and finite")
    frame.index = pd.DatetimeIndex(dates, name="date")
    frame = frame.sort_index()
    if frame.empty or frame.notna().sum().min() < 2:
        raise ValueError("At least two observed prices per product are required")
    if frame.index.max().date() > pd.Timestamp.now(tz="UTC").date():
        raise ValueError("Snapshot contains future source dates")
    return frame


def audit_data(frame: pd.DataFrame, path: Path) -> dict:
    """Check the processed file against bundled raw CSVs, not against a live API."""
    audit = {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": len(frame), "start": str(frame.index.min().date()),
        "end": str(frame.index.max().date()), "products": {},
        "provenance_scope": "Local CSV consistency only; no proof of provider authenticity or publication-time availability.",
    }
    raw_dir = path.parent.parent / "raw"
    for product, code in [("brent", "DCOILBRENTEU"), ("gasoil", "DDFUELNYH")]:
        series = frame[product].dropna()
        info = {
            "unit": UNITS[product], "observations": len(series),
            "missing_in_union_index": int(frame[product].isna().sum()),
            "missing_weekdays_including_holidays": len(pd.bdate_range(frame.index.min(), frame.index.max()).difference(series.index)),
            "longest_gap_calendar_days": int(series.index.to_series().diff().dt.days.max()),
            "latest_source_date": str(series.index[-1].date()),
            "age_calendar_days": (pd.Timestamp.now(tz="UTC").date() - series.index[-1].date()).days,
        }
        raw_path = raw_dir / f"FRED_{code}.csv"
        if raw_path.exists():
            raw = pd.read_csv(raw_path, na_values=["."], index_col=0, parse_dates=True)[code]
            raw = pd.to_numeric(raw, errors="raise").reindex(frame.index)
            same = np.isclose(frame[product], raw, equal_nan=True, rtol=0, atol=1e-9)
            info.update(raw_sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                        raw_mismatches=int((~same).sum()))
        audit["products"][product] = info
    return audit
