"""Durable schedules and request budgets shared by workers and manual requests.

UTC epoch seconds avoid backend-specific date conversions. Quota reservations
count real HTTP attempts, including failed requests and fallback requests.
"""
from __future__ import annotations

from uuid import uuid4

PROVIDERS = ("fred", "eia", "alpha_vantage", "bls", "yahoo", "demo")


class CollectionStoreMixin:
    def collection_state(self, provider: str) -> dict:
        with self.connect() as conn:
            return dict(conn.execute("SELECT * FROM provider_collection_state WHERE provider=?", (provider,)).fetchone())

    def claim_collection(self, provider: str, now: float, force: bool = False) -> str | None:
        """An atomic lease also prevents concurrent application processes."""
        token = uuid4().hex
        with self._lock, self.connect() as conn:
            changed = conn.execute(
                """UPDATE provider_collection_state SET lease_token=?, lease_until=?, last_started=?
                   WHERE provider=? AND lease_until<=? AND
                   (next_due<=? OR (?=1 AND failures=0))""",
                (token, now + 600, now, provider, now, now, int(force)),
            ).rowcount
        return token if changed else None

    def finish_collection(self, provider: str, token: str, *, now: float, next_due: float,
                          failures: int, status: str, error: str = "") -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """UPDATE provider_collection_state SET lease_token='', lease_until=0,
                   last_finished=?, next_due=?, failures=?, status=?, last_error=?
                   WHERE provider=? AND lease_token=?""",
                (now, next_due, failures, status, error[:500], provider, token),
            )

    def request_budget(self, provider: str, now: float, limit: int = 0) -> dict:
        with self.connect() as conn:
            state = dict(conn.execute("SELECT * FROM provider_request_limits WHERE provider=?", (provider,)).fetchone())
            if limit:
                rows = conn.execute(
                    "SELECT attempted_at FROM provider_requests WHERE provider=? AND attempted_at>? ORDER BY attempted_at",
                    (provider, now - 86400),
                ).fetchall()
                used = len(rows)
            else:
                used = int(conn.execute(
                    "SELECT COUNT(*) AS used FROM provider_requests WHERE provider=? AND attempted_at>?",
                    (provider, now - 86400),
                ).fetchone()["used"])
        retry_at = float(state["blocked_until"])
        if limit and used >= limit:
            # For a reduced quota, wait until enough old attempts have expired.
            retry_at = max(retry_at, float(rows[used - limit]["attempted_at"]) + 86400)
        return {"used": used, "limit": limit or None, "retry_at": retry_at,
                "reason": state["reason"] if float(state["blocked_until"]) > now else "Quota de requêtes sur 24 heures atteint."}

    def reserve_provider_request(self, provider: str, now: float, limit: int = 0) -> dict:
        with self._lock, self.connect() as conn:
            # UPDATE obtains a write lock before the quota read on both backends.
            # In MySQL, subsequent FOR UPDATE reads see committed concurrent usage.
            conn.execute("UPDATE provider_request_limits SET serial=serial+1 WHERE provider=?", (provider,))
            suffix = " FOR UPDATE" if hasattr(self, "settings") else ""
            state = dict(conn.execute("SELECT * FROM provider_request_limits WHERE provider=?" + suffix, (provider,)).fetchone())
            conn.execute("DELETE FROM provider_requests WHERE provider=? AND attempted_at<=?", (provider, now - 86400))
            rows = conn.execute(
                "SELECT attempted_at FROM provider_requests WHERE provider=? ORDER BY attempted_at" + suffix,
                (provider,),
            ).fetchall() if limit else []
            retry_at = float(state["blocked_until"])
            if limit and len(rows) >= limit:
                retry_at = max(retry_at, float(rows[len(rows) - limit]["attempted_at"]) + 86400)
            if retry_at > now:
                return {"allowed": False, "retry_at": retry_at,
                        "reason": state["reason"] if float(state["blocked_until"]) > now else "Quota de requêtes sur 24 heures atteint."}
            conn.execute("INSERT INTO provider_requests(provider,attempted_at) VALUES(?,?)", (provider, now))
            return {"allowed": True, "retry_at": 0, "reason": ""}

    def defer_provider_requests(self, provider: str, until: float, reason: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """UPDATE provider_request_limits SET blocked_until=CASE WHEN blocked_until>? THEN blocked_until ELSE ? END,
                   reason=? WHERE provider=?""", (until, until, reason[:500], provider),
            )
