from __future__ import annotations

import json
import os
import random
import csv
import math
import threading
import time
from io import StringIO
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import Any

from ..config import ALPHA_VANTAGE_API_KEY, DEMO_MODE, EIA_API_KEY, FRED_API_KEY, LIVE_QUOTES_ENABLED
from ..bitumen import benchmark_payload
from .provider_requests import ProviderDeferred, reserve_request, provider_for_url, rate_limit_error


_bls_lock = threading.Lock()
_bls_next_check = 0.0
_bls_defer_reason = ""


def _latest_valid_observation(rows, date_field: str, value_field: str, provider: str) -> tuple[str, float]:
    """Choose the newest published finite, positive value, regardless of order."""
    usable = []
    today = datetime.now(timezone.utc).date()
    for row in rows:
        try:
            published = date.fromisoformat(str(row[date_field])[:10])
            value = float(row[value_field])
            if published <= today and math.isfinite(value) and value > 0:
                usable.append((published.isoformat(), value))
        except (KeyError, TypeError, ValueError):
            continue
    if not usable:
        raise RuntimeError(f"{provider} returned no usable value")
    return max(usable, key=lambda item: item[0])


class CollectionBatch(tuple):
    """A backwards-compatible pair with per-source outcomes attached."""

    source_health: list[dict[str, Any]]
    benchmarks: list[dict[str, Any]]

    def __new__(
        cls,
        observations: list[dict],
        messages: list[str],
        source_health: list[dict[str, Any]],
        benchmarks: list[dict[str, Any]] | None = None,
    ) -> "CollectionBatch":
        result = super().__new__(cls, (observations, messages))
        result.source_health = source_health
        result.benchmarks = list(benchmarks or [])
        return result


class CollectionError(RuntimeError):
    """Raised when every configured source fails, retaining source outcomes."""

    source_health: list[dict[str, Any]]

    def __init__(self, message: str, source_health: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.source_health = source_health


def _request_json(url: str) -> dict:
    return json.loads(_request_text(url))


def _request_text(url: str) -> str:
    """Fetch a public text feed without requiring an API token."""
    reserve_request(url)
    req = Request(url, headers={"User-Agent": "PriceMonitor/1.0"})
    try:
        with urlopen(req, timeout=12) as response:
            return response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code in (429, 503):
            provider = provider_for_url(url)
            error = rate_limit_error(provider, (exc.headers or {}).get("Retry-After"),
                                     daily=exc.code == 429 and provider in {"alpha_vantage", "bls"})
            exc.close()
            raise error from None
        raise


def _latest_fred_csv_observation(series_id: str) -> tuple[str, float]:
    """Return the newest published value from FRED's public CSV endpoint.

    The FRED JSON API needs a user key, whereas the chart CSV endpoint is
    public.  Keeping this fallback means a new installation can update its
    daily EIA benchmarks immediately, while still preferring the authenticated
    API when a key is configured.
    """
    rows = csv.DictReader(
        StringIO(_request_text(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"))
    )
    observations = []
    for row in rows:
        published = row.get("observation_date") or row.get("DATE") or row.get("date")
        value = row.get(series_id) or row.get("value") or row.get("VALUE")
        observations.append({"date": published, "value": value})
    return _latest_valid_observation(observations, "date", "value", f"FRED ({series_id})")


def _latest_eia_observation(series_id: str) -> tuple[str, float]:
    """Read a direct EIA v2 series, independently of the FRED mirror."""
    if not EIA_API_KEY:
        raise RuntimeError("EIA_API_KEY is not configured")
    query = urlencode({"api_key": EIA_API_KEY, "length": 10, "sort[0][column]": "period", "sort[0][direction]": "desc"})
    data = _request_json(f"https://api.eia.gov/v2/seriesid/{series_id}?{query}")
    rows = (data.get("response") or {}).get("data") or []
    observations = [{"date": row.get("period") or row.get("date"), "value": row.get("value")} for row in rows]
    return _latest_valid_observation(observations, "date", "value", f"EIA ({series_id})")


def _latest_live_quote(symbol: str) -> tuple[float, datetime, int | None]:
    """Return the most recent exchange quote exposed by Yahoo Finance."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?interval=1m&range=1d"
    data = _request_json(url)
    result = ((data.get("chart") or {}).get("result") or [None])[0] or {}
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    timestamp = meta.get("regularMarketTime")
    if price in (None, "") or timestamp in (None, ""):
        raise RuntimeError(f"Live quote unavailable for {symbol}")
    try:
        quote_time = datetime.fromtimestamp(int(timestamp), timezone.utc)
        delay = meta.get("exchangeDataDelayedBy")
        quote_price = float(price)
        if not 0 < quote_price < 1_000_000 or quote_time > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("price or timestamp outside valid range")
        return quote_price, quote_time, int(delay) if delay is not None else None
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Invalid live quote for {symbol}") from exc


def collect_live_brent() -> dict:
    price, quote_time, delay = _latest_live_quote("BZ=F")
    delay_note = f"Exchange-reported delay: {delay} min." if delay else "No exchange-reported delay."
    return {
        "product": "brent", "price": price, "unit": "USD/baril",
        "source": "Yahoo Finance - Brent futures BZ=F", "source_code": "live_brent",
        "source_date": quote_time.date().isoformat(),
        "quote_at": quote_time.isoformat(),
        "notes": f"Market quote at {quote_time.isoformat()}. {delay_note}",
    }


def collect_live_gasoil() -> dict:
    """Use live heating-oil futures as a clearly labelled gasoil proxy."""
    price, quote_time, delay = _latest_live_quote("HO=F")
    delay_note = f"Exchange-reported delay: {delay} min." if delay else "No exchange-reported delay."
    return {
        "product": "gasoil", "price": price, "unit": "USD/gallon",
        "source": "Yahoo Finance - NY Harbor Heating Oil HO=F (proxy gasoil)", "source_code": "live_gasoil_proxy",
        "source_date": quote_time.date().isoformat(),
        "quote_at": quote_time.isoformat(),
        "notes": f"Live diesel proxy quoted at {quote_time.isoformat()}. {delay_note}",
    }


def collect_gasoil() -> dict:
    if FRED_API_KEY:
        try:
            query = urlencode({"series_id": "DDFUELNYH", "api_key": FRED_API_KEY, "file_type": "json", "sort_order": "desc", "limit": 10})
            data = _request_json(f"https://api.stlouisfed.org/fred/series/observations?{query}")
            published, price = _latest_valid_observation(data.get("observations", []), "date", "value", "FRED (DDFUELNYH)")
            return {"product": "gasoil", "price": price, "unit": "USD/gallon", "source": "EIA/FRED - DDFUELNYH", "source_code": "fred_diesel", "source_date": published, "notes": "FRED API collection"}
        except ProviderDeferred:
            raise
        except Exception:
            # Fall through to FRED's public CSV feed if the configured key is
            # expired or temporarily unavailable.
            pass
    date, price = _latest_fred_csv_observation("DDFUELNYH")
    return {"product": "gasoil", "price": price, "unit": "USD/gallon", "source": "EIA/FRED - DDFUELNYH", "source_code": "fred_diesel", "source_date": date, "notes": "Public FRED collection"}


def collect_brent() -> dict:
    if FRED_API_KEY:
        try:
            query = urlencode({"series_id": "DCOILBRENTEU", "api_key": FRED_API_KEY, "file_type": "json", "sort_order": "desc", "limit": 10})
            data = _request_json(f"https://api.stlouisfed.org/fred/series/observations?{query}")
            published, price = _latest_valid_observation(data.get("observations", []), "date", "value", "FRED (DCOILBRENTEU)")
            return {"product": "brent", "price": price, "unit": "USD/baril", "source": "EIA/FRED - DCOILBRENTEU", "source_code": "fred_brent", "source_date": published, "notes": "FRED API collection"}
        except ProviderDeferred:
            raise
        except Exception:
            pass
    date, price = _latest_fred_csv_observation("DCOILBRENTEU")
    return {"product": "brent", "price": price, "unit": "USD/baril", "source": "EIA/FRED - DCOILBRENTEU", "source_code": "fred_brent", "source_date": date, "notes": "Public FRED collection"}


def collect_alpha_brent() -> dict:
    """Collect the Alpha Vantage Brent series when its optional key is present."""
    if not ALPHA_VANTAGE_API_KEY:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY is not configured")
    query = urlencode({"function": "BRENT", "interval": "daily", "apikey": ALPHA_VANTAGE_API_KEY, "datatype": "json"})
    data = _request_json(f"https://www.alphavantage.co/query?{query}")
    notice = str(data.get("Note") or data.get("Information") or "").lower()
    if any(word in notice for word in ("rate limit", "call frequency", "requests per day", "calls per day", "25 requests", "premium")):
        raise rate_limit_error("alpha_vantage", daily=True)
    if data.get("Error Message"):
        # Do not echo upstream payloads that might contain the configured key.
        raise RuntimeError("Alpha Vantage: request denied or invalid configuration.")
    rows = data.get("data") or data.get("observations") or []
    observations = [{"date": row.get("date") or row.get("timestamp"), "value": row.get("value") or row.get("price")} for row in rows]
    published, price = _latest_valid_observation(observations, "date", "value", "Alpha Vantage (BRENT)")
    return {"product": "brent", "price": price, "unit": "USD/baril", "source": "Alpha Vantage - BRENT", "source_code": "alpha_brent", "source_date": published, "notes": "Alpha Vantage API collection"}


def collect_eia_gasoil() -> dict:
    """Collect the EIA diesel benchmark directly, rather than via FRED."""
    date, price = _latest_eia_observation("PET.DDFUELNYH.D")
    return {"product": "gasoil", "price": price, "unit": "USD/gallon", "source": "U.S. EIA - PET.DDFUELNYH.D", "source_code": "eia_diesel", "source_date": date, "notes": "Direct EIA API collection"}


def collect_eia_brent() -> dict:
    """Collect the EIA Brent benchmark directly, rather than via FRED."""
    date, price = _latest_eia_observation("PET.RBRTE.D")
    return {"product": "brent", "price": price, "unit": "USD/baril", "source": "U.S. EIA - PET.RBRTE.D", "source_code": "eia_brent", "source_date": date, "notes": "Direct EIA API collection"}


def _collect_bls_bitumen() -> dict:
    """Collect a free public asphalt PPI as a clearly labelled bitumen proxy.

    WPU058 is an official monthly U.S. producer-price index.  It is useful for
    direction and negotiation context, but it is not a cargo quote and must
    never be represented as USD/tonne.
    """
    data = _request_json("https://api.bls.gov/publicAPI/v2/timeseries/data/WPU058?latest=true")
    results = data.get("Results") or {}
    series = results.get("series") if isinstance(results, dict) else None
    notice = " ".join(str(item) for item in data.get("message") or []).lower()
    if any(word in notice for word in ("daily threshold", "daily limit", "rate limit", "too many requests")):
        raise rate_limit_error("bls", daily=True, detail="; ".join(str(item) for item in data.get("message") or []))
    if data.get("status") != "REQUEST_SUCCEEDED" or not series:
        raise RuntimeError("; ".join(str(item) for item in data.get("message") or []) or "Indice bitume BLS indisponible")
    usable = []
    for row in series[0].get("data") or []:
        try:
            period = str(row.get("period") or "")
            if not period.startswith("M") or not 1 <= int(period[1:]) <= 12:
                continue
            usable.append({"date": f"{int(row['year']):04d}-{int(period[1:]):02d}-01", "value": row["value"]})
        except (KeyError, TypeError, ValueError):
            continue
    return benchmark_payload(*_latest_valid_observation(usable, "date", "value", "BLS (WPU058)"))


def collect_bitumen_benchmark() -> dict:
    """Space direct BLS queries and fetch its official FRED mirror in between."""
    global _bls_next_check, _bls_defer_reason
    direct_error = None
    with _bls_lock:
        now = time.monotonic()
        if now >= _bls_next_check:
            try:
                result = _collect_bls_bitumen()
                _bls_next_check = time.monotonic() + 6 * 3600
                _bls_defer_reason = "Direct BLS checks are spaced six hours apart to respect the quota."
                return result
            except Exception as error:
                direct_error = error
                _bls_defer_reason = str(error)
                quota = any(text in _bls_defer_reason.lower() for text in ("daily threshold", "rate limit", "http error 429", "limite quotidienne"))
                _bls_next_check = time.monotonic() + (24 * 3600 if quota else 3600)
        reason = _bls_defer_reason
    # This fetch is never cached: last_success_at continues to describe an
    # actual provider response, and monthly source dates remain unchanged.
    try:
        source_date, value = _latest_fred_csv_observation("WPU058")
        result = benchmark_payload(source_date, value, mirror=True)
        result["notes"] += f" FRED mirror used: {reason[:140]}"
        return result
    except ProviderDeferred as fred_error:
        # If both transports were locally deferred, there was no new source
        # failure. A real BLS failure must still be visible when its mirror is
        # unavailable, with the next opportunity to use either transport.
        real_failure = direct_error is not None and not getattr(direct_error, "deferred", False)
        retry_times = [fred_error.retry_at]
        if getattr(direct_error, "retry_at", None):
            retry_times.append(direct_error.retry_at)
        raise ProviderDeferred(f"Bitumen index deferred — BLS: {reason}; FRED: {fred_error}",
                               min(retry_times), deferred=not real_failure) from None
    except Exception as fred_error:
        raise RuntimeError(f"Indice bitume indisponible — BLS : {reason}; FRED : {fred_error}") from fred_error


def demo_observations() -> list[dict]:
    """A clearly labelled fallback for demonstrations and local development."""
    now = datetime.now(timezone.utc)
    jitter = random.uniform(-0.01, 0.01)
    return [
        {"product": "gasoil", "price": round(2.33 + jitter, 4), "unit": "USD/gallon", "source": "Demo mode - EIA/FRED", "source_date": now.date().isoformat(), "notes": "API source not configured; demo value."},
        {"product": "brent", "price": round(81.4 + jitter * 10, 2), "unit": "USD/baril", "source": "Demo mode - Alpha Vantage", "source_date": now.date().isoformat(), "notes": "API source not configured; demo value."},
        {"product": "bitume", "price": round(522 + jitter * 40, 2), "unit": "USD/tonne", "source": "Demo mode - internal entry", "source_date": now.date().isoformat(), "notes": "Replace with an approved quote, order, or invoice."},
    ]


def configured_providers() -> list[str]:
    if DEMO_MODE:
        return ["demo"]
    return ["fred", "bls"] + (["eia"] if EIA_API_KEY else []) + (["alpha_vantage"] if ALPHA_VANTAGE_API_KEY else []) + (["yahoo"] if LIVE_QUOTES_ENABLED else [])


def collect_all(provider: str | None = None) -> tuple[list[dict], list[str]]:
    if DEMO_MODE:
        return CollectionBatch(demo_observations(), ["Demo mode is active."], [], [])
    observations: list[dict] = []
    benchmarks: list[dict] = []
    messages: list[str] = []
    source_health: list[dict[str, Any]] = []
    collectors: list[tuple[str, Any]] = []
    if provider in (None, "fred"):
        collectors.extend((("fred_diesel", collect_gasoil), ("fred_brent", collect_brent)))
    # Keep additional providers separate in the audit trail instead of silently
    # replacing one source's quote with another's.
    if EIA_API_KEY and provider in (None, "eia"):
        collectors.extend((("eia_diesel", collect_eia_gasoil), ("eia_brent", collect_eia_brent)))
    if ALPHA_VANTAGE_API_KEY and provider in (None, "alpha_vantage"):
        collectors.append(("alpha_brent", collect_alpha_brent))
    if LIVE_QUOTES_ENABLED and provider in (None, "yahoo"):
        # These are appended last so the dashboard's latest cards use the
        # current market quote while daily feeds remain in the history.
        collectors.extend((("live_gasoil_proxy", collect_live_gasoil), ("live_brent", collect_live_brent)))
    for source_code, collector in collectors:
        try:
            observation = collector()
            observations.append(observation)
            source_health.append({"code": observation.get("source_code") or source_code, "success": True, "error": None, "quote_at": observation.get("quote_at")})
        except Exception as exc:  # source failure must not stop the other source
            messages.append(str(exc))
            source_health.append({"code": source_code, "success": False, "error": str(exc),
                                  "deferred": bool(getattr(exc, "deferred", False)), "retry_at": getattr(exc, "retry_at", 0)})
    if provider in (None, "bls"):
        try:
            benchmark = collect_bitumen_benchmark()
            benchmarks.append(benchmark)
            source_health.append(
                {"code": benchmark.get("source_code") or "bls_asphalt_ppi", "success": True, "error": None}
            )
        except Exception as exc:
            messages.append(str(exc))
            source_health.append({"code": "bls_asphalt_ppi", "success": False, "error": str(exc),
                                  "deferred": bool(getattr(exc, "deferred", False)), "retry_at": getattr(exc, "retry_at", 0)})
    if not observations and not benchmarks and any(not row.get("deferred") for row in source_health):
        raise CollectionError(
            (f"{provider}: no data received. {' '.join(messages)}" if provider else
             "No public source is available. Configure FRED_API_KEY and check BLS API access."),
            source_health,
        )
    return CollectionBatch(observations, messages, source_health, benchmarks)
