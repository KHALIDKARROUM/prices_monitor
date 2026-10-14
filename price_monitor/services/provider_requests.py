"""Request-level quota and Retry-After handling for all collector transports."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from email.utils import parsedate_to_datetime
from math import isfinite
import time
from urllib.parse import urlsplit

from ..config import PROVIDER_DAILY_LIMITS

_guard = ContextVar("provider_request_guard", default=None)


class ProviderDeferred(RuntimeError):
    def __init__(self, message: str, retry_at: float, *, deferred: bool = True):
        super().__init__(message)
        self.retry_at = retry_at
        self.deferred = deferred


def provider_for_url(url: str) -> str:
    host = urlsplit(url).hostname or ""
    if host.endswith("stlouisfed.org"):
        return "fred"
    if host == "api.eia.gov":
        return "eia"
    if host.endswith("alphavantage.co"):
        return "alpha_vantage"
    if host.endswith("bls.gov"):
        return "bls"
    if host.endswith("finance.yahoo.com"):
        return "yahoo"
    raise ValueError("Unknown collection provider.")


class RequestGuard:
    def __init__(self, database, now=time.time, limits=None):
        self.database = database
        self.now = now
        self.limits = PROVIDER_DAILY_LIMITS if limits is None else limits

    def reserve(self, provider: str):
        reservation = self.database.reserve_provider_request(provider, self.now(), self.limits.get(provider, 0))
        if not reservation["allowed"]:
            raise ProviderDeferred(f"{provider} : {reservation['reason']}", reservation["retry_at"])

    def defer(self, provider: str, until: float, message: str):
        self.database.defer_provider_requests(provider, until, message)


@contextmanager
def guarded_requests(guard):
    token = _guard.set(guard)
    try:
        yield
    finally:
        _guard.reset(token)


def reserve_request(url: str) -> None:
    guard = _guard.get()
    if guard is not None:
        guard.reserve(provider_for_url(url))


def rate_limit_error(provider: str, retry_after=None, *, daily=False, detail="") -> ProviderDeferred:
    guard = _guard.get()
    now = guard.now() if guard else time.time()
    # A provider returning a daily quota notice must stop until the daily
    # allowance can renew, even when another application consumed the key.
    until = now + (86400 if daily else 60)
    if retry_after:
        try:
            seconds = float(retry_after)
            if isfinite(seconds):
                until = max(until, now + max(0, seconds))
        except (TypeError, ValueError):
            try:
                until = max(until, parsedate_to_datetime(str(retry_after)).timestamp())
            except (TypeError, ValueError, OverflowError):
                pass
    message = f"{provider}: {'daily limit' if daily else 'provider limit'} reached; collection deferred."
    if detail:
        message += f" {detail[:140]}"
    if guard:
        guard.defer(provider, until, message)
    return ProviderDeferred(message, until, deferred=False)
