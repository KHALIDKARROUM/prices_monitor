"""Stable market identities, independent of the API delivering a price."""

from __future__ import annotations

INSTRUMENTS = {
    "brent_spot": {"product": "brent", "label": "Brent Europe · spot", "unit": "USD/baril"},
    "brent_futures": {"product": "brent", "label": "Brent BZ=F · futures", "unit": "USD/baril"},
    "diesel_spot": {"product": "gasoil", "label": "Diesel New York Harbor · spot", "unit": "USD/gallon"},
    "heating_oil_futures": {"product": "gasoil", "label": "Heating oil HO=F · diesel proxy", "unit": "USD/gallon"},
    "bitumen_quote": {"product": "bitume", "label": "Bitumen · supplier quote", "unit": "USD/tonne"},
    "asphalt_index": {"product": "bitume", "label": "Bitumen · BLS WPU058 index", "unit": "indice (déc. 1984 = 100)"},
}
DEFAULT_INSTRUMENTS = {"brent": "brent_spot", "gasoil": "diesel_spot", "bitume": "bitumen_quote"}
LIVE_INSTRUMENTS = {"brent": "brent_futures", "gasoil": "heating_oil_futures"}
SOURCE_INSTRUMENTS = {
    "EIA/FRED - DCOILBRENTEU": "brent_spot",
    "U.S. EIA - PET.RBRTE.D": "brent_spot",
    "Alpha Vantage - BRENT": "brent_spot",
    "Yahoo Finance - Brent futures BZ=F": "brent_futures",
    "EIA/FRED - DDFUELNYH": "diesel_spot",
    "U.S. EIA - PET.DDFUELNYH.D": "diesel_spot",
    "Yahoo Finance - NY Harbor Heating Oil HO=F (proxy gasoil)": "heating_oil_futures",
    "U.S. BLS Public Data API - WPU058": "asphalt_index",
    "BLS/FRED - WPU058": "asphalt_index",
}
# Choose one provider per instrument/date. Receipt order must not decide whether
# disagreeing equivalent APIs trigger and recover the same alert in one batch.
SOURCE_PRIORITY = {
    "EIA/FRED - DCOILBRENTEU": 0, "EIA/FRED - DDFUELNYH": 0,
    "U.S. EIA - PET.RBRTE.D": 1, "U.S. EIA - PET.DDFUELNYH.D": 1,
    "Alpha Vantage - BRENT": 2,
}


def resolve_instrument(product: str, source: str = "", instrument: str | None = None, *, index: bool = False) -> str:
    inferred = "asphalt_index" if index else SOURCE_INSTRUMENTS.get(source, DEFAULT_INSTRUMENTS.get(product))
    selected = instrument or inferred
    if selected not in INSTRUMENTS or INSTRUMENTS[selected]["product"] != product:
        raise ValueError("Instrument is incompatible with the product.")
    if source in SOURCE_INSTRUMENTS and selected != inferred:
        raise ValueError("The instrument must match the series published by the source.")
    if index != (selected == "asphalt_index"):
        raise ValueError("The BLS index must remain separate from prices and quotes.")
    return selected


def row_instrument(row: dict) -> str:
    return resolve_instrument(
        str(row.get("product", "")), str(row.get("source", "")), row.get("instrument"),
        index=row.get("data_kind") == "index",
    )


def canonical_daily_rows(rows: list[dict]) -> list[dict]:
    """One actual observation per instrument/day, with stable provider priority."""
    selected: dict[tuple[str, str], dict] = {}
    for original in rows:
        row = dict(original, instrument=row_instrument(original))
        day = str(row.get("source_date") or row.get("collected_at", ""))[:10]
        key = (row["instrument"], day)
        previous = selected.get(key)
        rank = SOURCE_PRIORITY.get(row.get("source"), 10)
        previous_rank = SOURCE_PRIORITY.get(previous.get("source"), 10) if previous else 100
        if previous is None or rank < previous_rank or (
            rank == previous_rank and (str(row.get("collected_at", "")), int(row.get("id", 0)))
            > (str(previous.get("collected_at", "")), int(previous.get("id", 0)))
        ):
            selected[key] = row
    return sorted(selected.values(), key=lambda row: (str(row.get("source_date", "")), str(row.get("collected_at", ""))))


def canonical_latest(rows: list[dict]) -> list[dict]:
    latest: dict[str, dict] = {}
    for row in canonical_daily_rows(rows):
        latest[row["instrument"]] = row
    return list(latest.values())


def price_alert_key(product: str, instrument: str, direction: str, threshold: float) -> str:
    if instrument == "asphalt_index":
        return f"index:{product}:{direction}:{threshold:g}"
    if instrument == "bitumen_quote":
        return f"price:{product}:{direction}:{threshold:g}"
    return f"price:{product}:{instrument}:{direction}:{threshold:g}"
