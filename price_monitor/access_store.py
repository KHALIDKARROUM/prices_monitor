"""Individual credentials, revocable sessions and transactional action audits."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

from .config import SESSION_HOURS, SESSION_IDLE_MINUTES

PASSWORDS = PasswordHasher()
_dummy_hash = PASSWORDS.hash(secrets.token_urlsafe(32))
_transaction = ContextVar("team_audit_transaction", default=None)
ROLES = {"viewer": 1, "editor": 2, "admin": 3}
USER_COLUMNS = "id, username, full_name, role, active, version, created_at, updated_at"


class LoginThrottled(ValueError):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def username(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.@+\-]{2,79}", value.strip().lower()):
        raise ValueError("Username: 3 to 80 characters; letters, numbers, . _ @ + or -.")
    return value.strip().lower()


def password_hash(value) -> str:
    if not isinstance(value, str) or not 12 <= len(value) <= 128:
        raise ValueError("Password must contain between 12 and 128 characters.")
    return PASSWORDS.hash(value)


def public_user(row) -> dict:
    result = {key: row[key] for key in USER_COLUMNS.split(", ")}
    result["active"] = bool(result["active"])
    for key in ("created_at", "updated_at"):
        result[key] = datetime.fromtimestamp(float(result[key]), timezone.utc).isoformat()
    return result


class BorrowedConnection:
    """Nested persistence methods share the outer audited transaction."""
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *args):
        return False


def borrowed_connection(database):
    current = _transaction.get()
    return BorrowedConnection(current[1]) if current and current[0] is database else None


class AccessStoreMixin:
    def _audit_row(self, conn, actor, action, resource, resource_id="", *, request_id="", client_ip="", outcome="success", details=None):
        actor = actor or {"id": None, "username": "anonymous"}
        conn.execute("""INSERT INTO audit_events(occurred_at, actor_id, actor, action, resource, resource_id,
                     request_id, client_ip, outcome, details_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (time.time(), actor.get("id"), actor.get("username", "anonymous")[:100], action[:80], resource[:80],
                      str(resource_id)[:80], request_id[:64], client_ip[:64], outcome[:32],
                      json.dumps(details or {}, ensure_ascii=False, allow_nan=False)))

    def audit_action(self, actor, action, resource, resource_id="", **context):
        with self._lock, self.connect() as conn:
            self._audit_row(conn, actor, action, resource, resource_id, **context)

    def audited_write(self, actor, action, resource, operation, resource_id="", **context):
        """Business mutation and its audit entry either both commit or roll back."""
        with self._lock, self.connect() as conn:
            token = _transaction.set((self, conn))
            try:
                result = operation()
                identifier = resource_id or (result.get("id", "") if isinstance(result, dict) else "")
                self._audit_row(conn, actor, action, resource, identifier, **context)
                return result
            finally:
                _transaction.reset(token)

    def audit_events(self, page=1, page_size=50):
        page, page_size = max(1, int(page)), max(1, min(100, int(page_size)))
        with self.connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) AS count FROM audit_events").fetchone()["count"])
            rows = conn.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ? OFFSET ?",
                                (page_size, (page - 1) * page_size)).fetchall()
        result = []
        for raw in rows:
            row = dict(raw)
            row["occurred_at"] = datetime.fromtimestamp(float(row["occurred_at"]), timezone.utc).isoformat()
            row["details"] = json.loads(row.pop("details_json"))
            result.append(row)
        return {"events": result, "total": total, "page": page, "pages": max(1, (total + page_size - 1) // page_size)}

    def team_users(self):
        with self.connect() as conn:
            return [public_user(dict(row)) for row in conn.execute(f"SELECT {USER_COLUMNS} FROM team_users ORDER BY username").fetchall()]

    def active_admin_count(self):
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS count FROM team_users WHERE role='admin' AND active=1").fetchone()["count"])

    @staticmethod
    def _security_lock(conn):
        conn.execute("UPDATE security_guard SET serial=serial+1 WHERE id=1")

    def create_team_user(self, payload, *, bootstrap=False):
        name = username(payload.get("username"))
        full_name = payload.get("full_name", name)
        role = payload.get("role", "viewer")
        if not isinstance(full_name, str) or not full_name.strip() or len(full_name) > 100 or not isinstance(role, str) or role not in ROLES:
            raise ValueError("Invalid name or role.")
        encoded = password_hash(payload.get("password"))
        now = time.time()
        with self._lock, self.connect() as conn:
            self._security_lock(conn)
            count = int(conn.execute("SELECT COUNT(*) AS count FROM team_users").fetchone()["count"])
            if bootstrap and count:
                raise ValueError("An account already exists. Use an existing administrator.")
            if not count and role != "admin":
                raise ValueError("The first account must be an administrator.")
            if conn.execute("SELECT id FROM team_users WHERE username=?", (name,)).fetchone():
                raise ValueError("This username is already in use.")
            conn.execute("""INSERT INTO team_users(username,full_name,password_hash,role,created_at,updated_at)
                         VALUES(?,?,?,?,?,?)""", (name, full_name.strip(), encoded, role, now, now))
            row = conn.execute(f"SELECT {USER_COLUMNS} FROM team_users WHERE username=?", (name,)).fetchone()
            return public_user(dict(row))

    def update_team_user(self, user_id, payload):
        encoded = password_hash(payload["password"]) if payload.get("password") else None
        with self._lock, self.connect() as conn:
            self._security_lock(conn)
            suffix = " FOR UPDATE" if hasattr(self, "settings") else ""
            raw = conn.execute("SELECT * FROM team_users WHERE id=?" + suffix, (user_id,)).fetchone()
            if raw is None:
                raise ValueError("Compte introuvable.")
            current = dict(raw)
            if type(payload.get("version")) is not int or payload["version"] != current["version"]:
                raise ValueError("The account has changed. Reload the list before editing it.")
            role, active, name = payload.get("role", current["role"]), payload.get("active", bool(current["active"])), payload.get("full_name", current["full_name"])
            if not isinstance(role, str) or role not in ROLES or not isinstance(active, bool) or not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError("Invalid name, role, or status.")
            admins = int(conn.execute("SELECT COUNT(*) AS count FROM team_users WHERE role='admin' AND active=1").fetchone()["count"])
            if current["role"] == "admin" and current["active"] and admins <= 1 and (role != "admin" or not active):
                raise ValueError("Conservez au moins un administrateur actif.")
            conn.execute("""UPDATE team_users SET full_name=?, role=?, active=?, password_hash=?, version=version+1,
                         updated_at=? WHERE id=?""", (name.strip(), role, int(active), encoded or current["password_hash"], time.time(), user_id))
            if encoded or not active:
                self._revoke_user_credentials(conn, user_id)
            return public_user(dict(conn.execute(f"SELECT {USER_COLUMNS} FROM team_users WHERE id=?", (user_id,)).fetchone()))

    @staticmethod
    def _revoke_user_credentials(conn, user_id):
        conn.execute("DELETE FROM team_sessions WHERE user_id=?", (user_id,))
        conn.execute("UPDATE team_api_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (time.time(), user_id))

    def login(self, name, password, client_ip, request_id="", *, now=None):
        now = time.time() if now is None else now
        if not isinstance(name, str) or not isinstance(password, str) or len(name) > 80 or len(password) > 128:
            raise ValueError("Identifiant ou mot de passe incorrect.")
        name = name.strip().lower()
        client_hash, name_hash = digest(client_ip), digest(name)
        with self._lock, self.connect() as conn:
            self._security_lock(conn)
            conn.execute("DELETE FROM auth_attempts WHERE attempted_at<=?", (now - 900,))
            count = conn.execute("""SELECT SUM(CASE WHEN client_hash=? THEN 1 ELSE 0 END) AS clients,
                                SUM(CASE WHEN username_hash=? THEN 1 ELSE 0 END) AS names FROM auth_attempts""",
                                (client_hash, name_hash)).fetchone()
            if int(count["clients"] or 0) >= 30 or int(count["names"] or 0) >= 10:
                raise LoginThrottled("Too many attempts. Try again in 15 minutes.")
            attempt = conn.execute("INSERT INTO auth_attempts(client_hash,username_hash,attempted_at) VALUES(?,?,?)", (client_hash, name_hash, now)).lastrowid
            raw = conn.execute("SELECT * FROM team_users WHERE username=?", (name,)).fetchone()
            row = dict(raw) if raw else None
        valid = False
        try:
            valid = PASSWORDS.verify(row["password_hash"] if row else _dummy_hash, password)
        except (VerificationError, InvalidHashError):
            pass
        with self._lock, self.connect() as conn:
            # Recheck after password verification to respect concurrent resets.
            self._security_lock(conn)
            latest = conn.execute("SELECT * FROM team_users WHERE username=?", (name,)).fetchone()
            valid = valid and row and latest and latest["active"] and latest["password_hash"] == row["password_hash"]
            actor = {"id": row["id"], "username": name} if row else None
            if not valid:
                self._audit_row(conn, actor, "login.failed", "account", request_id=request_id, client_ip=client_ip, outcome="denied")
            else:
                conn.execute("DELETE FROM auth_attempts WHERE id=?", (attempt,))
                if PASSWORDS.check_needs_rehash(row["password_hash"]):
                    conn.execute("UPDATE team_users SET password_hash=? WHERE id=?", (PASSWORDS.hash(password), row["id"]))
                session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                conn.execute("DELETE FROM team_sessions WHERE expires_at<=? OR last_seen_at<=?", (now, now - SESSION_IDLE_MINUTES * 60))
                conn.execute("""INSERT INTO team_sessions(token_hash,user_id,csrf_token,created_at,expires_at,last_seen_at)
                             VALUES(?,?,?,?,?,?)""", (digest(session), row["id"], csrf, now, now + SESSION_HOURS * 3600, now))
                self._audit_row(conn, actor, "login.succeeded", "account", row["id"], request_id=request_id, client_ip=client_ip)
                result = {"user": public_user(dict(latest)), "session": session, "csrf_token": csrf}
        if not valid:
            raise ValueError("Identifiant ou mot de passe incorrect.")
        return result

    def authenticate_session(self, value, *, now=None):
        now = time.time() if now is None else now
        if not value or len(value) > 100:
            return None
        with self._lock, self.connect() as conn:
            raw = conn.execute("""SELECT u.*, s.csrf_token, s.token_hash FROM team_sessions s JOIN team_users u ON u.id=s.user_id
                                WHERE s.token_hash=? AND s.expires_at>? AND s.last_seen_at>? AND u.active=1""",
                                (digest(value), now, now - SESSION_IDLE_MINUTES * 60)).fetchone()
            if not raw:
                return None
            conn.execute("UPDATE team_sessions SET last_seen_at=? WHERE token_hash=?", (now, digest(value)))
            return dict(raw, kind="session")

    def logout(self, actor, request_id="", client_ip=""):
        with self._lock, self.connect() as conn:
            conn.execute("DELETE FROM team_sessions WHERE token_hash=?", (actor.get("token_hash", ""),))
            self._audit_row(conn, actor, "logout", "account", actor.get("id", ""), request_id=request_id, client_ip=client_ip)

    def change_password(self, user_id, current_password, new_password):
        if not isinstance(current_password, str) or len(current_password) > 128:
            raise ValueError("Mot de passe actuel incorrect.")
        encoded = password_hash(new_password)
        with self._lock, self.connect() as conn:
            self._security_lock(conn)
            current = conn.execute("SELECT password_hash FROM team_users WHERE id=? AND active=1", (user_id,)).fetchone()
            try:
                if not current or not PASSWORDS.verify(current["password_hash"], current_password):
                    raise ValueError
            except (VerificationError, InvalidHashError, TypeError, ValueError):
                raise ValueError("Mot de passe actuel incorrect.") from None
            conn.execute("UPDATE team_users SET password_hash=?,version=version+1,updated_at=? WHERE id=?", (encoded, time.time(), user_id))
            self._revoke_user_credentials(conn, user_id)
            return {"id": user_id}

    def create_user_token(self, user_id, label, days=30):
        if not isinstance(label, str) or not label.strip() or len(label) > 100 or type(days) is not int or not 1 <= days <= 365:
            raise ValueError("A label and a duration of 1 to 365 days are required.")
        token, now = "pm_" + secrets.token_urlsafe(32), time.time()
        with self._lock, self.connect() as conn:
            if not conn.execute("SELECT id FROM team_users WHERE id=? AND active=1", (user_id,)).fetchone():
                raise ValueError("Compte actif requis.")
            cursor = conn.execute("""INSERT INTO team_api_tokens(user_id,token_hash,label,created_at,expires_at)
                                  VALUES(?,?,?,?,?)""", (user_id, digest(token), label.strip(), now, now + days * 86400))
            return {"id": int(cursor.lastrowid), "token": token}

    def user_tokens(self, user_id):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT id,label,created_at,expires_at,last_used_at,revoked_at FROM team_api_tokens WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()]

    def revoke_user_token(self, user_id, token_id):
        with self._lock, self.connect() as conn:
            changed = conn.execute("UPDATE team_api_tokens SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL", (time.time(), token_id, user_id)).rowcount
            if not changed:
                raise ValueError("Token not found or already revoked.")
        return {"id": token_id}

    def authenticate_user_token(self, value, *, now=None):
        now = time.time() if now is None else now
        if not value.startswith("pm_") or len(value) > 100:
            return None
        with self._lock, self.connect() as conn:
            raw = conn.execute("""SELECT u.*, t.id AS token_id FROM team_api_tokens t JOIN team_users u ON u.id=t.user_id
                                WHERE t.token_hash=? AND t.expires_at>? AND t.revoked_at IS NULL AND u.active=1""",
                                (digest(value), now)).fetchone()
            if not raw:
                return None
            conn.execute("UPDATE team_api_tokens SET last_used_at=? WHERE token_hash=?", (now, digest(value)))
            return dict(raw, kind="token")
