"""Controlled promotion, shadowing, and rollback for live forecast models.

The forecasting service deliberately contains only the small, inspectable
baseline models that can run without the research stack.  This store turns
those models into versioned candidates: one is live, the alternatives issue
the same endpoint forecasts in shadow, and a switch is recorded only after
paired live outcomes meet the published criteria.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any

from .forecast_store import LIVE_FORECAST_MODEL_VERSION
from .instruments import INSTRUMENTS


MODEL_LIFECYCLE_VERSION = "controlled_lifecycle_v1"
SHADOW_MIN_SAMPLES = 30
PROMOTION_MIN_IMPROVEMENT = 0.05
ROLLBACK_MAX_DEGRADATION = 0.10


def candidate_version(model: str, implementation_version: str) -> str:
    """Return the stable, auditable identity used by the forecast ledger."""
    return f"{MODEL_LIFECYCLE_VERSION}:{model}:{implementation_version}"


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _metric(value: Any) -> float | None:
    return round(float(value), 6) if value is not None else None


class ModelLifecycleStoreMixin:
    """Persistence methods shared by the SQLite and MySQL backends."""

    def _lifecycle_now(self) -> str:
        return self._datetime_value(datetime.now(timezone.utc).replace(microsecond=0).isoformat())

    @staticmethod
    def _candidate_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        for field in ("created_at", "updated_at", "promoted_at", "rolled_back_at"):
            item[field] = _timestamp(item.get(field))
        item["validation_mae"] = _metric(item.get("validation_mae"))
        item["calibration_coverage"] = _metric(item.get("calibration_coverage"))
        item["promotion_min_improvement"] = _metric(item.get("promotion_min_improvement"))
        item["rollback_max_degradation"] = _metric(item.get("rollback_max_degradation"))
        item["validation_samples"] = int(item.get("validation_samples") or 0)
        item["shadow_min_samples"] = int(item.get("shadow_min_samples") or SHADOW_MIN_SAMPLES)
        item["forecast_model_version"] = f"{item['candidate_version']}:{LIVE_FORECAST_MODEL_VERSION}"
        return item

    @staticmethod
    def _event_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["created_at"] = _timestamp(item.get("created_at"))
        for field in ("samples",):
            item[field] = int(item[field]) if item.get(field) is not None else None
        for field in ("production_mae", "candidate_mae", "baseline_mae", "improvement", "interval_coverage"):
            item[field] = _metric(item.get(field))
        return item

    @staticmethod
    def _validate_scope(product: str, instrument: str, horizon: int, horizon_unit: str) -> None:
        if product not in {"gasoil", "brent", "bitume"}:
            raise ValueError("Unknown forecast product.")
        if instrument not in INSTRUMENTS or INSTRUMENTS[instrument]["product"] != product:
            raise ValueError("Forecast instrument is incompatible with the product.")
        if int(horizon) < 1:
            raise ValueError("Forecast horizon must be positive.")
        if horizon_unit not in {"calendar_days", "months"}:
            raise ValueError("Unknown forecast horizon unit.")

    def _lifecycle_event(
        self, conn: Any, candidate_id: int, event_type: str, *, from_status: str | None = None,
        to_status: str | None = None, reason: str = "", score: dict[str, Any] | None = None,
    ) -> None:
        score = score or {}
        conn.execute(
            """INSERT INTO forecast_model_events(
                candidate_id, event_type, from_status, to_status, reason, samples,
                production_mae, candidate_mae, baseline_mae, improvement, interval_coverage, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                candidate_id, event_type, from_status, to_status, reason,
                score.get("samples"), score.get("production_mae"), score.get("candidate_mae"),
                score.get("baseline_mae"), score.get("improvement"), score.get("interval_coverage"),
                self._lifecycle_now(),
            ),
        )

    def _candidate_records(
        self, conn: Any, *, product: str | None = None, instrument: str | None = None,
        horizon: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if product:
            clauses.append("product=?")
            params.append(product)
        if instrument:
            clauses.append("instrument=?")
            params.append(instrument)
        if horizon is not None:
            clauses.append("horizon=?")
            params.append(int(horizon))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM forecast_model_candidates{where} "
            "ORDER BY product, instrument, horizon, CASE status WHEN 'production' THEN 0 WHEN 'shadow' THEN 1 ELSE 2 END, id",
            params,
        ).fetchall()
        return [self._candidate_row(row) for row in rows]

    def prepare_model_lifecycle(
        self, *, product: str, instrument: str, horizon: int, horizon_unit: str,
        candidates: list[dict[str, Any]], preferred_model: str,
    ) -> dict[str, Any]:
        """Register fresh candidates and select the existing controlled live model.

        ``preferred_model`` is consulted only when a scope has no production
        candidate yet.  Subsequent evaluator changes never switch the live
        model directly; they are measured in shadow first.
        """
        self._validate_scope(product, instrument, horizon, horizon_unit)
        if not candidates:
            raise ValueError("At least one model candidate is required.")
        normalised: list[dict[str, Any]] = []
        for raw in candidates:
            model = str(raw.get("model") or "").strip().lower()
            implementation_version = str(raw.get("model_version") or "").strip()
            if model not in {"naive", "holt"} or not implementation_version:
                raise ValueError("Only versioned naive and Holt candidates can enter the live lifecycle.")
            normalised.append({
                "model": model,
                "model_version": implementation_version,
                "candidate_version": candidate_version(model, implementation_version),
                "validation_mae": raw.get("validation_mae"),
                "validation_samples": int(raw.get("validation_samples") or 0),
                "calibration_coverage": raw.get("calibration_coverage"),
            })
        if preferred_model not in {candidate["model"] for candidate in normalised}:
            preferred_model = "naive" if any(candidate["model"] == "naive" for candidate in normalised) else normalised[0]["model"]

        with self._lock, self.connect() as conn:
            existing = self._candidate_records(conn, product=product, instrument=instrument, horizon=horizon)
            by_version = {row["candidate_version"]: row for row in existing}
            now = self._lifecycle_now()
            for candidate in normalised:
                previous = by_version.get(candidate["candidate_version"])
                if previous:
                    conn.execute(
                        """UPDATE forecast_model_candidates
                        SET validation_mae=?, validation_samples=?, calibration_coverage=?, updated_at=?
                        WHERE id=?""",
                        (candidate["validation_mae"], candidate["validation_samples"], candidate["calibration_coverage"], now, previous["id"]),
                    )
                else:
                    cursor = conn.execute(
                        """INSERT INTO forecast_model_candidates(
                            product, instrument, horizon, horizon_unit, model, implementation_version,
                            candidate_version, status, validation_mae, validation_samples, calibration_coverage,
                            shadow_min_samples, promotion_min_improvement, rollback_max_degradation,
                            created_at, updated_at, reason
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            product, instrument, int(horizon), horizon_unit, candidate["model"], candidate["model_version"],
                            candidate["candidate_version"], "candidate", candidate["validation_mae"],
                            candidate["validation_samples"], candidate["calibration_coverage"],
                            SHADOW_MIN_SAMPLES, PROMOTION_MIN_IMPROVEMENT, ROLLBACK_MAX_DEGRADATION,
                            now, now, "Registered from fresh chronological evaluation.",
                        ),
                    )
                    candidate["id"] = cursor.lastrowid
                    self._lifecycle_event(conn, candidate["id"], "registered", to_status="candidate",
                                          reason="Registered from fresh chronological evaluation.")

            records = self._candidate_records(conn, product=product, instrument=instrument, horizon=horizon)
            production = next((row for row in records if row["status"] == "production"), None)
            if production is None:
                production = next(row for row in records if row["model"] == preferred_model)
                conn.execute(
                    """UPDATE forecast_model_candidates
                    SET status='production', promoted_at=?, updated_at=?, reason=? WHERE id=?""",
                    (now, now, "Initial production candidate follows chronological validation selection.", production["id"]),
                )
                self._lifecycle_event(
                    conn, production["id"], "registered_production", from_status=production["status"], to_status="production",
                    reason="Initial production candidate follows chronological validation selection.",
                )
            records = self._candidate_records(conn, product=product, instrument=instrument, horizon=horizon)
            production = next(row for row in records if row["status"] == "production")
            for row in records:
                if row["id"] == production["id"] or row["status"] != "candidate":
                    continue
                conn.execute(
                    "UPDATE forecast_model_candidates SET status='shadow', updated_at=?, reason=? WHERE id=?",
                    (now, "Running beside the production model until paired live evidence is sufficient.", row["id"]),
                )
                self._lifecycle_event(
                    conn, row["id"], "shadow_started", from_status="candidate", to_status="shadow",
                    reason="Running beside the production model until paired live evidence is sufficient.",
                )
            records = self._candidate_records(conn, product=product, instrument=instrument, horizon=horizon)
            production = next(row for row in records if row["status"] == "production")
            shadows = [row for row in records if row["status"] == "shadow"]
            return {"production": production, "shadows": shadows, "candidates": records, "criteria": self.lifecycle_criteria()}

    @staticmethod
    def lifecycle_criteria() -> dict[str, Any]:
        return {
            "shadow_min_samples": SHADOW_MIN_SAMPLES,
            "promotion_min_improvement": PROMOTION_MIN_IMPROVEMENT,
            "rollback_max_degradation": ROLLBACK_MAX_DEGRADATION,
            "comparison": "paired issued forecasts for the same instrument, origin, target date, and horizon",
            "promotion": "Shadow MAE must improve on the production MAE by at least 5% across at least 30 paired actuals and must not be worse than persistence.",
            "rollback": "A non-persistence production model is rolled back to persistence after at least 30 live actuals when its MAE is more than 10% worse than its issued last-known-price baseline.",
        }

    def active_forecast_model_version(self, *, product: str, instrument: str, horizon: int) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM forecast_model_candidates
                WHERE product=? AND instrument=? AND horizon=? AND status='production'
                ORDER BY promoted_at DESC, id DESC LIMIT 1""",
                (product, instrument, int(horizon)),
            ).fetchone()
        return self._candidate_row(row)["forecast_model_version"] if row else None

    def _paired_scores(self, conn: Any, production: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        rows = conn.execute(
            """SELECT shadow.actual_value AS actual_value, shadow.prediction AS candidate_prediction,
                      production.prediction AS production_prediction,
                      shadow.baseline_prediction AS baseline_prediction,
                      shadow.interval_covered AS interval_covered
               FROM live_forecasts AS shadow
               INNER JOIN live_forecasts AS production
                 ON production.instrument=shadow.instrument
                AND production.origin_date=shadow.origin_date
                AND production.target_date=shadow.target_date
                AND production.horizon=shadow.horizon
               WHERE shadow.model_version=? AND production.model_version=?
                 AND shadow.actual_value IS NOT NULL AND production.actual_value IS NOT NULL
               ORDER BY shadow.evaluated_at DESC, shadow.id DESC LIMIT ?""",
            (candidate["forecast_model_version"], production["forecast_model_version"], SHADOW_MIN_SAMPLES * 3),
        ).fetchall()
        entries = [dict(row) for row in rows]
        actual = [float(row["actual_value"]) for row in entries]
        candidate_errors = [abs(float(row["actual_value"]) - float(row["candidate_prediction"])) for row in entries]
        production_errors = [abs(float(row["actual_value"]) - float(row["production_prediction"])) for row in entries]
        baseline_errors = [abs(float(row["actual_value"]) - float(row["baseline_prediction"])) for row in entries]
        candidate_mae = mean(candidate_errors) if candidate_errors else None
        production_mae = mean(production_errors) if production_errors else None
        baseline_mae = mean(baseline_errors) if baseline_errors else None
        coverage = [int(row["interval_covered"]) for row in entries if row["interval_covered"] is not None]
        improvement = ((production_mae - candidate_mae) / production_mae) if production_mae not in (None, 0) else None
        return {
            "samples": len(actual), "production_mae": production_mae, "candidate_mae": candidate_mae,
            "baseline_mae": baseline_mae, "improvement": improvement,
            "interval_coverage": mean(coverage) if coverage else None,
        }

    def _production_scores(self, conn: Any, production: dict[str, Any]) -> dict[str, Any]:
        rows = conn.execute(
            """SELECT actual_value, prediction, baseline_prediction, interval_covered
               FROM live_forecasts WHERE model_version=? AND actual_value IS NOT NULL
               ORDER BY evaluated_at DESC, id DESC LIMIT ?""",
            (production["forecast_model_version"], SHADOW_MIN_SAMPLES * 3),
        ).fetchall()
        entries = [dict(row) for row in rows]
        errors = [abs(float(row["actual_value"]) - float(row["prediction"])) for row in entries]
        baseline = [abs(float(row["actual_value"]) - float(row["baseline_prediction"])) for row in entries]
        coverage = [int(row["interval_covered"]) for row in entries if row["interval_covered"] is not None]
        return {
            "samples": len(entries), "production_mae": mean(errors) if errors else None,
            "baseline_mae": mean(baseline) if baseline else None,
            "interval_coverage": mean(coverage) if coverage else None,
        }

    def _switch_production(
        self, conn: Any, *, outgoing: dict[str, Any], incoming: dict[str, Any],
        outgoing_status: str, event_type: str, reason: str, score: dict[str, Any],
    ) -> None:
        now = self._lifecycle_now()
        conn.execute(
            """UPDATE forecast_model_candidates
            SET status=?, rolled_back_at=CASE WHEN ?='rolled_back' THEN ? ELSE rolled_back_at END,
                updated_at=?, reason=? WHERE id=?""",
            (outgoing_status, outgoing_status, now, now, reason, outgoing["id"]),
        )
        conn.execute(
            """UPDATE forecast_model_candidates
            SET status='production', promoted_at=?, updated_at=?, reason=? WHERE id=?""",
            (now, now, reason, incoming["id"]),
        )
        self._lifecycle_event(
            conn, outgoing["id"], event_type, from_status="production", to_status=outgoing_status,
            reason=reason, score=score,
        )
        self._lifecycle_event(
            conn, incoming["id"], "promoted" if event_type == "promoted" else "rollback_promoted",
            from_status=incoming["status"], to_status="production", reason=reason, score=score,
        )

    def evaluate_model_lifecycle(self) -> dict[str, Any]:
        """Evaluate fresh resolved endpoints and apply deterministic lifecycle rules."""
        promoted = rolled_back = 0
        evaluated = 0
        with self._lock, self.connect() as conn:
            productions = [row for row in self._candidate_records(conn) if row["status"] == "production"]
            for production in productions:
                scope = {"product": production["product"], "instrument": production["instrument"], "horizon": production["horizon"]}
                records = self._candidate_records(conn, **scope)
                production = next(row for row in records if row["status"] == "production")
                shadows = [row for row in records if row["status"] == "shadow"]
                production_score = self._production_scores(conn, production)
                evaluated += 1
                min_samples = int(production["shadow_min_samples"])
                baseline_mae = production_score["baseline_mae"]
                is_degraded = (
                    production["model"] != "naive" and production_score["samples"] >= min_samples
                    and production_score["production_mae"] is not None and baseline_mae is not None
                    and production_score["production_mae"] > baseline_mae * (1 + float(production["rollback_max_degradation"]))
                )
                fallback = next((row for row in shadows if row["model"] == "naive"), None)
                if is_degraded and fallback is not None:
                    reason = (
                        f"Rollback: live MAE {production_score['production_mae']:.6g} exceeded the issued persistence "
                        f"baseline MAE {baseline_mae:.6g} by more than {production['rollback_max_degradation']:.0%}."
                    )
                    self._switch_production(
                        conn, outgoing=production, incoming=fallback, outgoing_status="rolled_back",
                        event_type="rolled_back", reason=reason, score=production_score,
                    )
                    rolled_back += 1
                    continue
                for shadow in shadows:
                    score = self._paired_scores(conn, production, shadow)
                    min_samples = int(shadow["shadow_min_samples"])
                    improvement = score["improvement"]
                    promotes = (
                        score["samples"] >= min_samples and improvement is not None
                        and improvement >= float(shadow["promotion_min_improvement"])
                        and (score["baseline_mae"] is None or score["candidate_mae"] <= score["baseline_mae"])
                    )
                    if not promotes:
                        continue
                    reason = (
                        f"Promoted after {score['samples']} paired live actuals: shadow MAE "
                        f"{score['candidate_mae']:.6g} improved on production MAE {score['production_mae']:.6g} "
                        f"by {improvement:.1%}."
                    )
                    self._switch_production(
                        conn, outgoing=production, incoming=shadow, outgoing_status="shadow",
                        event_type="promoted", reason=reason, score=score,
                    )
                    promoted += 1
                    break
        return {"evaluated_scopes": evaluated, "promoted": promoted, "rolled_back": rolled_back, "criteria": self.lifecycle_criteria()}

    def model_lifecycle(
        self, *, product: str | None = None, instrument: str | None = None, horizon: int | None = None,
        event_limit: int = 100,
    ) -> dict[str, Any]:
        if product and product not in {"gasoil", "brent", "bitume"}:
            raise ValueError("Unknown forecast product.")
        if instrument and (instrument not in INSTRUMENTS or (product and INSTRUMENTS[instrument]["product"] != product)):
            raise ValueError("Forecast instrument is incompatible with the product.")
        if horizon is not None and int(horizon) < 1:
            raise ValueError("Forecast horizon must be positive.")
        event_limit = max(1, min(1000, int(event_limit)))
        with self.connect() as conn:
            candidates = self._candidate_records(conn, product=product, instrument=instrument, horizon=horizon)
            candidate_ids = [row["id"] for row in candidates]
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                rows = conn.execute(
                    f"SELECT event.*, candidate.product, candidate.instrument, candidate.horizon, candidate.model, candidate.candidate_version "
                    f"FROM forecast_model_events AS event INNER JOIN forecast_model_candidates AS candidate ON candidate.id=event.candidate_id "
                    f"WHERE event.candidate_id IN ({placeholders}) ORDER BY event.id DESC LIMIT ?",
                    [*candidate_ids, event_limit],
                ).fetchall()
            else:
                rows = []
        return {"criteria": self.lifecycle_criteria(), "candidates": candidates, "events": [self._event_row(row) for row in rows]}
