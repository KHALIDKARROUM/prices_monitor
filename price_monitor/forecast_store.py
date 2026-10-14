"""Persistent live forecast ledger and instrument-safe scoring."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
from statistics import mean
from typing import Any

from .bitumen import INDEX_CODE
from .forecast_evaluation import forecast_metrics
from .instruments import INSTRUMENTS, canonical_daily_rows, resolve_instrument


LIVE_FORECAST_WINDOW = 90
LIVE_FORECAST_MODEL_VERSION = "live_forecast_ledger_v1"
DAILY_PUBLICATION_GRACE_DAYS = 4


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _timestamp_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


class ForecastStoreMixin:
    """Store forecasts at issue time and score them when their actual arrives.

    The database receives the original persistence baseline alongside every
    issued endpoint.  Later reports therefore compare a forecast to the price
    that was actually known when it was issued, rather than a current value.
    """

    @staticmethod
    def _live_number(value: Any, label: str, *, required: bool = True) -> float | None:
        if value is None and not required:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be numeric.") from None
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{label} must be greater than zero.")
        return number

    @staticmethod
    def _live_date(value: Any, label: str) -> str:
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except (TypeError, ValueError):
            raise ValueError(f"{label} must use YYYY-MM-DD format.") from None

    def _live_forecast_row(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        for field in (
            "prediction", "baseline_prediction", "lower_bound", "upper_bound", "actual_value",
            "error", "absolute_error", "baseline_error", "baseline_absolute_error",
        ):
            if item.get(field) is not None:
                item[field] = float(item[field])
        for field in ("id", "horizon"):
            if item.get(field) is not None:
                item[field] = int(item[field])
        if item.get("interval_covered") is not None:
            item["interval_covered"] = bool(item["interval_covered"])
        for field in ("origin_date", "target_date", "actual_source_date"):
            item[field] = _date_text(item.get(field))
        for field in ("issued_at", "actual_collected_at", "evaluated_at"):
            item[field] = _timestamp_text(item.get(field))
        return item

    def record_live_forecast(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist one selected endpoint once, without replacing its original issue."""
        product = str(payload.get("product") or "").strip().lower()
        if product not in {"gasoil", "brent", "bitume"}:
            raise ValueError("Unknown product for live forecast.")
        instrument = str(payload.get("instrument") or "").strip()
        if not instrument:
            raise ValueError("Instrument is required for live forecast.")
        resolve_instrument(product, instrument=instrument, index=instrument == "asphalt_index")
        horizon_unit = str(payload.get("horizon_unit") or "").strip()
        if horizon_unit not in {"calendar_days", "months"}:
            raise ValueError("Live forecast horizon unit must be calendar_days or months.")
        if (instrument == "asphalt_index") != (horizon_unit == "months"):
            raise ValueError("The index forecast must use monthly horizons and market prices daily horizons.")
        try:
            horizon = int(payload.get("horizon"))
        except (TypeError, ValueError):
            raise ValueError("Forecast horizon must be an integer.") from None
        if not 1 <= horizon <= 365:
            raise ValueError("Forecast horizon must be from 1 to 365.")
        origin_date = self._live_date(payload.get("origin_date"), "Forecast origin")
        target_date = self._live_date(payload.get("target_date"), "Forecast target")
        if target_date <= origin_date:
            raise ValueError("Forecast target must be after its origin.")
        prediction = self._live_number(payload.get("prediction"), "Forecast prediction")
        baseline_prediction = self._live_number(payload.get("baseline_prediction"), "Forecast baseline")
        lower_bound = self._live_number(payload.get("lower_bound"), "Forecast lower bound", required=False)
        upper_bound = self._live_number(payload.get("upper_bound"), "Forecast upper bound", required=False)
        if (lower_bound is None) != (upper_bound is None):
            raise ValueError("Forecast intervals must include both bounds or neither bound.")
        if lower_bound is not None and not lower_bound <= prediction <= upper_bound:
            raise ValueError("Forecast prediction must fall inside its interval.")
        model = str(payload.get("model") or "").strip()[:64]
        model_version = str(payload.get("model_version") or LIVE_FORECAST_MODEL_VERSION).strip()[:100]
        if not model or not model_version:
            raise ValueError("Forecast model and model version are required.")
        issued_at = self._collected_at_value(
            payload.get("issued_at") or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        identity = (instrument, origin_date, target_date, horizon, model_version)
        values = (
            product, instrument, horizon, horizon_unit, model, model_version, origin_date, target_date,
            self._datetime_value(issued_at), prediction, baseline_prediction, lower_bound, upper_bound,
        )
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                """SELECT * FROM live_forecasts WHERE instrument=? AND origin_date=? AND target_date=?
                   AND horizon=? AND model_version=?""",
                identity,
            ).fetchone()
            if existing:
                return self._live_forecast_row(existing)
            try:
                cursor = conn.execute(
                    """INSERT INTO live_forecasts
                    (product, instrument, horizon, horizon_unit, model, model_version, origin_date, target_date,
                     issued_at, prediction, baseline_prediction, lower_bound, upper_bound)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
            except Exception:
                # A parallel dashboard request can issue the same endpoint. The
                # unique identity preserves the first observed issue time.
                existing = conn.execute(
                    """SELECT * FROM live_forecasts WHERE instrument=? AND origin_date=? AND target_date=?
                       AND horizon=? AND model_version=?""",
                    identity,
                ).fetchone()
                if existing:
                    return self._live_forecast_row(existing)
                raise
            row = conn.execute("SELECT * FROM live_forecasts WHERE id=?", (cursor.lastrowid,)).fetchone()
            return self._live_forecast_row(row)

    def _score_live_forecast(
        self, conn: Any, row: dict[str, Any], actual: dict[str, Any], *, replace: bool = False,
    ) -> bool:
        actual_value = float(actual["value"])
        actual_source_date = self._live_date(actual["source_date"], "Actual source date")
        if (
            row.get("actual_value") is not None
            and float(row["actual_value"]) == actual_value
            and row.get("actual_source_date") == actual_source_date
        ):
            return False
        prediction = float(row["prediction"])
        baseline = float(row["baseline_prediction"])
        error = prediction - actual_value
        baseline_error = baseline - actual_value
        lower, upper = row.get("lower_bound"), row.get("upper_bound")
        covered = None if lower is None or upper is None else int(float(lower) <= actual_value <= float(upper))
        now = self._datetime_value(datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        condition = "id=?" if replace else "id=? AND actual_value IS NULL"
        conn.execute(
            """UPDATE live_forecasts SET actual_value=?, actual_source_date=?, actual_collected_at=?,
                evaluated_at=?, error=?, absolute_error=?, baseline_error=?, baseline_absolute_error=?,
                interval_covered=? WHERE """ + condition,
            (
                actual_value, actual_source_date, self._datetime_value(actual["collected_at"]), now,
                error, abs(error), baseline_error, abs(baseline_error), covered, row["id"],
            ),
        )
        return True

    def _match_live_forecasts_for_actual(
        self, conn: Any, *, product: str, instrument: str, value: float, source_date: str, collected_at: str,
    ) -> int:
        """Score pending forecasts affected by a newly stored actual observation."""
        actual_day = self._live_date(source_date, "Actual source date")
        pending = conn.execute(
            """SELECT * FROM live_forecasts
               WHERE product=? AND instrument=? AND actual_value IS NULL AND target_date<=?""",
            (product, instrument, actual_day),
        ).fetchall()
        matched = 0
        for raw in pending:
            row = self._live_forecast_row(raw)
            target = date.fromisoformat(row["target_date"])
            actual_date = date.fromisoformat(actual_day)
            monthly = row["horizon_unit"] == "months"
            if (monthly and actual_date != target) or (not monthly and actual_date > target + timedelta(days=DAILY_PUBLICATION_GRACE_DAYS)):
                continue
            if self._score_live_forecast(conn, row, {
                "value": value, "source_date": actual_day, "collected_at": collected_at,
            }):
                matched += 1
        return matched

    def reconcile_live_forecasts(self) -> int:
        """Backfill pending scores after startup or a period without collection."""
        with self._lock, self.connect() as conn:
            issued = [self._live_forecast_row(row) for row in conn.execute(
                "SELECT * FROM live_forecasts ORDER BY target_date, id"
            ).fetchall()]
            matched = 0
            for row in issued:
                if row["horizon_unit"] == "months":
                    actual = conn.execute(
                        """SELECT value, source_date, collected_at FROM market_benchmarks
                           WHERE code=? AND product=? AND source_date=?
                           ORDER BY collected_at DESC, id DESC LIMIT 1""",
                        (INDEX_CODE, row["product"], row["target_date"]),
                    ).fetchone()
                    if actual:
                        if self._score_live_forecast(conn, row, {
                            "value": actual["value"], "source_date": _date_text(actual["source_date"]),
                            "collected_at": _timestamp_text(actual["collected_at"]),
                        }, replace=True):
                            matched += 1
                    continue
                target = date.fromisoformat(row["target_date"])
                last_allowed = (target + timedelta(days=DAILY_PUBLICATION_GRACE_DAYS)).isoformat()
                candidates = conn.execute(
                    """SELECT price, source, source_date, collected_at, instrument, product, id
                       FROM price_observations WHERE product=? AND instrument=?
                       AND source_date>=? AND source_date<=?
                       ORDER BY source_date, collected_at, id""",
                    (row["product"], row["instrument"], row["target_date"], last_allowed),
                ).fetchall()
                canonical = canonical_daily_rows([dict(candidate) for candidate in candidates])
                if canonical:
                    actual = canonical[0]
                    if self._score_live_forecast(conn, row, {
                        "value": actual["price"], "source_date": actual["source_date"], "collected_at": actual["collected_at"],
                    }, replace=True):
                        matched += 1
            return matched

    def live_forecast_performance(
        self, window: int = LIVE_FORECAST_WINDOW, *, product: str | None = None, instrument: str | None = None,
        horizon: int | None = None,
    ) -> dict[str, Any]:
        """Return rolling, comparable endpoint performance and recent ledger entries."""
        try:
            window = int(window)
        except (TypeError, ValueError):
            raise ValueError("Forecast performance window must be an integer.") from None
        if not 1 <= window <= 365:
            raise ValueError("Forecast performance window must be from 1 to 365.")
        clauses: list[str] = []
        params: list[Any] = []
        if product:
            if product not in {"gasoil", "brent", "bitume"}:
                raise ValueError("Unknown forecast product.")
            clauses.append("product=?")
            params.append(product)
        if instrument:
            if instrument not in INSTRUMENTS or (product and INSTRUMENTS[instrument]["product"] != product):
                raise ValueError("Forecast instrument is incompatible with the product.")
            clauses.append("instrument=?")
            params.append(instrument)
        if horizon is not None:
            try:
                horizon = int(horizon)
            except (TypeError, ValueError):
                raise ValueError("Forecast horizon must be an integer.") from None
            if not 1 <= horizon <= 365:
                raise ValueError("Forecast horizon must be from 1 to 365.")
            clauses.append("horizon=?")
            params.append(horizon)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            pending_count = conn.execute(
                f"SELECT COUNT(*) AS count FROM live_forecasts{where} AND actual_value IS NULL"
                if where else "SELECT COUNT(*) AS count FROM live_forecasts WHERE actual_value IS NULL", params,
            ).fetchone()["count"]
            evaluated_where = f"{where}{' AND ' if where else ' WHERE '}actual_value IS NOT NULL"
            rows = conn.execute(
                f"SELECT * FROM live_forecasts{evaluated_where} ORDER BY evaluated_at DESC, id DESC LIMIT ?",
                [*params, window],
            ).fetchall()
        records = [self._live_forecast_row(row) for row in reversed(rows)]
        actual = [row["actual_value"] for row in records]
        predicted = [row["prediction"] for row in records]
        baseline = [row["baseline_prediction"] for row in records]
        metrics = forecast_metrics(actual, predicted, baseline)
        coverage = [row["interval_covered"] for row in records if row["interval_covered"] is not None]
        widths = [row["upper_bound"] - row["lower_bound"] for row in records if row["upper_bound"] is not None]
        metrics.update({
            "window": window,
            "pending": int(pending_count),
            "interval_samples": len(coverage),
            "interval_coverage": mean(coverage) if coverage else None,
            "mean_interval_width": mean(widths) if widths else None,
            "latest_evaluated_at": records[-1]["evaluated_at"] if records else None,
        })
        # Keep the API vocabulary explicit: `improvement_over_baseline` is the
        # same MAE skill used by the research protocol, not a price change.
        metrics["improvement_over_baseline"] = metrics["mae_skill_vs_naive"]
        return {"window": window, "horizon": horizon, "metrics": metrics, "records": records}
