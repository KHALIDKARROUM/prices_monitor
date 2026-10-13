from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from ..config import COLLECTION_INTERVAL_MINUTES, DEMO_MODE, DIGEST_EMAIL_TO
from ..database import DatabaseBackend
from ..observability import logger, metrics
from .alerts import AlertManager
from .collectors import collect_all
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
    successful_sources = any(bool(outcome.get("success")) for outcome in source_outcomes)
    failed_sources = any(not bool(outcome.get("success")) for outcome in source_outcomes)

    if successful_sources and failed_sources:
        return "partial"

    # Older/custom collectors may not attach source metadata. In that case,
    # observations plus an error message still unambiguously means partial.
    if not has_source_metadata and observations and messages:
        return "partial"

    return "success"


class CollectionScheduler:
    """Lightweight cron-compatible scheduler; production can also call /api/collect from cron."""

    def __init__(self, database: DatabaseBackend):
        self.database = database
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._digest: CollectionDigest | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        # Capture the current log cursor before the startup collection so it
        # appears in the first five-hour report.
        self._digest = CollectionDigest(self.database) if DIGEST_EMAIL_TO else None
        # A launch must not wait for the next schedule slot: the first page
        # load should show the newest value the providers have published.
        try:
            run_collection(self.database)
        except Exception:
            logger.exception("Initial collection failed", extra={"event": "initial_collection_error"})
        self._thread = threading.Thread(target=self._run, name="price-collection", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        last_health_check = 0.0
        next_collection = time.monotonic() + COLLECTION_INTERVAL_MINUTES * 60
        next_live_check = time.monotonic()
        while not self._stop.wait(1):
            if time.monotonic() - last_health_check >= 300:
                last_health_check = time.monotonic()
                evaluate_source_alerts(self.database)
            if time.monotonic() >= next_live_check:
                evaluate_live_alerts(self.database)
                next_live_check = time.monotonic() + _next_live_check_delay(self.database)
            if time.monotonic() >= next_collection:
                next_collection = time.monotonic() + COLLECTION_INTERVAL_MINUTES * 60
                try:
                    run_collection(self.database)
                except Exception:
                    # The scheduler must survive a single failed scheduled run.
                    logger.exception("Scheduled collection failed", extra={"event": "collection_scheduler_error"})
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


def _next_live_check_delay(database: DatabaseBackend) -> float:
    """Wake at the next quote deadline; retry undelivered alerts each minute."""
    try:
        manager = AlertManager(database)
        sources = {row["code"]: row for row in database.source_health()}
        now = datetime.now(timezone.utc)
        delay = 60.0
        for rule in manager.freshness_rules:
            code = {"brent": "live_brent", "gasoil": "live_gasoil_proxy"}.get(rule["product"])
            source = sources.get(code)
            if not source or not source.get("active", 1):
                continue
            anchor = manager._parse_datetime(source.get("last_quote_at") or rule["created_at"])
            seconds = (anchor + timedelta(minutes=float(rule["threshold"])) - now).total_seconds()
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


def run_collection(database: DatabaseBackend) -> dict:
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    source_outcomes: list[dict] = []
    generation = website_status.begin_collection()
    try:
        batch = collect_all()
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
        finished = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        _update_source_health(database, source_outcomes, finished)
        # Check only prices received in this run. A saved quote from an API
        # that did not return data cannot verify the current collection.
        comparison = compare_api_prices(observations, benchmarks, database.source_health())
        message = " ".join(messages) or "Collecte terminée avec succès."
        if comparison["warnings"]:
            warning = " ".join([comparison["message"], *comparison["warnings"]])
            message = f"{message} {warning}"
            logger.warning(warning, extra={"event": "api_price_mismatch", "mismatch_count": comparison["mismatch_count"]})
        status = _collection_status(observations, messages, source_outcomes, has_source_metadata)
        row_count = len(observations) + len(benchmarks)
        database.log_collection(status, row_count, message, started, finished)
        index_observations = [dict(row, price=row["value"], data_kind="index") for row in benchmarks]
        _evaluate_collection_alerts(database, observations + index_observations)
        metrics.increment("price_monitor_collection_total", labels={"status": status})
        website_status.record_collection(row_count > 0 and not DEMO_MODE, generation)
        logger.debug(
            "Collection completed",
            extra={"event": "collection_completed", "status": status, "rows": row_count},
        )
        result = {"status": status, "rows": row_count, "message": message, "source_comparison": comparison}
        if benchmarks:
            result["benchmarks"] = len(benchmarks)
        return result
    except Exception as exc:
        website_status.record_collection(False, generation)
        finished = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        if not source_outcomes:
            source_outcomes = list(getattr(exc, "source_health", []))
        _update_source_health(database, source_outcomes, finished)
        database.log_collection("error", 0, str(exc), started, finished)
        _evaluate_collection_alerts(database, [])
        metrics.increment("price_monitor_collection_total", labels={"status": "error"})
        logger.debug("Collection failed", extra={"event": "collection_failed"}, exc_info=True)
        return {"status": "error", "rows": 0, "message": str(exc), "source_comparison": compare_api_prices([])}
