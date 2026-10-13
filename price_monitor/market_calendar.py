"""NYMEX energy sessions in exchange time, including DST and closure overrides.

BZ and HO trade Sunday 18:00 through Friday 17:00 New York time, with a
17:00–18:00 maintenance break. Holiday/exceptional windows come from config;
they must be maintained against the exchange's published holiday schedule.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import MARKET_CLOSURES

EXCHANGE_ZONE = ZoneInfo("America/New_York")
LIVE_SOURCE_CODES = {"live_brent", "live_gasoil_proxy"}


def _closures():
    windows = []
    for item in MARKET_CLOSURES:
        try:
            start, end = (datetime.fromisoformat(item[key].replace("Z", "+00:00")) for key in ("start", "end"))
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                raise ValueError
        except (KeyError, AttributeError, TypeError, ValueError):
            raise ValueError("Chaque fermeture doit contenir start et end ISO 8601 avec fuseau, end > start.") from None
        windows.append((start.astimezone(timezone.utc), end.astimezone(timezone.utc)))
    return sorted(windows)


CLOSURES = _closures()


def market_session(now: datetime) -> dict:
    """Return open state and session boundaries as aware UTC datetimes."""
    if now.tzinfo is None:
        raise ValueError("Market calendar requires an aware timestamp")
    now = now.astimezone(timezone.utc)
    local = now.astimezone(EXCHANGE_ZONE)
    # Candidate sessions span the overnight boundary, weekends, and closures.
    # Jump over long configured closures rather than scanning minute by minute.
    candidate = local.date() - timedelta(days=1)
    while True:
        if candidate.weekday() in (0, 1, 2, 3, 6):
            start = datetime.combine(candidate, time(18), EXCHANGE_ZONE).astimezone(timezone.utc)
            end = datetime.combine(candidate + timedelta(days=1), time(17), EXCHANGE_ZONE).astimezone(timezone.utc)
            segments = [(start, end)]
            for closed_start, closed_end in CLOSURES:
                remaining = []
                for segment_start, segment_end in segments:
                    if closed_end <= segment_start or closed_start >= segment_end:
                        remaining.append((segment_start, segment_end))
                    else:
                        if segment_start < closed_start:
                            remaining.append((segment_start, closed_start))
                        if closed_end < segment_end:
                            remaining.append((closed_end, segment_end))
                segments = remaining
            for start, end in segments:
                if start <= now < end:
                    return {"open": True, "opened_at": start, "closes_at": end, "next_open_at": start}
                if start > now:
                    return {"open": False, "opened_at": None, "closes_at": None, "next_open_at": start}
        candidate += timedelta(days=1)
        # An exceptional closure may span weeks or months.
        probe = datetime.combine(candidate, time(18), EXCHANGE_ZONE).astimezone(timezone.utc)
        for closed_start, closed_end in CLOSURES:
            if closed_start <= probe < closed_end:
                candidate = max(candidate, closed_end.astimezone(EXCHANGE_ZONE).date() - timedelta(days=1))


def live_monitoring_anchor(source_code: str, now: datetime) -> datetime | None:
    """None suspends freshness checks; otherwise allow reopening grace."""
    if source_code not in LIVE_SOURCE_CODES:
        return None
    session = market_session(now)
    return session["opened_at"] if session["open"] else None
