"""Append-only provider vintages for point-in-time market research."""

from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any

from .instruments import INSTRUMENTS, canonical_daily_rows


AVAILABILITY_BASES = {
    "provider_timestamp",
    "collection_observed",
    "manual_entry",
    "reconstructed",
}


def _timestamp_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _source_date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


class VintageStoreMixin:
    """Capture a new immutable vintage only when a provider value changes."""

    def _vintage_row(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        for field in ("id", "revision_number", "previous_vintage_id"):
            if item.get(field) is not None:
                item[field] = int(item[field])
        if item.get("value") is not None:
            item["value"] = float(item["value"])
        for field in ("source_date",):
            if item.get(field) is not None:
                item[field] = _source_date_text(item[field])
        for field in ("available_at", "captured_at"):
            item[field] = _timestamp_text(item.get(field))
        return item

    def _capture_provider_vintage(
        self,
        conn: Any,
        *,
        data_kind: str,
        product: str,
        instrument: str,
        source: str,
        source_date: str,
        value: float,
        unit: str,
        currency: str,
        captured_at: Any,
        available_at: Any | None = None,
        availability_basis: str | None = None,
    ) -> dict[str, Any]:
        """Append a revision without altering a prior provider vintage."""
        if data_kind not in {"quote", "index"}:
            raise ValueError("Vintage data kind must be quote or index.")
        if instrument not in INSTRUMENTS or INSTRUMENTS[instrument]["product"] != product:
            raise ValueError("Vintage instrument is incompatible with the product.")
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError("Vintage value must be numeric.") from None
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Vintage value must be greater than zero.")
        source_date = self._source_date_value(source_date)
        captured_text = self._collected_at_value(_timestamp_text(captured_at))
        source_available = available_at is not None
        available_text = self._collected_at_value(_timestamp_text(available_at) if source_available else captured_text)
        default_basis = "provider_timestamp" if source_available else "collection_observed"
        basis = str(availability_basis or default_basis).strip().lower()
        if basis not in AVAILABILITY_BASES:
            raise ValueError("Unknown vintage availability basis.")
        group = (data_kind, product, instrument, source, source_date)
        previous = conn.execute(
            """SELECT * FROM provider_value_vintages
               WHERE data_kind=? AND product=? AND instrument=? AND source=? AND source_date=?
               ORDER BY revision_number DESC, id DESC LIMIT 1""",
            group,
        ).fetchone()
        # Repeated polling of an unchanged value adds no new information. A
        # return to an earlier price still becomes a new vintage because the
        # immediately preceding value differs.
        if previous and float(previous["value"]) == value:
            return self._vintage_row(previous)
        revision_number = int(previous["revision_number"]) + 1 if previous else 1
        cursor = conn.execute(
            """INSERT INTO provider_value_vintages
            (data_kind, product, instrument, source, source_date, value, unit, currency,
             available_at, availability_basis, captured_at, revision_number, previous_vintage_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data_kind, product, instrument, source, source_date, value, unit, currency,
                self._datetime_value(available_text), basis, self._datetime_value(captured_text),
                revision_number, previous["id"] if previous else None,
            ),
        )
        row = conn.execute("SELECT * FROM provider_value_vintages WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self._vintage_row(row)

    def provider_vintages(
        self, *, product: str | None = None, instrument: str | None = None, source: str | None = None,
        data_kind: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return immutable captured provider revisions, newest availability first."""
        clauses: list[str] = []
        params: list[Any] = []
        if product:
            if product not in {"gasoil", "brent", "bitume"}:
                raise ValueError("Unknown vintage product.")
            clauses.append("product=?")
            params.append(product)
        if instrument:
            if instrument not in INSTRUMENTS or (product and INSTRUMENTS[instrument]["product"] != product):
                raise ValueError("Vintage instrument is incompatible with the product.")
            clauses.append("instrument=?")
            params.append(instrument)
        if source:
            clauses.append("source=?")
            params.append(str(source)[:180])
        if data_kind:
            if data_kind not in {"quote", "index"}:
                raise ValueError("Vintage data kind must be quote or index.")
            clauses.append("data_kind=?")
            params.append(data_kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        safe_limit = max(1, min(int(limit), 1000))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM provider_value_vintages{where} ORDER BY available_at DESC, id DESC LIMIT ?",
                [*params, safe_limit],
            ).fetchall()
        return [self._vintage_row(row) for row in rows]

    def point_in_time_observations(
        self, as_of: str, *, product: str | None = None, instrument: str | None = None,
        data_kind: str = "quote",
    ) -> dict[str, Any]:
        """Reconstruct the values the application had observed at ``as_of``.

        This intentionally reads only append-only vintages. It never treats the
        older reconstructed CSV import timestamps as evidence of availability.
        """
        as_of_text = self._collected_at_value(as_of)
        as_of_day = self._source_date_value(as_of_text[:10])
        if data_kind not in {"quote", "index"}:
            raise ValueError("Vintage data kind must be quote or index.")
        # A provider may report a market timestamp that predates the request
        # that delivered it to us.  A genuine system-as-of snapshot can use
        # the value only after this application captured that response.
        clauses = ["data_kind=?", "captured_at<=?", "source_date<=?"]
        params: list[Any] = [data_kind, self._datetime_value(as_of_text), as_of_day]
        if product:
            if product not in {"gasoil", "brent", "bitume"}:
                raise ValueError("Unknown vintage product.")
            clauses.append("product=?")
            params.append(product)
        if instrument:
            if instrument not in INSTRUMENTS or (product and INSTRUMENTS[instrument]["product"] != product):
                raise ValueError("Vintage instrument is incompatible with the product.")
            clauses.append("instrument=?")
            params.append(instrument)
        where = " AND ".join(clauses)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY product, instrument, source, source_date
                        ORDER BY revision_number DESC, id DESC
                    ) AS revision_rank
                    FROM provider_value_vintages WHERE {where}
                ) viable WHERE revision_rank=1
                ORDER BY source_date, captured_at, id""",
                params,
            ).fetchall()
        vintages = [self._vintage_row(row) for row in rows]
        observations = canonical_daily_rows([
            {
                "id": row["id"], "product": row["product"], "instrument": row["instrument"],
                "source": row["source"], "source_date": row["source_date"], "collected_at": row["captured_at"],
                "price": row["value"], "unit": row["unit"], "currency": row["currency"],
                "data_kind": row["data_kind"],
                "available_at": row["available_at"], "availability_basis": row["availability_basis"],
                "revision_number": row["revision_number"],
            }
            for row in vintages
        ])
        return {"as_of": as_of_text, "items": observations, "vintages": vintages}
