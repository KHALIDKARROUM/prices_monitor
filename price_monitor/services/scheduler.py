from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from ..config import (DEMO_MODE, DIGEST_EMAIL_TO, PROVIDER_INTERVAL_MINUTES, PROVIDER_DAILY_LIMITS,
                      COLLECTION_BACKOFF_SECONDS, COLLECTION_MAX_BACKOFF_SECONDS)
from ..database import DatabaseBackend
from ..market_calendar import LIVE_SOURCE_CODES, market_session
from ..instruments import row_instrument
from ..observability import logger, metrics
from .alerts import AlertManager
from .collectors import collect_all, configured_providers
from .provider_requests import RequestGuard, guarded_requests
from .comparison import SOURCE_CODE_BY_LABEL, compare_api_prices
from .connectivity import website_status
from .digest import CollectionDigest


def _observation_source_health(observations: list[dict]) -> list[dict]:
    """Infer source success for compatible/custom collectors without metadata."""
    outcomes: list[dict] = []
    for observation in observations:
        code = observation.get("source_code") or SOURCE_CODE_BY_LABEL.get(observation.get("source"))
        if code:
            outcomes.append({"code": code, "success": True, "error": None})
    return outcomes


def _update_source_health(database: DatabaseBackend, outcomes: list[dict], at: str) -> None:
    updater = getattr(database, "update_source_health", None)
    if updater is None:
        return
    for outcome in outcomes:
        if outcome.get("deferred"):
            continue
        code = outcome.get("code")
        if not code:
            continue
        updater(
            str(code),
            bool(outcome.get("success")),
            at,
            str(outcome.get("error")) if outcome.get("error") else None,
            str(outcome.get("quote_at")) if outcome.get("quote_at") else None,
        )


def _collection_status(
    observations: list[dict],
    messages: list[str],
    source_outcomes: list[dict],
    has_source_metadata: bool,
) -> str:
    """Return the collection status without hiding source-level failures."""
    attempted = [outcome for outcome in source_outcomes if not outcome.get("deferred")]
    successful_sources = any(bool(outcome.get("success")) for outcome in attempted)
    failed_sources = any(not bool(outcome.get("success")) for outcome in attempted)
    if not attempted and source_outcomes:
        return "deferred"

    if successful_sources and failed_sources:
        return "partial"

    # Older/custom collectors may not attach source metadata. In that case,
    # observations plus an error message still unambiguously means partial.
    if not has_source_metadata and observations and messages:
        return "partial"

    return "success"


PROVIDER_SOURCES = {
    "fred": {"fred_diesel", "fred_brent"}, "eia": {"eia_diesel", "eia_brent"},
    "alpha_vantage": {"alpha_brent"}, "bls": {"bls_asphalt_ppi"}, "yahoo": LIVE_SOURCE_CODES,
    "demo": set(),
}


class CollectionCoordinator:
    """Shared policy for background collection and explicit manual refreshes."""

    def __init__(self, database, *, now=time.time, providers=None, intervals=None, limits=None):
        self.database = database
        self.now = now
        self.providers = configured_providers() if providers is None else list(providers)
        self.intervals = PROVIDER_INTERVAL_MINUTES if intervals is None else intervals
        self.guard = RequestGuard(database, now, limits)

    def snapshot(self) -> list[dict]:
        now = self.now()
        session = market_session(datetime.fromtimestamp(now, timezone.utc))
        result = []
        for provider in self.providers:
            state = self.database.collection_state(provider)
            budget = self.database.request_budget(provider, now, self.guard.limits.get(provider, 0))
            # BLS may still use its independently budgeted official FRED mirror.
            next_due = max(float(state["next_due"]), budget["retry_at"] if provider != "bls" else 0)
            reason = ""
            if provider == "yahoo" and not session["open"]:
                next_due = max(next_due, session["next_open_at"].timestamp())
                reason = "Market closed."
            if budget["retry_at"] > now and provider != "bls":
                reason = budget["reason"]
            running = float(state["lease_until"]) > now
            if running:
                reason = "Collection in progress."
            elif not reason and state["failures"] and next_due > now:
                reason = "Retry delayed after a failure."
            primary_info = "BLS API paused; FRED mirror available." if provider == "bls" and budget["retry_at"] > now else ""
            result.append({"provider": provider, "interval_minutes": self.intervals[provider],
                           "status": "running" if running else "deferred" if reason else state["status"],
                           "next_collection_at": datetime.fromtimestamp(max(now, next_due), timezone.utc).isoformat(),
                           "requests_last_24h": budget["used"], "request_limit_24h": budget["limit"],
                           "failures": state["failures"], "reason": reason or primary_info, "last_error": state["last_error"]})
        return result

    def collect_provider(self, provider: str, *, force=False) -> dict:
        if provider not in self.providers:
            raise ValueError("Provider is not configured.")
        now = self.now()
        if provider == "yahoo":
            session = market_session(datetime.fromtimestamp(now, timezone.utc))
            if not session["open"]:
                return self._deferred(provider, "Market closed.", session["next_open_at"].timestamp())
        budget = self.database.request_budget(provider, now, self.guard.limits.get(provider, 0))
        if budget["retry_at"] > now and provider != "bls":
            return self._deferred(provider, budget["reason"], budget["retry_at"])
        token = self.database.claim_collection(provider, now, force)
        if token is None:
            state = self.database.collection_state(provider)
            reason = "Collection in progress." if state["lease_until"] > now else "Next collection is scheduled."
            return self._deferred(provider, reason, max(state["next_due"], state["lease_until"]))
        state = self.database.collection_state(provider)
        try:
            with guarded_requests(self.guard):
                result = run_collection(self.database, collector=lambda: collect_all(provider))
        except Exception:
            # Unexpected infrastructure errors still release the lease and
            # receive backoff; do not send repeated requests after a DB failure.
            finished = self.now()
            self.database.finish_collection(provider, token, now=finished,
                next_due=finished + COLLECTION_MAX_BACKOFF_SECONDS, failures=int(state["failures"]) + 1,
                status="error", error="Internal collection error.")
            raise
        finished = self.now()
        outcomes = result.get("source_outcomes", [])
        failed = result["status"] in {"partial", "error"}
        failures = int(state["failures"]) + 1 if failed else 0
        delay = self.intervals[provider] * 60
        if failed:
            delay = min(COLLECTION_MAX_BACKOFF_SECONDS, COLLECTION_BACKOFF_SECONDS * 2 ** min(failures - 1, 20))
        retry_at = max([float(row.get("retry_at") or 0) for row in outcomes] + [0])
        next_due = max(finished + delay, retry_at)
        self.database.finish_collection(provider, token, now=finished, next_due=next_due,
            failures=failures, status=result["status"], error=result["message"] if failed else "")
        # A failed optional provider must not flap the global connectivity
        # banner while another provider is still supplying current data.
        healthy = self.availability()["status"] in {"success", "partial"}
        generation = result.pop("generation")
        if result["status"] != "deferred":
            # Use the generation captured before the requests. A response from
            # before an Internet disconnect cannot announce restored service.
            website_status.record_collection(healthy and not DEMO_MODE, generation)
        return dict(result, provider=provider, next_collection_at=datetime.fromtimestamp(next_due, timezone.utc).isoformat())

    def availability(self) -> dict:
        """Aggregate current provider health, rather than the last worker's log."""
        now = self.now()
        session = market_session(datetime.fromtimestamp(now, timezone.utc))
        healthy = failed = False
        latest = None
        for source in self.database.source_health():
            owner = next((name for name in self.providers if source["code"] in PROVIDER_SOURCES[name]), None)
            if not owner:
                continue
            failed |= bool(source.get("last_error"))
            if source.get("last_success_at"):
                stamp = AlertManager._parse_datetime(source["last_success_at"])
                latest = max(latest, stamp) if latest else stamp
                recent = now - stamp.timestamp() <= max(300, self.intervals[owner] * 120)
                # The retained closing quote is expected during a closed session.
                if owner == "yahoo" and not session["open"]:
                    recent = True
                healthy |= recent and not source.get("last_error")
        status = "partial" if healthy and failed else "success" if healthy else "error" if failed or latest else "pending"
        return {"status": status, "finished_at": latest.isoformat() if latest else None}

    @staticmethod
    def _deferred(provider, reason, until):
        return {"provider": provider, "status": "deferred", "rows": 0, "message": reason,
                "next_collection_at": datetime.fromtimestamp(until, timezone.utc).isoformat()}

    def collect_now(self) -> dict:
        # Manual requests may refresh a healthy provider before its normal due
        # date, but cannot bypass a lease, backoff, quota or market closure.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, len(self.providers))) as pool:
            results = list(pool.map(lambda name: self.collect_provider(name, force=True), self.providers))
        attempted = [row for row in results if row["status"] != "deferred"]
        status = "deferred" if not attempted else "error" if all(row["status"] == "error" for row in attempted) else "partial" if any(row["status"] in {"error", "partial"} for row in attempted) else "success"
        return {"status": status, "rows": sum(row["rows"] for row in results),
                "message": " ".join(f"{row['provider']} : {row['message']}" for row in results), "providers": results,
                "source_comparison": self.database.source_comparison()}


class CollectionScheduler:
    """One bounded worker per provider; alert/digest checks have their own loop."""

    def __init__(self, database: DatabaseBackend, coordinator=None):
        self.database = database
        self.coordinator = coordinator or CollectionCoordinator(database)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._digest: CollectionDigest | None = None
        self._workers: list[threading.Thread] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        # Capture the current log cursor before the startup collection so it
        # appears in the first five-hour report.
        self._digest = CollectionDigest(self.database) if DIGEST_EMAIL_TO else None
        # All due initial collections run in the background. Saved data is
        # available immediately, and a slow provider cannot stall its peers.
        self._workers = [threading.Thread(target=self._run_provider, args=(provider,),
                         name=f"collection-{provider}", daemon=True) for provider in self.coordinator.providers]
        for worker in self._workers:
            worker.start()
        self._thread = threading.Thread(target=self._run, name="price-collection", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and all(worker.is_alive() for worker in self._workers))

    def _run_provider(self, provider: str) -> None:
        while not self._stop.is_set():
            try:
                result = self.coordinator.collect_provider(provider)
                until = datetime.fromisoformat(result["next_collection_at"]).timestamp()
                delay = max(1, min(60, until - self.coordinator.now()))
            except Exception:
                logger.exception("Provider collection failed", extra={"event": "collection_scheduler_error", "provider": provider})
                delay = 60
            self._stop.wait(delay)

    def _run(self) -> None:
        last_health_check = 0.0
        next_live_check = time.monotonic()
        while not self._stop.wait(1):
            if time.monotonic() - last_health_check >= 300:
                last_health_check = time.monotonic()
                evaluate_source_alerts(self.database)
            if time.monotonic() >= next_live_check:
                evaluate_live_alerts(self.database)
                next_live_check = time.monotonic() + _next_live_check_delay(self.database)
            if self._digest is not None:
                self._digest.maybe_send()


def evaluate_source_alerts(database: DatabaseBackend) -> None:
    """Check source freshness even when a scheduled collection is missed."""
    try:
        AlertManager(database).evaluate_sources(database.source_health())
    except Exception:
        # Alerting must never stop the collection scheduler.
        logger.exception("Source alert evaluation failed", extra={"event": "source_alert_evaluation_failed"})


def evaluate_live_alerts(database: DatabaseBackend) -> None:
    """Check live quotes at their freshness deadline, even between collections."""
    try:
        AlertManager(database).evaluate_live_freshness(database.source_health())
    except Exception:
        logger.exception("Live quote alert evaluation failed", extra={"event": "live_alert_evaluation_failed"})


def _next_live_check_delay(database: DatabaseBackend, now: datetime | None = None) -> float:
    """Wake at the next quote deadline; retry undelivered alerts each minute."""
    try:
        manager = AlertManager(database)
        sources = {row["code"]: row for row in database.source_health()}
        now = now or datetime.now(timezone.utc)
        delay = 60.0
        for rule in manager.freshness_rules:
            code = {"brent": "live_brent", "gasoil": "live_gasoil_proxy"}.get(rule["product"])
            source = sources.get(code)
            if not source or not source.get("active", 1):
                continue
            session = market_session(now)
            if not session["open"]:
                continue
            anchor = manager._parse_datetime(source.get("last_quote_at") or rule["created_at"])
            threshold = float(rule["threshold"])
            deadline = max(anchor + timedelta(minutes=threshold),
                           session["opened_at"] + timedelta(minutes=min(5, threshold)))
            seconds = (deadline - now).total_seconds()
            if seconds > 0:
                delay = min(delay, seconds)
        return max(0.1, delay)
    except Exception:
        logger.exception("Live quote alert scheduling failed", extra={"event": "live_alert_schedule_failed"})
        return 60.0


def _evaluate_collection_alerts(database: DatabaseBackend, observations: list[dict]) -> None:
    try:
        AlertManager(database).evaluate_collection(observations, database.source_health())
    except Exception:
        # Alerting must never turn a successful collection into an error.
        logger.exception("Collection alert evaluation failed", extra={"event": "collection_alert_evaluation_failed"})


def run_collection(database: DatabaseBackend, *, collector=None) -> dict:
    """Persist one batch. Production callers use CollectionCoordinator policy."""
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    source_outcomes: list[dict] = []
    generation = website_status.begin_collection()
    try:
        batch = (collector or collect_all)()
        observations, messages = batch
        source_health = getattr(batch, "source_health", None)
        benchmarks = list(getattr(batch, "benchmarks", []))
        has_source_metadata = source_health is not None
        source_outcomes = list(source_health or []) or _observation_source_health(observations)
        inserted_at = datetime.now(timezone.utc)
        for index, observation in enumerate(observations):
            # Preserve an unambiguous ordering when multiple provider quotes
            # arrive within one second; this lets live quotes win the dashboard
            # card without losing daily benchmarks from the audit trail.
            record = dict(observation)
            record.setdefault("collected_at", (inserted_at + timedelta(microseconds=index)).isoformat())
            database.insert_observation(record)
        benchmark_inserter = getattr(database, "insert_benchmark", None)
        if callable(benchmark_inserter):
            for benchmark in benchmarks:
                benchmark_inserter(benchmark)
        reconcile_forecasts = getattr(database, "reconcile_live_forecasts", None)
        if callable(reconcile_forecasts):
            # Provider workers complete independently. Re-evaluate against the
            # stable canonical daily observation after every batch, so the
            # first response cannot permanently choose the scored actual.
            reconcile_forecasts()
        finished = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        _update_source_health(database, source_outcomes, finished)
        # With independent workers, compare their latest successful published
        # quotes; source health still excludes failed/disabled providers. Legacy
        # whole-batch callers compare only the responses received in this run.
        comparison = (database.source_comparison() if collector is not None else
                      compare_api_prices(observations, benchmarks, database.source_health()))
        message = " ".join(messages) or "Collection completed successfully."
        if comparison["warnings"] and (observations or benchmarks):
            warning = " ".join([comparison["message"], *comparison["warnings"]])
            message = f"{message} {warning}"
            logger.warning(warning, extra={"event": "api_price_mismatch", "mismatch_count": comparison["mismatch_count"]})
        status = _collection_status(observations, messages, source_outcomes, has_source_metadata)
        row_count = len(observations) + len(benchmarks)
        if status != "deferred":
            database.log_collection(status, row_count, message, started, finished)
        index_observations = [dict(row, price=row["value"], data_kind="index") for row in benchmarks]
        price_observations = observations
        if collector is not None and observations:
            # Providers arrive independently now. Preserve the same canonical
            # price priority as the dashboard, rather than evaluating whichever
            # equivalent feed happened to finish last.
            affected = {row_instrument(row) for row in observations}
            price_observations = [row for row in database.latest_by_instrument() if row["instrument"] in affected]
        _evaluate_collection_alerts(database, price_observations + index_observations)
        metrics.increment("price_monitor_collection_total", labels={"status": status})
        if collector is None and status != "deferred":
            website_status.record_collection(row_count > 0 and not DEMO_MODE, generation)
        logger.debug(
            "Collection completed",
            extra={"event": "collection_completed", "status": status, "rows": row_count},
        )
        result = {"status": status, "rows": row_count, "message": message, "source_comparison": comparison}
        if collector is not None:
            result["source_outcomes"] = source_outcomes
            result["generation"] = generation
        if benchmarks:
            result["benchmarks"] = len(benchmarks)
        return result
    except Exception as exc:
        if collector is None:
            website_status.record_collection(False, generation)
        finished = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if not source_outcomes:
            source_outcomes = list(getattr(exc, "source_health", []))
        _update_source_health(database, source_outcomes, finished)
        database.log_collection("error", 0, str(exc), started, finished)
        _evaluate_collection_alerts(database, [])
        metrics.increment("price_monitor_collection_total", labels={"status": "error"})
        logger.debug("Collection failed", extra={"event": "collection_failed"}, exc_info=True)
        result = {"status": "error", "rows": 0, "message": str(exc), "source_comparison": compare_api_prices([])}
        if collector is not None:
            result["source_outcomes"] = source_outcomes
            result["generation"] = generation
        return result
