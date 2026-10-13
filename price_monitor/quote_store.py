"""Transactional quote records, append-only revisions and private documents.

Document bytes live in the database, so the normal database backup also retains
the supporting evidence. Shared methods work with SQLite and the MySQL adapter.
"""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import PurePath
import zipfile

from .quotes import (COST_FIELDS, QUOTE_TEXT_FIELDS, DOCUMENT_TYPES, QuoteConflict, QuoteNotFound,
                     compare_quotes, landed_costs, text, validate_quote)

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_QUOTE_DOCUMENT_BYTES = 50 * 1024 * 1024
DOCUMENT_FORMATS = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".txt": "text/plain; charset=utf-8", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
QUOTE_COLUMNS = (*QUOTE_TEXT_FIELDS, "product", "unit", "incoterm", "document_type", "packaging", "quote_date",
                 "valid_until", "delivery_date", "status", "quantity", "currency", "unit_price", "exchange_rate",
                 *COST_FIELDS, "other_costs_json")


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


def iso_value(value):
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()
    return value.isoformat() if isinstance(value, date) else value


def validate_document(payload: dict) -> tuple[str, str, str, bytes]:
    filename = text(payload.get("filename"), "Le nom du fichier", 180, True)
    if "/" in filename or "\\" in filename or filename in {".", ".."} or "\n" in filename or "\t" in filename:
        raise ValueError("Nom de fichier invalide.")
    suffix = PurePath(filename).suffix.lower()
    if suffix not in DOCUMENT_FORMATS:
        raise ValueError("Formats autorisés : PDF, PNG, JPEG, TXT, DOCX et XLSX.")
    kind = payload.get("document_type")
    if not isinstance(kind, str) or kind not in DOCUMENT_TYPES:
        raise ValueError("Type de document invalide.")
    encoded = payload.get("content_base64")
    if not isinstance(encoded, str) or len(encoded) > ((MAX_DOCUMENT_BYTES + 2) // 3) * 4:
        raise ValueError("Le document doit faire au maximum 5 Mo.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Contenu du document invalide.") from None
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("Le document doit être non vide et faire au maximum 5 Mo.")
    matches = {".pdf": data.startswith(b"%PDF-"), ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
               ".jpg": data.startswith(b"\xff\xd8\xff"), ".jpeg": data.startswith(b"\xff\xd8\xff")}
    if suffix in matches and not matches[suffix]:
        raise ValueError("Le contenu ne correspond pas au format du fichier.")
    if suffix == ".txt":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Un fichier TXT doit être encodé en UTF-8.") from None
        if b"\0" in data:
            raise ValueError("Fichier texte invalide.")
    if suffix in {".docx", ".xlsx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                files = archive.infolist()
                expected = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                names = archive.namelist()
                if (expected not in names or "[Content_Types].xml" not in names or len(files) > 1000
                        or sum(f.file_size for f in files) > 25 * 1024 * 1024
                        or any("vbaproject" in name.lower() for name in names)):
                    raise ValueError()
        except (zipfile.BadZipFile, ValueError):
            raise ValueError("Document Office invalide ou contenant des macros.") from None
    return filename, kind, DOCUMENT_FORMATS[suffix], data


class QuoteStoreMixin:
    def _quote_now(self):
        return self._datetime_value(datetime.now(timezone.utc).isoformat(timespec="microseconds"))

    @staticmethod
    def _version(payload: dict) -> int:
        raw = payload.get("expected_version")
        if isinstance(raw, bool) or not str(raw).isdigit() or int(raw) < 1:
            raise ValueError("La version du devis est obligatoire pour enregistrer une correction.")
        return int(raw)

    @staticmethod
    def _audit_input(payload: dict, default_reason: str = "") -> tuple[str, str]:
        return (text(payload.get("actor", "Équipe achats"), "L’auteur", 100, True),
                text(payload.get("change_reason", default_reason), "Le motif", 1000, True))

    @staticmethod
    def _quote_input(quote: dict) -> dict:
        return {**quote, "other_costs_json": json_text(quote["other_costs"])}

    def _quote_record(self, conn, quote_id: int) -> dict:
        raw = conn.execute("SELECT * FROM supplier_quotes WHERE id=?", (quote_id,)).fetchone()
        if raw is None:
            raise QuoteNotFound("Devis introuvable.")
        row = dict(raw)
        for key in ("quote_date", "valid_until", "delivery_date", "created_at", "updated_at"):
            row[key] = iso_value(row[key])
        row["other_costs"] = json.loads(row.pop("other_costs_json"))
        row.update(landed_costs(row))
        row["documents"] = self._quote_documents(conn, quote_id)
        row["expired"] = row["valid_until"] < date.today().isoformat()
        return row

    @staticmethod
    def _quote_documents(conn, quote_id: int) -> list[dict]:
        rows = conn.execute("""SELECT id, quote_id, filename, document_type, content_type, size_bytes, sha256,
                             created_at, removed_at FROM quote_documents WHERE quote_id=? ORDER BY id""", (quote_id,)).fetchall()
        return [{key: iso_value(value) for key, value in dict(row).items()} for row in rows]

    def quote(self, quote_id: int) -> dict:
        with self.connect() as conn:
            return self._quote_record(conn, quote_id)

    def quotes(self, *, page: int = 1, page_size: int = 20, product: str | None = None, supplier: str = "") -> dict:
        page, page_size = max(1, int(page)), max(1, min(100, int(page_size)))
        clauses, params = ["1=1"], []
        if product:
            clauses.append("product=?")
            params.append(product)
        if supplier:
            clauses.append("supplier LIKE ?")
            params.append("%" + supplier.strip() + "%")
        where = " AND ".join(clauses)
        with self.connect() as conn:
            total = dict(conn.execute(f"SELECT COUNT(*) AS count FROM supplier_quotes WHERE {where}", params).fetchone())["count"]
            ids = conn.execute(f"SELECT id FROM supplier_quotes WHERE {where} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                               (*params, page_size, (page - 1) * page_size)).fetchall()
            return {"items": [self._quote_record(conn, dict(row)["id"]) for row in ids], "page": page,
                    "pages": (int(total) + page_size - 1) // page_size, "total": int(total)}

    def _quote_revision(self, conn, quote_id: int, action: str, actor: str, reason: str, changed_fields: list[str]) -> dict:
        record = self._quote_record(conn, quote_id)
        conn.execute("""INSERT INTO quote_revisions(quote_id, version, action, actor, reason, changed_fields_json, snapshot_json, created_at)
                        VALUES(?,?,?,?,?,?,?,?)""", (quote_id, record["version"], action, actor, reason,
                                                     json_text(changed_fields), json_text(record), self._quote_now()))
        return record

    def create_quote(self, payload: dict) -> dict:
        record = self._quote_input(validate_quote(payload))
        actor, reason = self._audit_input(payload, "Création du devis")
        now = self._quote_now()
        with self._lock, self.connect() as conn:
            cursor = conn.execute(f"INSERT INTO supplier_quotes({','.join(QUOTE_COLUMNS)},version,created_at,updated_at) VALUES({','.join('?' for _ in QUOTE_COLUMNS)},1,?,?)",
                                  (*[record[key] for key in QUOTE_COLUMNS], now, now))
            return self._quote_revision(conn, int(cursor.lastrowid), "created", actor, reason, list(QUOTE_COLUMNS))

    def _bump_quote(self, conn, quote_id: int, version: int) -> None:
        changed = conn.execute("UPDATE supplier_quotes SET version=version+1, updated_at=? WHERE id=? AND version=?",
                               (self._quote_now(), quote_id, version)).rowcount
        if changed != 1:
            raise QuoteConflict("Ce devis a changé. Rechargez-le avant d’enregistrer votre correction.")

    def update_quote(self, quote_id: int, payload: dict) -> dict:
        version = self._version(payload)
        actor, reason = self._audit_input(payload)
        with self._lock, self.connect() as conn:
            current = self._quote_record(conn, quote_id)
            if current["version"] != version:
                raise QuoteConflict("Ce devis a changé. Rechargez-le avant d’enregistrer votre correction.")
            record = self._quote_input(validate_quote({**current, **payload}))
            before = self._quote_input(current)
            fields = [key for key in QUOTE_COLUMNS if record[key] != before[key]]
            if not fields:
                raise ValueError("Aucune correction à enregistrer.")
            self._bump_quote(conn, quote_id, version)
            conn.execute(f"UPDATE supplier_quotes SET {','.join(key+'=?' for key in QUOTE_COLUMNS)} WHERE id=?",
                         (*[record[key] for key in QUOTE_COLUMNS], quote_id))
            return self._quote_revision(conn, quote_id, "corrected", actor, reason, fields)

    def quote_history(self, quote_id: int) -> list[dict]:
        with self.connect() as conn:
            self._quote_record(conn, quote_id)
            rows = conn.execute("SELECT * FROM quote_revisions WHERE quote_id=? ORDER BY version DESC", (quote_id,)).fetchall()
            result = []
            for raw in rows:
                row = dict(raw)
                row["snapshot"] = json.loads(row.pop("snapshot_json"))
                row["changed_fields"] = json.loads(row.pop("changed_fields_json"))
                row["created_at"] = iso_value(row["created_at"])
                result.append(row)
            return result

    def attach_quote_document(self, quote_id: int, payload: dict) -> dict:
        version = self._version(payload)
        actor, reason = self._audit_input(payload, "Ajout d’un justificatif")
        filename, kind, content_type, data = validate_document(payload)
        with self._lock, self.connect() as conn:
            self._quote_record(conn, quote_id)
            total = dict(conn.execute("SELECT COALESCE(SUM(size_bytes),0) AS total FROM quote_documents WHERE quote_id=?", (quote_id,)).fetchone())["total"]
            if int(total) + len(data) > MAX_QUOTE_DOCUMENT_BYTES:
                raise ValueError("Les justificatifs d’un devis sont limités à 50 Mo, historique compris.")
            self._bump_quote(conn, quote_id, version)
            conn.execute("""INSERT INTO quote_documents(quote_id, filename, document_type, content_type, size_bytes, sha256, content, created_at)
                            VALUES(?,?,?,?,?,?,?,?)""", (quote_id, filename, kind, content_type, len(data), hashlib.sha256(data).hexdigest(), data, self._quote_now()))
            return self._quote_revision(conn, quote_id, "document_added", actor, reason, ["documents"])

    def remove_quote_document(self, quote_id: int, document_id: int, payload: dict) -> dict:
        version = self._version(payload)
        actor, reason = self._audit_input(payload)
        with self._lock, self.connect() as conn:
            self._quote_record(conn, quote_id)
            found = conn.execute("SELECT id FROM quote_documents WHERE id=? AND quote_id=? AND removed_at IS NULL", (document_id, quote_id)).fetchone()
            if not found:
                raise QuoteNotFound("Justificatif introuvable dans ce devis.")
            self._bump_quote(conn, quote_id, version)
            conn.execute("UPDATE quote_documents SET removed_at=? WHERE id=? AND quote_id=?", (self._quote_now(), document_id, quote_id))
            return self._quote_revision(conn, quote_id, "document_removed", actor, reason, ["documents"])

    def quote_document(self, quote_id: int, document_id: int) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT filename, content_type, content, sha256 FROM quote_documents WHERE id=? AND quote_id=?", (document_id, quote_id)).fetchone()
            if row is None:
                raise QuoteNotFound("Justificatif introuvable dans ce devis.")
            return dict(row)

    def compare_quotes(self, quote_ids: list[int], as_of: str | None = None) -> dict:
        if len(quote_ids) < 2 or len(quote_ids) > 6 or len(set(quote_ids)) != len(quote_ids):
            raise ValueError("Sélectionnez entre 2 et 6 devis distincts.")
        with self.connect() as conn:
            quotes = [self._quote_record(conn, quote_id) for quote_id in quote_ids]
        return compare_quotes(quotes, date.fromisoformat(as_of) if as_of else None)
