from __future__ import annotations

import json
import csv
import math
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from .config import (
    DB_BACKEND,
    DB_PATH,
    DEMO_MODE,
    MYSQL_SETTINGS,
    REAL_DATA_PATH,
    BITUMEN_DATA_PATH,
    MySQLSettings,
)
from .bitumen import INDEX_CODE, INDEX_LABEL, INDEX_UNIT, benchmark_payload
from .quote_store import QuoteStoreMixin, json_text
from .collection_store import CollectionStoreMixin
from .access_store import AccessStoreMixin, borrowed_connection
from .forecast_store import ForecastStoreMixin
from .market_calendar import market_session
from .quotes import QuoteConflict
from .quotes import COST_FIELDS, EXTRA_COST_FIELDS, landed_costs
from .services.comparison import SOURCE_CODE_BY_LABEL, compare_api_prices
from .instruments import (
    INSTRUMENTS, DEFAULT_INSTRUMENTS, LIVE_INSTRUMENTS,
    resolve_instrument, canonical_daily_rows, canonical_latest, price_alert_key, SOURCE_PRIORITY,
)


PRODUCTS = {
    "gasoil": {"label": "Gasoil / diesel", "unit": "USD/gallon", "color": "#5eead4"},
    "brent": {"label": "Brent crude oil", "unit": "USD/baril", "color": "#f9b35c"},
    "bitume": {"label": "Bitumen", "unit": "USD/tonne", "color": "#a78bfa"},
}

PURCHASE_UNITS = {
    "gasoil": "gallon",
    "brent": "baril",
    "bitume": "tonne",
}

PROCUREMENT_PRODUCTS = {"gasoil", "bitume"}

# A read model only: indices remain physically separate from supplier prices.
# Revisions replace older values for a given index month in market displays.
MARKET_READINGS_SQL = """
    SELECT id, product, price, unit, currency, source, supplier, source_date,
           collected_at, variation, variation_pct, is_unchanged, notes,
           'quote' AS data_kind, NULL AS benchmark_code, instrument
    FROM price_observations
    UNION ALL
    SELECT -id AS id, product, value AS price, unit, '' AS currency, source,
           '' AS supplier, source_date, collected_at,
           ROUND(value-previous_value, 6) AS variation,
           ROUND((value-previous_value)/previous_value*100, 4) AS variation_pct,
           CASE WHEN value=previous_value THEN 1 ELSE 0 END AS is_unchanged,
           notes, 'index' AS data_kind, code AS benchmark_code, 'asphalt_index' AS instrument
    FROM (
        SELECT revised.*, LAG(value) OVER (ORDER BY source_date) AS previous_value
        FROM (
            SELECT b.*, ROW_NUMBER() OVER (
                PARTITION BY code, source_date ORDER BY collected_at DESC, id DESC
            ) AS revision_rank FROM market_benchmarks b
            WHERE code='bls_wpu058' AND product='bitume'
        ) revised WHERE revision_rank=1
    ) sequenced
"""

DAILY_SOURCE_PRIORITY_SQL = "CASE source " + " ".join(
    "WHEN '" + source.replace("'", "''") + f"' THEN {priority}"
    for source, priority in SOURCE_PRIORITY.items()
) + " ELSE 10 END"

SUPPLIER_CHANNEL_ROWS = [
    (
        "Shell Commercial Fuels", "gasoil", "global",
        "Africa, the Americas, Asia/Middle East, and several European markets",
        "Local B2B sales contact or authorized distributor",
        "Local contract; road tanker or depot delivery depending on the country",
        "Request EN 590 (10 ppm sulfur) or the equivalent local standard",
        "https://www.shell.com/business-customers/commercial-fuels.html",
        "https://www.shell.com/business-customers/commercial-fuels/contact-commercial-fuels.html",
        "Check availability, import licensing, and minimum volume with the local entity.",
        10,
    ),
    (
        "TotalEnergies - business network", "gasoil", "global",
        "International network of subsidiaries and country customer sites",
        "Local subsidiary, key account team, or B2B distributor",
        "Terms, credit, and delivery negotiated in the destination country",
        "Specify EN 590/ASTM, sulfur content, quantity, terminal, and delivery date",
        "https://totalenergies.com/company/energy-expertise/ship-market",
        "https://totalenergies.com/clients-websites",
        "Use the official site for the delivery country and request a written offer from the contracting entity.",
        20,
    ),
    (
        "Licensed terminal trader or distributor", "gasoil", "local",
        "Nearest licensed oil terminal or depot to the destination",
        "Request bids from three licensed distributors",
        "Ex-rack/EXW, FCA, DAP, or road delivery depending on infrastructure",
        "National specification, certificate of analysis, SDS, and proof of origin",
        "",
        "",
        "Check business registration, energy license, sanctions, and bank details through a channel other than email.",
        30,
    ),
    (
        "Shell Bitumen", "bitume", "global",
        "Production, depot, and distribution network across many markets",
        "Request a B2B quote from the local bitumen team",
        "Heated bulk, road tanker, or vessel depending on destination and volume",
        "Specify EN 12591/ASTM D946, grade, temperature, packaging, and use",
        "https://www.shell.com/business-customers/bitumen.html",
        "https://www.shell.com/business-customers/bitumen.html",
        "Availability and loading point vary by country; obtain a quote with Incoterm and validity.",
        10,
    ),
    (
        "TotalEnergies Bitumen", "bitume", "europe",
        "Seven refineries and operations reported in fifteen European countries",
        "Local sales team or Bitumen Online portal for eligible customers",
        "Heated bulk; fixed or formula pricing depending on market and contract",
        "Road, modified, or industrial bitumen; specify grade and standard",
        "https://bitumen.totalenergies.com/",
        "https://bitumen.totalenergies.com/digital-portal-bitumen-customers",
        "Portal and price access is limited to customers; request a supplier account.",
        20,
    ),
    (
        "Nynas Bitumen", "bitume", "europe",
        "Nordic countries, Baltic states, United Kingdom, and exports subject to availability",
        "Regional or export sales contact",
        "Annual contract or spot quote; heated logistics depending on site",
        "Road and modified bitumen; request product data sheet, SDS, and declaration of performance",
        "https://nynas.com/en/products/bitumen/",
        "https://nynas.com/en/products/bitumen/our-offer/",
        "Use Nynas's official market selector or listed export contact.",
        30,
    ),
    (
        "Puma Energy Bitumen", "bitume", "global",
        "Markets served by the Puma Energy network, including Africa and Asia-Pacific",
        "Bitumen technical and sales contact",
        "Bulk, bitumen containers, and vessel-to-road solutions depending on market",
        "Grades compliant with EN 12591 or AS 2008, depending on product",
        "https://pumaenergy.com/bitumen/",
        "https://pumaenergy.com/bitumen/bitumen-products/",
        "Confirm covered territory, loading point, and temperature maintenance cost.",
        40,
    ),
]

# Existing installations retain the original French seed rows. Render the
# current English catalog without altering saved supplier records or IDs.
_LEGACY_SUPPLIER_NAMES = {
    "TotalEnergies - réseau entreprises": "TotalEnergies - business network",
    "Négociant ou distributeur agréé au terminal": "Licensed terminal trader or distributor",
}
_SUPPLIER_DISPLAY_ROWS = {
    name: row for row in SUPPLIER_CHANNEL_ROWS
    for name in (row[0], *[old for old, new in _LEGACY_SUPPLIER_NAMES.items() if new == row[0]])
}


def _english_supplier_channel(item: dict[str, Any]) -> dict[str, Any]:
    row = _SUPPLIER_DISPLAY_ROWS.get(item.get("name"))
    if row is None:
        return item
    for key, index in (("name", 0), ("coverage", 3), ("channel", 4),
                       ("typical_terms", 5), ("specifications", 6), ("buyer_note", 9)):
        item[key] = row[index]
    return item


SCHEMA = """
CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    price REAL NOT NULL CHECK(price > 0),
    unit TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    source TEXT NOT NULL,
    source_date TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    variation REAL,
    variation_pct REAL,
    is_unchanged INTEGER NOT NULL DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product, source, source_date, price)
);
CREATE INDEX IF NOT EXISTS idx_observations_product_date
  ON price_observations(product, collected_at);
CREATE TABLE IF NOT EXISTS data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    label TEXT NOT NULL,
    provider TEXT NOT NULL,
    frequency TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    last_success_at TEXT,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS collection_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    rows_collected INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS alert_states (
    alert_key TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 0,
    last_value REAL,
    last_notified_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS procurement_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'brent', 'bitume')),
    supplier TEXT NOT NULL,
    quantity REAL NOT NULL CHECK(quantity > 0),
    unit TEXT NOT NULL,
    currency TEXT NOT NULL,
    unit_price REAL NOT NULL CHECK(unit_price > 0),
    exchange_rate REAL NOT NULL CHECK(exchange_rate > 0),
    transport_cost REAL NOT NULL DEFAULT 0 CHECK(transport_cost >= 0),
    budget_amount REAL,
    purchase_date TEXT NOT NULL,
    total_cost REAL NOT NULL,
    total_cost_usd REAL NOT NULL,
    budget_variance REAL,
    budget_variance_usd REAL,
    market_price_usd REAL,
    price_impact_unit_usd REAL,
    price_impact_total_usd REAL,
    price_impact_pct REAL,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_procurement_purchase_date
  ON procurement_purchases(purchase_date, product);
CREATE TABLE IF NOT EXISTS market_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'bitume')),
    label TEXT NOT NULL,
    value REAL NOT NULL CHECK(value > 0),
    unit TEXT NOT NULL,
    geography TEXT NOT NULL,
    source TEXT NOT NULL,
    source_date TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    variation REAL,
    variation_pct REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(code, source_date, value)
);
CREATE INDEX IF NOT EXISTS idx_market_benchmarks_code_date
  ON market_benchmarks(code, source_date);
CREATE TABLE IF NOT EXISTS supplier_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    product TEXT NOT NULL CHECK(product IN ('gasoil', 'bitume')),
    region TEXT NOT NULL,
    coverage TEXT NOT NULL,
    channel TEXT NOT NULL,
    typical_terms TEXT NOT NULL,
    specifications TEXT NOT NULL,
    website_url TEXT NOT NULL DEFAULT '',
    contact_url TEXT NOT NULL DEFAULT '',
    buyer_note TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 100,
    UNIQUE(name, product, region)
);
"""


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
SQL_ROOT = Path(os.getenv("PRICE_MONITOR_SQL_DIR", PROJECT_ROOT / "sql"))
if not (SQL_ROOT / "mysql_schema.sql").exists():
    SQL_ROOT = PACKAGE_DIR
MYSQL_SCHEMA_PATH = SQL_ROOT / "mysql_schema.sql"
MYSQL_MIGRATIONS_DIR = SQL_ROOT / "mysql_migrations"
MYSQL_SCHEMA = MYSQL_SCHEMA_PATH.read_text(encoding="utf-8")
SQLITE_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class DatabaseBackend(Protocol):
    """Persistence contract shared by the SQLite and MySQL implementations."""

    def insert_observation(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def log_collection(self, status: str, rows: int, message: str, started_at: str, finished_at: str) -> None: ...

    def update_source_health(self, source_code: str, success: bool, at: str, error: str | None = None, quote_at: str | None = None) -> None: ...

    def source_health(self) -> list[dict[str, Any]]: ...

    def collection_state(self, provider: str) -> dict: ...
    def claim_collection(self, provider: str, now: float, force: bool = False) -> str | None: ...
    def finish_collection(self, provider: str, token: str, *, now: float, next_due: float,
                          failures: int, status: str, error: str = "") -> None: ...
    def request_budget(self, provider: str, now: float, limit: int = 0) -> dict: ...
    def reserve_provider_request(self, provider: str, now: float, limit: int = 0) -> dict: ...
    def defer_provider_requests(self, provider: str, until: float, reason: str) -> None: ...

    def latest(self) -> list[dict[str, Any]]: ...

    def latest_by_instrument(self) -> list[dict[str, Any]]: ...

    def observations(self, product: str | None = None, days: int = 30, limit: int | None = 600, instrument: str | None = None, daily: bool = False) -> list[dict[str, Any]]: ...

    def iter_history(
        self, product: str | None = None, supplier: str | None = None, source: str | None = None,
        date_from: str | None = None, date_to: str | None = None,
        min_price: float | None = None, max_price: float | None = None,
        data_kind: str | None = None, instrument: str | None = None,
    ) -> Iterator[dict[str, Any]]: ...

    def dashboard(self, days: int = 30, instruments: dict[str, str] | None = None) -> dict[str, Any]: ...

    def source_comparison(self) -> dict[str, Any]: ...

    def logs(self, limit: int = 12) -> list[dict[str, Any]]: ...

    def collection_logs_after(self, last_id: int) -> list[dict[str, Any]]: ...

    def get_alert_state(self, alert_key: str) -> dict[str, Any] | None: ...

    def set_alert_state(
        self,
        alert_key: str,
        active: bool,
        last_value: float | None,
        updated_at: str,
        last_notified_at: str | None = None,
        last_status: str | None = None,
        clear_notified: bool = False,
    ) -> None: ...

    def alert_rules(self, include_disabled: bool = True) -> list[dict[str, Any]]: ...

    def create_alert_rule(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update_alert_rule(self, rule_id: int, payload: dict[str, Any]) -> dict[str, Any]: ...

    def delete_alert_rule(self, rule_id: int) -> None: ...

    def create_procurement(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def procurements(self, limit: int = 100) -> list[dict[str, Any]]: ...

    def quotes(self, *, page: int = 1, page_size: int = 20, product: str | None = None, supplier: str = "") -> dict: ...
    def quote(self, quote_id: int) -> dict: ...
    def create_quote(self, payload: dict) -> dict: ...
    def update_quote(self, quote_id: int, payload: dict) -> dict: ...
    def quote_history(self, quote_id: int) -> list[dict]: ...
    def attach_quote_document(self, quote_id: int, payload: dict) -> dict: ...
    def remove_quote_document(self, quote_id: int, document_id: int, payload: dict) -> dict: ...
    def quote_document(self, quote_id: int, document_id: int) -> dict: ...
    def compare_quotes(self, quote_ids: list[int], as_of: str | None = None) -> dict: ...

    def record_live_forecast(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def reconcile_live_forecasts(self) -> int: ...

    def live_forecast_performance(self, window: int = 90, *, product: str | None = None,
                                  instrument: str | None = None, horizon: int | None = None) -> dict[str, Any]: ...

    def insert_benchmark(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def latest_benchmarks(self, product: str | None = None, limit: int = 20) -> list[dict[str, Any]]: ...

    def bitumen_series(self, days: int = 1825, include_anchor: bool = False) -> list[dict[str, Any]]: ...

    def supplier_channels(self, product: str | None = None, region: str | None = None) -> list[dict[str, Any]]: ...

    def history(
        self,
        product: str | None = None,
        supplier: str | None = None,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        page: int = 1,
        page_size: int = 50,
        data_kind: str | None = None,
        instrument: str | None = None,
    ) -> dict[str, Any]: ...

    def compare_periods(
        self,
        period_a_from: str,
        period_a_to: str,
        period_b_from: str,
        period_b_to: str,
        product: str | None = None,
        supplier: str | None = None,
        source: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        data_kind: str | None = None,
        instrument: str | None = None,
    ) -> dict[str, Any]: ...


def _split_sql_script(script: str) -> list[str]:
    """Split the simple DDL scripts used by the project into executable statements."""
    statements: list[str] = []
    current: list[str] = []
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        current.append(raw_line)
        if line.endswith(";"):
            statement = "\n".join(current).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            current = []
    remainder = "\n".join(current).strip()
    if remainder:
        statements.append(remainder)
    return statements


def run_mysql_migrations(connection: Any, migrations_dir: Path = MYSQL_MIGRATIONS_DIR) -> list[str]:
    """Apply pending numbered MySQL migrations and return applied versions."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(32) PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()}
    newly_applied: list[str] = []
    for migration in sorted(migrations_dir.glob("*.sql")):
        version = migration.stem.split("_", 1)[0]
        if not version or version in applied:
            continue
        for statement in _split_sql_script(migration.read_text(encoding="utf-8")):
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations(version) VALUES(?)", (version,))
        newly_applied.append(version)
    return newly_applied


def run_sqlite_migrations(connection: sqlite3.Connection, migrations_dir: Path = SQLITE_MIGRATIONS_DIR) -> list[str]:
    """Apply numbered SQLite migrations and record them for repeatable deploys."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()}
    newly_applied: list[str] = []
    for migration in sorted(migrations_dir.glob("*.sql")):
        version = migration.stem.split("_", 1)[0]
        if not version or version in applied:
            continue
        for statement in _split_sql_script(migration.read_text(encoding="utf-8")):
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations(version) VALUES(?)", (version,))
        newly_applied.append(version)
    return newly_applied


class SQLiteDatabase(ForecastStoreMixin, QuoteStoreMixin, CollectionStoreMixin, AccessStoreMixin):
    """SQLite persistence backend used locally and by default."""

    _lock = threading.RLock()

    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        borrowed = borrowed_connection(self)
        if borrowed is not None:
            return borrowed
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _datetime_value(value: str) -> str:
        return value

    @staticmethod
    def _source_date_value(value: Any) -> str:
        """Validate and normalize a publication date without silently truncating it."""
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Source date must use YYYY-MM-DD format.")
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise ValueError("Source date must be a valid calendar date.") from None

    @staticmethod
    def _collected_at_value(value: Any) -> str:
        """Validate an ISO-8601 timestamp and store it in a comparable UTC form."""
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
            value,
        ):
            raise ValueError("Collection date must be an ISO 8601 timestamp with a timezone.")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Collection date must be a valid ISO 8601 timestamp.") from None
        if parsed.tzinfo is None:
            raise ValueError("Collection date must include a timezone.")
        return parsed.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _deduplicate_observations(connection: Any) -> None:
        """Keep the first row before adding the identity index to older databases."""
        connection.execute(
            """DELETE FROM price_observations
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM price_observations
                GROUP BY product, instrument, source, source_date, price
            )"""
        )

    @staticmethod
    def _ensure_observation_identity(connection: Any) -> None:
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS uq_observation_identity
            ON price_observations(product, instrument, source, source_date, price)"""
        )

    def initialize(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(SCHEMA)
            run_sqlite_migrations(conn)
            self._deduplicate_observations(conn)
            self._ensure_observation_identity(conn)
            self._seed_sources(conn)
            self._seed_supplier_channels(conn)
            count = conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0]
            # Keep an existing installation synchronized with the reproducible
            # FRED/EIA snapshot. The import is idempotent, so refreshing the
            # bundled CSV adds missing market dates without duplicating rows.
            imported = self._seed_real_data(conn)
            self._seed_bitumen_data(conn)
            if count == 0 and imported == 0 and DEMO_MODE:
                self._seed_demo(conn)

    def healthcheck(self) -> dict[str, str]:
        """Verify that the configured database accepts a simple read."""
        with self.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "backend": "sqlite"}

    def _seed_sources(self, conn: sqlite3.Connection) -> None:
        rows = [
            ("fred_diesel", "Gasoil / diesel", "EIA/FRED - DDFUELNYH", "quotidienne"),
            ("fred_brent", "Brent crude oil", "EIA/FRED - DCOILBRENTEU", "quotidienne"),
            ("alpha_brent", "Brent crude oil", "Alpha Vantage - BRENT", "quotidienne"),
            ("eia_diesel", "Gasoil / diesel", "U.S. EIA - PET.DDFUELNYH.D", "quotidienne"),
            ("eia_brent", "Brent crude oil", "U.S. EIA - PET.RBRTE.D", "quotidienne"),
            ("live_gasoil_proxy", "Diesel / heating oil proxy", "Yahoo Finance - Heating Oil HO=F (live proxy)", "temps réel"),
            ("live_brent", "Brent crude oil", "Yahoo Finance - Brent futures BZ=F", "temps réel"),
            ("internal_bitumen", "Bitumen", "Internal quotes and invoices", "à la demande"),
            ("bls_asphalt_ppi", "Bitumen/asphalt index (proxy)", "U.S. BLS - WPU058", "mensuelle"),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO data_sources(code, label, provider, frequency) VALUES(?,?,?,?)",
            rows,
        )

    def _seed_supplier_channels(self, conn: Any) -> None:
        conn.executemany(
            """INSERT OR IGNORE INTO supplier_channels
            (name, product, region, coverage, channel, typical_terms, specifications,
             website_url, contact_url, buyer_note, sort_order)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            SUPPLIER_CHANNEL_ROWS,
        )

    def _seed_real_data(self, conn: Any) -> int:
        """Synchronize the reproducible FRED/EIA snapshot bundled with the project."""
        if not REAL_DATA_PATH.exists():
            return 0
        existing_rows = conn.execute(
            """SELECT product, source, source_date, price
            FROM price_observations
            WHERE source IN (?,?)""",
            ("EIA/FRED - DDFUELNYH", "EIA/FRED - DCOILBRENTEU"),
        ).fetchall()
        existing = {
            (
                str(row["product"]),
                str(row["source"]),
                str(row["source_date"])[:10],
                float(row["price"]),
            )
            for row in existing_rows
        }
        previous: dict[str, float] = {}
        imported = 0
        with REAL_DATA_PATH.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                source_date = str(row.get("date") or "")[:10]
                if not source_date:
                    continue
                for product, column, unit, source in (
                    ("gasoil", "gasoil", "USD/gallon", "EIA/FRED - DDFUELNYH"),
                    ("brent", "brent", "USD/baril", "EIA/FRED - DCOILBRENTEU"),
                ):
                    raw_value = row.get(column)
                    if raw_value in (None, "", "."):
                        continue
                    price = float(raw_value)
                    prior = previous.get(product)
                    variation = round(price - prior, 6) if prior is not None else None
                    variation_pct = round((variation / prior) * 100, 4) if prior else None
                    identity = (product, source, source_date, price)
                    if identity in existing:
                        previous[product] = price
                        continue
                    conn.execute(
                        """INSERT INTO price_observations
                        (product, instrument, price, unit, currency, source, source_date, collected_at,
                         variation, variation_pct, is_unchanged, notes)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (product, DEFAULT_INSTRUMENTS[product], price, unit, "USD", source, source_date,
                         self._datetime_value(f"{source_date}T12:00:00+00:00"), variation, variation_pct, 0,
                         "Historical EIA data imported from FRED; collection time approximated for backfill."),
                    )
                    existing.add(identity)
                    previous[product] = price
                    imported += 1
        return imported

    def _seed_bitumen_data(self, conn: Any) -> int:
        """Idempotently import the real monthly BLS/FRED index, including revisions."""
        path = BITUMEN_DATA_PATH if os.getenv("PRICE_MONITOR_BITUMEN_DATA") else REAL_DATA_PATH.parent / "bitumen_index.csv"
        if not path.exists() or DEMO_MODE:
            return 0
        imported = 0
        now = self._datetime_value(datetime.now(timezone.utc).isoformat())
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = sorted(csv.DictReader(handle), key=lambda row: row["date"])
        for row in rows:
            source_date = self._source_date_value(row["date"])
            if row.get("series") != "WPU058":
                raise ValueError("Bitumen history must use the BLS WPU058 series.")
            value = float(row["bitume"])
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid bitumen index in the snapshot.")
            existing = conn.execute(
                "SELECT id FROM market_benchmarks WHERE code=? AND source_date=? AND value=?",
                (INDEX_CODE, source_date, value),
            ).fetchone()
            if existing:
                continue
            payload = benchmark_payload(source_date, value, mirror=True)
            prior = conn.execute(
                "SELECT value FROM market_benchmarks WHERE code=? AND source_date<? ORDER BY source_date DESC, collected_at DESC, id DESC LIMIT 1",
                (INDEX_CODE, source_date),
            ).fetchone()
            delta = value - float(prior["value"]) if prior else None
            conn.execute(
                """INSERT INTO market_benchmarks
                (code, product, label, value, unit, geography, source, source_date,
                 collected_at, source_url, notes, variation, variation_pct)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (INDEX_CODE, "bitume", payload["label"], value, INDEX_UNIT, payload["geography"],
                 payload["source"], source_date, now, payload["source_url"],
                 payload["notes"] + " Historical BLS/FRED data imported; original publication date unknown.",
                 round(delta, 6) if delta is not None else None,
                 round(delta / float(prior["value"]) * 100, 4) if prior else None),
            )
            imported += 1
        return imported

    def _seed_demo(self, conn: Any) -> None:
        """Create a useful 30-day sandbox so the dashboard is meaningful at first launch."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        base = {"gasoil": 2.33, "brent": 81.40, "bitume": 522.00}
        units = {k: v["unit"] for k, v in PRODUCTS.items()}
        labels = {"gasoil": "Demo mode - EIA/FRED", "brent": "Demo mode - Alpha Vantage", "bitume": "Demo mode - internal entry"}
        for days_ago in range(29, -1, -1):
            for product, value in base.items():
                wave = ((days_ago * 17 + len(product) * 5) % 19 - 9) / 100
                price = round(value * (1 + wave / 100), 4)
                source_date = (now - timedelta(days=days_ago)).date().isoformat()
                collected_at = self._datetime_value((now - timedelta(days=days_ago)).isoformat())
                conn.execute(
                    """INSERT INTO price_observations
                    (product, instrument, price, unit, currency, source, source_date, collected_at,
                     variation, variation_pct, is_unchanged, notes)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (product, DEFAULT_INSTRUMENTS[product], price, units[product], "USD", labels[product], source_date,
                     collected_at, None, None, 0, "Demo value to be replaced by a configured source."),
                )

    def seed_if_empty(self) -> None:
        with self.connect() as conn:
            if conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 0:
                imported = self._seed_real_data(conn)
                if imported == 0 and DEMO_MODE:
                    self._seed_demo(conn)

    def insert_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        product = str(payload.get("product", "")).lower().strip()
        if product not in PRODUCTS:
            raise ValueError("Unknown product. Use gasoil, brent, or bitume.")
        try:
            price = float(payload.get("price"))
        except (TypeError, ValueError):
            raise ValueError("Price must be numeric.") from None
        if not math.isfinite(price) or price <= 0 or price > 1_000_000:
            raise ValueError("Price must be between 0 and 1,000,000.")
        source_date_input = payload.get("source_date")
        source_date = self._source_date_value(
            datetime.now(timezone.utc).date().isoformat() if source_date_input is None else source_date_input
        )
        collected_at_input = payload.get("collected_at")
        collected_at = self._datetime_value(
            self._collected_at_value(
                datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                if collected_at_input is None
                else collected_at_input
            )
        )
        unit_input = payload.get("unit")
        unit = PRODUCTS[product]["unit"] if unit_input is None else unit_input
        if not isinstance(unit, str) or unit.strip() != PRODUCTS[product]["unit"]:
            raise ValueError(f"Invalid unit for {product}. Use {PRODUCTS[product]['unit']}.")
        unit = unit.strip()
        source = str(payload.get("source") or "Validated entry")[:150]
        instrument = resolve_instrument(product, source, payload.get("instrument"))
        supplier = str(payload.get("supplier") or "").strip()[:150]
        if not supplier and product == "bitume":
            supplier = source
        notes = str(payload.get("notes") or "")[:500]
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                """SELECT * FROM price_observations
                WHERE product=? AND instrument=? AND source=? AND source_date=? AND price=?
                ORDER BY id LIMIT 1""",
                (product, instrument, source, source_date, price),
            ).fetchone()
            if existing:
                if source in SOURCE_CODE_BY_LABEL:
                    # A provider can revise a quote back to an earlier value.
                    # Keep its identity, but rank it by the latest receipt so
                    # the comparison can recover when the APIs agree again.
                    conn.execute(
                        "UPDATE price_observations SET collected_at=? WHERE id=? AND collected_at<?",
                        (collected_at, existing["id"], collected_at),
                    )
                    existing = conn.execute("SELECT * FROM price_observations WHERE id=?", (existing["id"],)).fetchone()
                record = dict(existing)
                self._match_live_forecasts_for_actual(
                    conn, product=product, instrument=instrument, value=price,
                    source_date=source_date, collected_at=collected_at,
                )
                return record
            previous = conn.execute(
                """SELECT price, source_date FROM price_observations
                WHERE product=? AND instrument=? AND source=?
                  AND (source_date<? OR (source_date=? AND collected_at<?))
                ORDER BY source_date DESC, collected_at DESC, id DESC LIMIT 1""",
                (product, instrument, source, source_date, source_date, collected_at),
            ).fetchone()
            previous_price = float(previous["price"]) if previous else None
            variation = round(price - previous_price, 6) if previous_price is not None else None
            variation_pct = round((variation / previous_price) * 100, 4) if previous_price else None
            previous_source_date = previous["source_date"] if previous else None
            if isinstance(previous_source_date, (date, datetime)):
                previous_source_date = previous_source_date.isoformat()[:10]
            unchanged = 1 if previous and previous_source_date == source_date else 0
            if unchanged:
                notes = (notes + " | Source publication unchanged.").strip(" |")
            try:
                cur = conn.execute(
                    """INSERT INTO price_observations
                    (product, instrument, price, unit, currency, source, supplier, source_date, collected_at,
                     variation, variation_pct, is_unchanged, notes)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (product, instrument, price, unit, "USD", source, supplier, source_date, collected_at,
                     variation, variation_pct, unchanged, notes),
                )
            except Exception:
                # Another worker may have inserted the same observation after the
                # pre-insert lookup. The unique identity index makes that race safe.
                existing = conn.execute(
                    """SELECT * FROM price_observations
                    WHERE product=? AND instrument=? AND source=? AND source_date=? AND price=?
                    ORDER BY id LIMIT 1""",
                    (product, instrument, source, source_date, price),
                ).fetchone()
                if existing:
                    record = dict(existing)
                    self._match_live_forecasts_for_actual(
                        conn, product=product, instrument=instrument, value=price,
                        source_date=source_date, collected_at=collected_at,
                    )
                    return record
                raise
            record = conn.execute("SELECT * FROM price_observations WHERE id=?", (cur.lastrowid,)).fetchone()
            self._match_live_forecasts_for_actual(
                conn, product=product, instrument=instrument, value=price,
                source_date=source_date, collected_at=collected_at,
            )
            return dict(record)

    def insert_benchmark(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a non-tradable market indicator without mixing it with quotes."""
        code = str(payload.get("code") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{2,64}", code):
            raise ValueError("Invalid indicator code.")
        product = str(payload.get("product") or "").strip().lower()
        if product not in PROCUREMENT_PRODUCTS:
            raise ValueError("The indicator must be for diesel or bitumen.")
        try:
            value = float(payload.get("value"))
        except (TypeError, ValueError):
            raise ValueError("Indicator value must be numeric.") from None
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Indicator value must be greater than zero.")
        label = str(payload.get("label") or "").strip()[:180]
        unit = str(payload.get("unit") or "").strip()[:64]
        source = str(payload.get("source") or "").strip()[:180]
        if not label or not unit or not source:
            raise ValueError("Indicator label, unit, and source are required.")
        source_date = self._source_date_value(payload.get("source_date"))
        if code == INDEX_CODE and (product != "bitume" or unit != INDEX_UNIT or not source_date.endswith("-01")):
            raise ValueError("WPU058 must retain its index unit and a monthly date (first day of the month).")
        collected_at = self._collected_at_value(
            payload.get("collected_at")
            or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        )
        geography = str(payload.get("geography") or "International")[:100]
        source_url = str(payload.get("source_url") or "")[:500]
        notes = str(payload.get("notes") or "")[:500]

        with self._lock, self.connect() as conn:
            existing = conn.execute(
                """SELECT * FROM market_benchmarks
                WHERE code=? AND source_date=? AND value=? ORDER BY id LIMIT 1""",
                (code, source_date, value),
            ).fetchone()
            if existing:
                if code == INDEX_CODE and str(existing["source"]) != source:
                    # The series identity stays the same when its delivery
                    # switches between BLS and FRED; retain the current route.
                    conn.execute(
                        "UPDATE market_benchmarks SET source=?, notes=?, collected_at=? WHERE id=?",
                        (source, notes, self._datetime_value(collected_at), existing["id"]),
                    )
                    record = dict(conn.execute("SELECT * FROM market_benchmarks WHERE id=?", (existing["id"],)).fetchone())
                else:
                    record = dict(existing)
                if code == INDEX_CODE:
                    self._match_live_forecasts_for_actual(
                        conn, product=product, instrument="asphalt_index", value=value,
                        source_date=source_date, collected_at=collected_at,
                    )
                return record
            previous = conn.execute(
                "SELECT value FROM market_benchmarks WHERE code=? AND source_date<? ORDER BY source_date DESC, collected_at DESC, id DESC LIMIT 1",
                (code, source_date),
            ).fetchone()
            previous_value = float(previous["value"]) if previous else None
            variation = round(value - previous_value, 6) if previous_value is not None else None
            variation_pct = round((variation / previous_value) * 100, 4) if previous_value else None
            try:
                cursor = conn.execute(
                    """INSERT INTO market_benchmarks
                    (code, product, label, value, unit, geography, source, source_date,
                     collected_at, source_url, notes, variation, variation_pct)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        code, product, label, value, unit, geography, source, source_date,
                        self._datetime_value(collected_at), source_url, notes, variation, variation_pct,
                    ),
                )
            except Exception:
                existing = conn.execute(
                    """SELECT * FROM market_benchmarks
                    WHERE code=? AND source_date=? AND value=? ORDER BY id LIMIT 1""",
                    (code, source_date, value),
                ).fetchone()
                if existing:
                    return dict(existing)
                raise
            record = conn.execute(
                "SELECT * FROM market_benchmarks WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
            if code == INDEX_CODE:
                self._match_live_forecasts_for_actual(
                    conn, product=product, instrument="asphalt_index", value=value,
                    source_date=source_date, collected_at=collected_at,
                )
            return dict(record)

    def latest_benchmarks(
        self, product: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        if product is not None:
            product = product.strip().lower()
            if product not in PROCUREMENT_PRODUCTS:
                raise ValueError("Unknown product. Use gasoil or bitume.")
        safe_limit = max(1, min(int(limit), 100))
        clauses = ["product=?"] if product else []
        params: list[Any] = [product] if product else []
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM market_benchmarks {where}
                ORDER BY source_date DESC, collected_at DESC, id DESC""",
                params,
            ).fetchall()
        latest_by_code: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            latest_by_code.setdefault(str(item["code"]), item)
            if len(latest_by_code) >= safe_limit:
                break
        if INDEX_CODE in latest_by_code:
            series = self.bitumen_series(days=100_000)
            if series:
                current = series[-1]
                latest_by_code[INDEX_CODE].update(
                    variation=current["variation"], variation_pct=current["variation_pct"],
                )
        return list(latest_by_code.values())

    def supplier_channels(
        self, product: str | None = None, region: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["active=1"]
        params: list[Any] = []
        if product:
            product = product.strip().lower()
            if product not in PROCUREMENT_PRODUCTS:
                raise ValueError("Unknown product. Use gasoil or bitume.")
            clauses.append("product=?")
            params.append(product)
        normalized_region = str(region or "").strip().lower()
        if normalized_region and normalized_region not in {"all", "global"}:
            if not re.fullmatch(r"[a-z_]{2,32}", normalized_region):
                raise ValueError("Invalid region.")
            clauses.append("region IN ('global', ?)")
            params.append(normalized_region)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT id, name, product, region, coverage, channel, typical_terms,
                specifications, website_url, contact_url, buyer_note
                FROM supplier_channels WHERE {' AND '.join(clauses)}
                ORDER BY product, sort_order, name""",
                params,
            ).fetchall()
        return [_english_supplier_channel(dict(row)) for row in rows]

    def create_procurement(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a purchase estimate and compare it with the latest USD market price."""
        linked_quote = None
        if payload.get("quote_id") not in (None, ""):
            linked_quote = self.quote(int(payload["quote_id"]))
            if str(payload.get("quote_version")) != str(linked_quote["version"]):
                raise QuoteConflict("This quote has changed. Reload it before preparing the budget.")
            payload = {**payload, **{key: linked_quote[key] for key in
                       ("product", "supplier", "quantity", "unit", "currency", "unit_price", "exchange_rate", *COST_FIELDS, "other_costs")}}
        product = str(payload.get("product", "")).lower().strip()
        if product not in PRODUCTS:
            raise ValueError("Unknown product. Use gasoil, brent, or bitume.")

        supplier = str(payload.get("supplier") or "").strip()
        if not supplier:
            raise ValueError("Supplier is required.")
        supplier = supplier[:150]

        def positive_number(field: str, label: str, default: float | None = None) -> float:
            raw = payload.get(field, default)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{label} must be numeric.") from None
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be greater than zero.")
            return value

        quantity = positive_number("quantity", "Quantity")
        unit = str(payload.get("unit") or PURCHASE_UNITS[product]).strip().lower()
        if unit != PURCHASE_UNITS[product]:
            raise ValueError(f"Invalid unit for {product}. Use {PURCHASE_UNITS[product]}.")
        currency = str(payload.get("currency") or "USD").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("Currency must be a three-letter ISO code, such as USD or EUR.")
        unit_price = positive_number("unit_price", "Unit price")

        exchange_input = payload.get("exchange_rate")
        if exchange_input in (None, "") and currency == "USD":
            exchange_input = 1
        try:
            exchange_rate = float(exchange_input)
        except (TypeError, ValueError):
            raise ValueError("Exchange rate must be numeric.") from None
        if not math.isfinite(exchange_rate) or exchange_rate <= 0:
            raise ValueError("Exchange rate must be greater than zero.")

        costs = landed_costs(payload)
        quantity, unit_price, exchange_rate = costs["quantity"], costs["unit_price"], costs["exchange_rate"]
        transport_cost = costs["transport_cost"]

        budget_raw = payload.get("budget_amount")
        budget_amount: float | None
        if budget_raw in (None, ""):
            budget_amount = None
        else:
            try:
                budget_amount = float(budget_raw)
            except (TypeError, ValueError):
                raise ValueError("Budget must be numeric.") from None
            if not math.isfinite(budget_amount) or budget_amount < 0:
                raise ValueError("Budget must be non-negative.")

        purchase_date = self._source_date_value(
            datetime.now(timezone.utc).date().isoformat()
            if payload.get("purchase_date") is None
            else payload.get("purchase_date")
        )
        notes = str(payload.get("notes") or "")[:500]
        if linked_quote and (linked_quote["status"] != "active" or not linked_quote["quote_date"] <= purchase_date <= linked_quote["valid_until"]):
            raise ValueError("The quote must be active and valid on the planned purchase date.")
        total_cost = costs["total_cost"]
        total_cost_usd = costs["total_cost_usd"]
        budget_variance = round(total_cost - budget_amount, 6) if budget_amount is not None else None
        budget_variance_usd = round(budget_variance * exchange_rate, 6) if budget_variance is not None else None

        market_row = next((row for row in self.latest_by_instrument() if row["instrument"] == DEFAULT_INSTRUMENTS[product]), None)
        with self._lock, self.connect() as conn:
            if linked_quote:
                current = conn.execute("SELECT version FROM supplier_quotes WHERE id=?", (linked_quote["id"],)).fetchone()
                if current is None or dict(current)["version"] != linked_quote["version"]:
                    raise QuoteConflict("This quote has changed. Reload it before preparing the budget.")
            market_price_usd = float(market_row["price"]) if market_row else None
            unit_price_usd = unit_price * exchange_rate
            price_impact_unit_usd = (
                round(unit_price_usd - market_price_usd, 6) if market_price_usd is not None else None
            )
            price_impact_total_usd = (
                round(price_impact_unit_usd * quantity, 6)
                if price_impact_unit_usd is not None
                else None
            )
            price_impact_pct = (
                round((price_impact_unit_usd / market_price_usd) * 100, 4)
                if price_impact_unit_usd is not None and market_price_usd
                else None
            )
            created_at = self._datetime_value(datetime.now(timezone.utc).replace(microsecond=0).isoformat())
            columns = ("product", "supplier", "quantity", "unit", "currency", "unit_price", "exchange_rate",
                       "transport_cost", "budget_amount", "purchase_date", "total_cost", "total_cost_usd",
                       "budget_variance", "budget_variance_usd", "market_price_usd", "price_impact_unit_usd",
                       "price_impact_total_usd", "price_impact_pct", "notes", "created_at",
                       *EXTRA_COST_FIELDS, "other_costs_json", "quote_id", "quote_version", "quote_snapshot_json")
            values = (product, supplier, quantity, unit, currency, unit_price, exchange_rate,
                      transport_cost, budget_amount, purchase_date, total_cost, total_cost_usd,
                      budget_variance, budget_variance_usd, market_price_usd, price_impact_unit_usd,
                      price_impact_total_usd, price_impact_pct, notes, created_at,
                      *[costs[key] for key in EXTRA_COST_FIELDS], json_text(costs["other_costs"]),
                      linked_quote["id"] if linked_quote else None, linked_quote["version"] if linked_quote else None,
                      json_text(linked_quote) if linked_quote else None)
            cur = conn.execute(f"INSERT INTO procurement_purchases({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)
            record = conn.execute("SELECT * FROM procurement_purchases WHERE id=?", (cur.lastrowid,)).fetchone()
            return self._procurement_record(record)

    @staticmethod
    def _procurement_record(record) -> dict:
        row = dict(record)
        row["other_costs"] = json.loads(row.pop("other_costs_json", None) or "[]")
        row["quote_snapshot"] = json.loads(row.pop("quote_snapshot_json", None) or "null")
        quantity, price, rate = float(row["quantity"]), float(row["unit_price"]), float(row["exchange_rate"])
        breakdown = [{"key": "merchandise", "label": "Marchandise", "amount": round(quantity * price, 6)}]
        breakdown += [{"key": key, "label": label, "amount": float(row.get(key) or 0)} for key, label in COST_FIELDS.items()]
        breakdown += [{"key": "other", **item} for item in row["other_costs"]]
        for item in breakdown:
            item["amount_usd"] = round(item["amount"] * rate, 6)
        row.update({"cost_breakdown": breakdown, "merchandise_cost": breakdown[0]["amount"],
                    "additional_cost": round(sum(item["amount"] for item in breakdown[1:]), 6),
                    "landed_unit_cost_usd": round(float(row["total_cost_usd"]) / quantity, 6)})
        return row

    def procurements(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM procurement_purchases
                ORDER BY purchase_date DESC, id DESC LIMIT ?""",
                (safe_limit,),
            ).fetchall()
            return [self._procurement_record(row) for row in rows]

    def history(
        self,
        product: str | None = None,
        supplier: str | None = None,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        page: int = 1,
        page_size: int = 50,
        data_kind: str | None = None,
        instrument: str | None = None,
    ) -> dict[str, Any]:
        clauses, params = self._history_filters(
            product, supplier, source, date_from, date_to, min_price, max_price, data_kind, instrument
        )
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 10_000))
        where = " AND ".join(clauses) if clauses else "1=1"
        offset = (page - 1) * page_size
        with self.connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM ({MARKET_READINGS_SQL}) readings WHERE {where}", params
            ).fetchone()
            rows = conn.execute(
                f"""SELECT * FROM ({MARKET_READINGS_SQL}) readings WHERE {where}
                ORDER BY source_date DESC, collected_at DESC, id DESC LIMIT ? OFFSET ?""",
                [*params, page_size, offset],
            ).fetchall()
        total = int(total_row["count"] if isinstance(total_row, sqlite3.Row) else total_row["count"])
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": math.ceil(total / page_size) if total else 0,
        }

    def iter_history(
        self, product: str | None = None, supplier: str | None = None, source: str | None = None,
        date_from: str | None = None, date_to: str | None = None,
        min_price: float | None = None, max_price: float | None = None,
        data_kind: str | None = None, instrument: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Read every matching record from one query, without a page-size cutoff."""
        clauses, params = self._history_filters(
            product, supplier, source, date_from, date_to, min_price, max_price, data_kind, instrument
        )
        where = " AND ".join(clauses) if clauses else "1=1"
        connection = self.connect()
        try:
            query = f"""SELECT * FROM ({MARKET_READINGS_SQL}) readings WHERE {where}
                ORDER BY source_date DESC, collected_at DESC, id DESC"""
            cursor = (connection.execute(query, params, buffered=False)
                      if isinstance(connection, _MySQLConnection) else connection.execute(query, params))
            while batch := cursor.fetchmany(1000):
                for row in batch:
                    yield dict(row)
        finally:
            # Closing the read connection also handles an export cancelled
            # before its unbuffered MySQL result was completely consumed.
            connection.close()

    def compare_periods(
        self,
        period_a_from: str,
        period_a_to: str,
        period_b_from: str,
        period_b_to: str,
        product: str | None = None,
        supplier: str | None = None,
        source: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        data_kind: str | None = None,
        instrument: str | None = None,
    ) -> dict[str, Any]:
        period_a_from = self._source_date_value(period_a_from)
        period_a_to = self._source_date_value(period_a_to)
        period_b_from = self._source_date_value(period_b_from)
        period_b_to = self._source_date_value(period_b_to)
        if period_a_from > period_a_to or period_b_from > period_b_to:
            raise ValueError("Start date must be before end date.")

        first = list(self.iter_history(
            product, supplier, source, period_a_from, period_a_to, min_price, max_price,
            data_kind=data_kind, instrument=instrument,
        ))
        second = list(self.iter_history(
            product, supplier, source, period_b_from, period_b_to, min_price, max_price,
            data_kind=data_kind, instrument=instrument,
        ))

        first = canonical_daily_rows([r for r in first if r["product"] != "bitume"]) + [r for r in first if r["product"] == "bitume"]
        second = canonical_daily_rows([r for r in second if r["product"] != "bitume"]) + [r for r in second if r["product"] == "bitume"]
        has_index = any(row.get("data_kind") == "index" for row in first + second)
        identities = {"gasoil": "diesel_spot", "brent": "brent_spot", "bitume": "asphalt_index" if has_index else "bitumen_quote"}
        for row in first + second:
            identity = row["instrument"]
            if identity not in identities.values():
                identities["bitume_quotes" if identity == "bitumen_quote" else identity] = identity

        def summarize(rows, identity):
            values = [float(row["price"]) for row in rows if row["instrument"] == identity]
            return {"count": len(values), "average": round(sum(values) / len(values), 6) if values else None,
                    "min": min(values) if values else None, "max": max(values) if values else None}

        products = {}
        for key, identity in identities.items():
            a, b = summarize(first, identity), summarize(second, identity)
            change = round(b["average"] - a["average"], 6) if a["average"] is not None and b["average"] is not None else None
            products[key] = {
                "label": INSTRUMENTS[identity]["label"], "unit": INSTRUMENTS[identity]["unit"],
                "instrument": identity, "data_kind": "index" if identity == "asphalt_index" else "quote",
                "period_a": a, "period_b": b, "average_change": change,
                "average_change_pct": round(change / a["average"] * 100, 4) if change is not None and a["average"] else None,
            }
        return {
            "period_a": {"from": period_a_from, "to": period_a_to},
            "period_b": {"from": period_b_from, "to": period_b_to},
            "products": products,
        }

    def _history_filters(
        self,
        product: str | None,
        supplier: str | None,
        source: str | None,
        date_from: str | None,
        date_to: str | None,
        min_price: float | None,
        max_price: float | None,
        data_kind: str | None = None,
        instrument: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if product:
            product = product.strip().lower()
            if product not in PRODUCTS:
                raise ValueError("Unknown product. Use gasoil, brent, or bitume.")
            clauses.append("product=?")
            params.append(product)
        if supplier:
            clauses.append("supplier LIKE ?")
            params.append(f"%{supplier.strip()}%")
        if source:
            clauses.append("source LIKE ?")
            params.append(f"%{source.strip()}%")
        if date_from:
            clauses.append("source_date >= ?")
            params.append(self._source_date_value(date_from))
        if date_to:
            clauses.append("source_date <= ?")
            params.append(self._source_date_value(date_to))
        numeric_filters: list[tuple[str, Any, str]] = [
            ("price >= ?", min_price, "Minimum price"),
            ("price <= ?", max_price, "Maximum price"),
        ]
        for clause, raw, label in numeric_filters:
            if raw is None or raw == "":
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{label} must be numeric.") from None
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{label} must be non-negative.")
            clauses.append(clause)
            params.append(value)
        if min_price is not None and max_price is not None and float(min_price) > float(max_price):
            raise ValueError("Minimum price must be lower than maximum price.")
        if data_kind:
            if data_kind not in {"quote", "index"}:
                raise ValueError("Data type must be quote or index.")
            clauses.append("data_kind=?")
            params.append(data_kind)
        if instrument:
            if instrument not in INSTRUMENTS or (product and INSTRUMENTS[instrument]["product"] != product):
                raise ValueError("Instrument is incompatible with the product.")
            clauses.append("instrument=?")
            params.append(instrument)
        return clauses, params

    def log_collection(self, status: str, rows: int, message: str, started_at: str, finished_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO collection_logs(started_at,finished_at,status,rows_collected,message) VALUES(?,?,?,?,?)",
                (self._datetime_value(started_at), self._datetime_value(finished_at), status, rows, message[:500]),
            )

    def update_source_health(
        self,
        source_code: str,
        success: bool,
        at: str,
        error: str | None = None,
        quote_at: str | None = None,
    ) -> None:
        """Persist the latest outcome for one configured source."""
        with self._lock, self.connect() as conn:
            if success:
                quote_value = self._datetime_value(quote_at) if quote_at else None
                conn.execute(
                    """UPDATE data_sources SET last_success_at=?, last_error=NULL,
                    last_quote_at=CASE WHEN ? IS NOT NULL AND (last_quote_at IS NULL OR last_quote_at < ?)
                        THEN ? ELSE last_quote_at END WHERE code=?""",
                    (self._datetime_value(at), quote_value, quote_value, quote_value, source_code),
                )
            else:
                conn.execute(
                    "UPDATE data_sources SET last_error=? WHERE code=?",
                    (str(error or "Collection failed")[:500], source_code),
                )

    def source_health(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT code, label, provider, frequency, active, last_success_at, last_error, last_quote_at
                FROM data_sources WHERE active=1 ORDER BY id"""
            ).fetchall()
            labels = {
                "fred_diesel": ("Diesel", "EIA/FRED - DDFUELNYH"),
                "fred_brent": ("Brent crude oil", "EIA/FRED - DCOILBRENTEU"),
                "alpha_brent": ("Brent crude oil", "Alpha Vantage - BRENT"),
                "eia_diesel": ("Diesel", "U.S. EIA - PET.DDFUELNYH.D"),
                "eia_brent": ("Brent crude oil", "U.S. EIA - PET.RBRTE.D"),
                "live_gasoil_proxy": ("Diesel / heating oil proxy", "Yahoo Finance - Heating Oil HO=F (live proxy)"),
                "live_brent": ("Brent crude oil", "Yahoo Finance - Brent futures BZ=F"),
                "internal_bitumen": ("Bitumen", "Internal quotes and invoices"),
                "bls_asphalt_ppi": ("Bitumen/asphalt index (proxy)", "U.S. BLS - WPU058"),
            }
            result = [dict(row) for row in rows]
            for item in result:
                if item["code"] in labels:
                    item["label"], item["provider"] = labels[item["code"]]
            return result

    def latest(self) -> list[dict[str, Any]]:
        """Latest spot benchmarks and supplier quote; never substitute a proxy."""
        rows = {row["product"]: row for row in self.latest_by_instrument()
                if row["instrument"] == DEFAULT_INSTRUMENTS[row["product"]]}
        return [rows[product] for product in PRODUCTS if product in rows]

    def observations(self, product: str | None = None, days: int = 30, limit: int | None = 600, instrument: str | None = None, daily: bool = False) -> list[dict[str, Any]]:
        """Return the newest records in chronological order; aggregate before limiting."""
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        clauses, params = self._history_filters(product, None, None, cutoff, None, None, None, instrument=instrument)
        selection = f"SELECT * FROM ({MARKET_READINGS_SQL}) readings WHERE {' AND '.join(clauses)}"
        if daily:
            selection = f"""SELECT * FROM (
                SELECT filtered.*, ROW_NUMBER() OVER (
                    PARTITION BY instrument, source_date
                    ORDER BY {DAILY_SOURCE_PRIORITY_SQL}, collected_at DESC, id DESC
                ) AS day_rank FROM ({selection}) filtered
            ) daily_readings WHERE day_rank=1"""
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM ({selection}) selected
                ORDER BY source_date DESC, collected_at DESC, id DESC{limit_sql}""",
                params,
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def bitumen_series(self, days: int = 1825, include_anchor: bool = False) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        date_filter = "source_date>=?"
        params = [cutoff]
        if include_anchor:
            date_filter += " OR source_date=(SELECT MAX(source_date) FROM market_benchmarks WHERE code=? AND source_date<?)"
            params.extend([INDEX_CODE, cutoff])
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM ({MARKET_READINGS_SQL}) readings WHERE data_kind='index' AND ({date_filter}) ORDER BY source_date, collected_at",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_by_instrument(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT ranked.* FROM (
                    SELECT p.*, ROW_NUMBER() OVER (
                        PARTITION BY instrument, source
                        ORDER BY source_date DESC, collected_at DESC, id DESC
                    ) AS source_rank FROM price_observations p
                ) ranked WHERE source_rank=1"""
            ).fetchall()
        return canonical_latest([dict(row) for row in rows])

    def source_comparison(self) -> dict[str, Any]:
        """Read one latest quote per API, independently of chart filters.

        Sorting by publication date first prevents a later fetch of an older
        daily observation from replacing a newer recorded market value.
        ROW_NUMBER is supported by both SQLite and the MySQL 8 backend.
        """
        labels = tuple(SOURCE_CODE_BY_LABEL)
        placeholders = ",".join("?" for _ in labels)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT ranked.* FROM (
                    SELECT p.*, ROW_NUMBER() OVER (
                        PARTITION BY product, source
                        ORDER BY source_date DESC, collected_at DESC, id DESC
                    ) AS source_rank
                    FROM price_observations p WHERE source IN ({placeholders})
                ) ranked WHERE source_rank=1""",
                labels,
            ).fetchall()
        return compare_api_prices(
            [dict(row) for row in rows], self.latest_benchmarks(), self.source_health(),
        )

    def dashboard(self, days: int = 30, instruments: dict[str, str] | None = None) -> dict[str, Any]:
        latest = self.latest_by_instrument()
        selected = {}
        for product in ("gasoil", "brent"):
            requested = (instruments or {}).get(product)
            if requested:
                selected[product] = resolve_instrument(product, instrument=requested)
            else:
                live = LIVE_INSTRUMENTS[product]
                selected[product] = live if any(r["instrument"] == live for r in latest) else DEFAULT_INSTRUMENTS[product]
        rows = canonical_daily_rows([
            row for product, instrument in selected.items()
            for row in self.observations(product, days=days, limit=None, instrument=instrument, daily=True)
        ]) + self.observations("bitume", days=days, limit=None, instrument="bitumen_quote")
        latest = [r for r in latest if r["product"] == "bitume" or r["instrument"] == selected.get(r["product"])]
        supplier_quote = next((row for row in latest if row["product"] == "bitume"), None)
        index_rows = self.bitumen_series(days, include_anchor=True)
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        for row in index_rows:
            if str(row["source_date"])[:10] < cutoff:
                row["chart_date"] = cutoff
                row["is_period_anchor"] = True
        if index_rows:
            rows = [row for row in rows if row["product"] != "bitume"] + index_rows
            latest = [row for row in latest if row["product"] != "bitume"] + [index_rows[-1]]
        source_health = self.source_health()
        live_sources = {row["code"]: row for row in source_health}
        market = market_session(datetime.now(timezone.utc))
        by_product: dict[str, list[dict[str, Any]]] = {p: [] for p in PRODUCTS}
        for row in rows:
            by_product.setdefault(row["product"], []).append(row)
        metrics = {}
        for product, meta in PRODUCTS.items():
            values = [float(r["price"]) for r in by_product.get(product, [])]
            current = next((r for r in latest if r["product"] == product), None)
            current_source_date = str(current["source_date"])[:10] if current else None
            age_days = (
                (datetime.now(timezone.utc).date() - date.fromisoformat(current_source_date)).days
                if current_source_date
                else None
            )
            freshness_days = 75 if current and current.get("data_kind") == "index" else 45 if product == "bitume" else 7
            live_code = {"gasoil": "live_gasoil_proxy", "brent": "live_brent"}.get(product)
            live_quote_at = live_sources.get(live_code, {}).get("last_quote_at") if live_code else None
            current_is_live_feed = bool(current and str(current["source"]).startswith("Yahoo Finance - "))
            normalized_quote_at = None
            try:
                if current_is_live_feed and live_quote_at:
                    parsed_quote_at = live_quote_at if isinstance(live_quote_at, datetime) else datetime.fromisoformat(str(live_quote_at).replace("Z", "+00:00"))
                    if parsed_quote_at.tzinfo is None:
                        parsed_quote_at = parsed_quote_at.replace(tzinfo=timezone.utc)
                    normalized_quote_at = parsed_quote_at.astimezone(timezone.utc).isoformat()
                    quote_age = (datetime.now(timezone.utc) - parsed_quote_at.astimezone(timezone.utc)).total_seconds() / 60
                else:
                    quote_age = None
            except (TypeError, ValueError, OverflowError):
                quote_age = None
            metrics[product] = {
                "label": INSTRUMENTS[current["instrument"]]["label"] if current else INSTRUMENTS[selected[product]]["label"] if product in selected else meta["label"],
                "instrument": current["instrument"] if current else selected.get(product, "bitumen_quote"),
                "unit": current["unit"] if current else meta["unit"], "color": meta["color"],
                "data_kind": current.get("data_kind", "quote") if current else "quote",
                "frequency": "monthly" if current and current.get("data_kind") == "index" else "daily",
                "supplier_quote": supplier_quote if product == "bitume" else None,
                "current": float(current["price"]) if current else None,
                "variation": float(current["variation_pct"]) if current and current["variation_pct"] is not None else None,
                "source_date": current_source_date,
                "source": str(current["source"]) if current else None,
                "collected_at": current["collected_at"] if current else None,
                "quote_at": normalized_quote_at,
                "is_live": bool(market["open"] and quote_age is not None and 0 <= quote_age <= 5),
                "market_closed": current_is_live_feed and not market["open"],
                "next_market_open_at": market["next_open_at"].isoformat() if current_is_live_feed and not market["open"] else None,
                "quote_age_minutes": quote_age,
                "age_days": age_days,
                "is_stale": age_days is not None and age_days > freshness_days,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "average": round(sum(values) / len(values), 4) if values else None,
                "count": len(values),
            }
        active_sources = {row["source"] for row in rows if row.get("source")}
        quality_score = self._quality_score(rows, source_health)
        return {
            "days": days,
            "instruments": INSTRUMENTS,
            "selected_instruments": selected,
            "metrics": metrics,
            "latest": latest,
            "series": by_product,
            "quality": {
                "score": quality_score,
                "source_count": len(active_sources),
                "observation_count": len(rows),
            },
            "source_health": source_health,
            "source_comparison": self.source_comparison(),
            "demo_mode": DEMO_MODE,
        }

    @classmethod
    def _quality_score(cls, rows: list[dict[str, Any]], sources: list[dict[str, Any]]) -> int:
        """Score stored data and the health of sources that have been checked.

        Sources that have never been collected are intentionally excluded from
        the health denominator: manually maintained and not-yet-configured
        sources should not make an otherwise valid dataset look unhealthy.
        """
        if not rows:
            return 0

        valid_rows = sum(cls._observation_is_valid(row) for row in rows)
        data_score = (valid_rows / len(rows)) * 100

        monitored = [
            source
            for source in sources
            if source.get("last_success_at") is not None or source.get("last_error")
        ]
        if not monitored:
            source_score = 100.0
        else:
            healthy = sum(cls._source_is_healthy(source) for source in monitored)
            source_score = (healthy / len(monitored)) * 100

        return max(0, min(100, round((data_score + source_score) / 2)))

    @staticmethod
    def _observation_is_valid(row: dict[str, Any]) -> bool:
        required = ("product", "price", "unit", "currency", "source", "source_date", "collected_at")
        if row.get("data_kind") == "index":
            required = tuple(field for field in required if field != "currency")
        if any(row.get(field) in (None, "") for field in required):
            return False
        try:
            price = float(row["price"])
            if price <= 0 or not math.isfinite(price):
                return False
            date.fromisoformat(str(row["source_date"])[:10])
            collected_at = row["collected_at"]
            if isinstance(collected_at, datetime):
                parsed = collected_at
            else:
                parsed = datetime.fromisoformat(str(collected_at).replace("Z", "+00:00"))
            return parsed.tzinfo is not None or isinstance(collected_at, datetime)
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _source_is_healthy(source: dict[str, Any]) -> bool:
        if source.get("last_error") or not source.get("last_success_at"):
            return False
        try:
            value = source["last_success_at"]
            if isinstance(value, datetime):
                success_at = value
                if success_at.tzinfo is None:
                    success_at = success_at.replace(tzinfo=timezone.utc)
            else:
                success_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if success_at.tzinfo is None:
                    success_at = success_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - success_at.astimezone(timezone.utc)
            freshness_window = {"temps réel": timedelta(minutes=5), "quotidienne": timedelta(days=2), "mensuelle": timedelta(days=62)}.get(
                str(source.get("frequency", "")).lower(), timedelta(days=365)
            )
            return age <= freshness_window
        except (TypeError, ValueError, OverflowError):
            return False

    def logs(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM collection_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def collection_logs_after(self, last_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM collection_logs WHERE id > ? ORDER BY id ASC", (last_id,)
            ).fetchall()]

    def get_alert_state(self, alert_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT alert_key, active, `last_value`, last_notified_at, updated_at, last_status FROM alert_states WHERE alert_key=?",
                (alert_key,),
            ).fetchone()
            return dict(row) if row else None

    def set_alert_state(
        self,
        alert_key: str,
        active: bool,
        last_value: float | None,
        updated_at: str,
        last_notified_at: str | None = None,
        last_status: str | None = None,
        clear_notified: bool = False,
    ) -> None:
        with self._lock, self.connect() as conn:
            notified_value = self._datetime_value(last_notified_at) if last_notified_at else None
            existing = conn.execute(
                "SELECT alert_key FROM alert_states WHERE alert_key=?", (alert_key,)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE alert_states
                    SET active=?, `last_value`=?, last_notified_at=CASE WHEN ? THEN NULL ELSE COALESCE(?, last_notified_at) END,
                        updated_at=?, last_status=COALESCE(?, last_status)
                    WHERE alert_key=?""",
                    (int(active), last_value, int(clear_notified), notified_value, self._datetime_value(updated_at), last_status, alert_key),
                )
            else:
                conn.execute(
                    """INSERT INTO alert_states
                    (alert_key, active, `last_value`, last_notified_at, updated_at, last_status)
                    VALUES(?,?,?,?,?,?)""",
                    (alert_key, int(active), last_value, notified_value, self._datetime_value(updated_at), last_status or "normal"),
                )

    @staticmethod
    def _alert_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off"}:
            return False
        raise ValueError("The muted field must be boolean.")

    @classmethod
    def _validate_alert_rule(cls, payload: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
        current = current or {}
        product = str(payload.get("product", current.get("product", ""))).strip().lower()
        if product not in PRODUCTS:
            raise ValueError("Unknown product. Use gasoil, brent, or bitume.")
        direction = str(payload.get("direction", current.get("direction", ""))).strip().lower()
        if direction not in {"above", "below", "stale"}:
            raise ValueError("Condition must be above, below, or stale.")
        if direction == "stale" and product not in {"gasoil", "brent"}:
            raise ValueError("Live feed monitoring applies to diesel and Brent.")
        basis = str(payload.get("basis", current.get("basis", "quote"))).strip().lower()
        if basis not in {"quote", "index"} or (basis == "index" and (product != "bitume" or direction == "stale")):
            raise ValueError("Index basis applies only to bitumen index thresholds.")
        same_basis = product == current.get("product") and basis == current.get("basis", "quote") and (direction == "stale") == (current.get("direction") == "stale")
        default_instrument = LIVE_INSTRUMENTS[product] if direction == "stale" else "asphalt_index" if basis == "index" else DEFAULT_INSTRUMENTS[product]
        instrument = resolve_instrument(
            product, instrument=payload.get("instrument") or (current.get("instrument") if same_basis else None) or default_instrument,
            index=basis == "index",
        )
        if direction == "stale" and instrument != LIVE_INSTRUMENTS[product]:
            raise ValueError("Freshness alerts must track the live futures contract.")
        threshold_input = payload.get("threshold", current.get("threshold"))
        try:
            threshold = float(threshold_input)
        except (TypeError, ValueError):
            raise ValueError("Threshold must be numeric.") from None
        if not math.isfinite(threshold) or threshold <= 0 or threshold > 1_000_000:
            raise ValueError("Threshold must be between 0 and 1,000,000.")
        if direction == "stale" and (threshold < 2 or threshold > 1440 or not threshold.is_integer()):
            raise ValueError("The no-new-quote interval must be an integer from 2 to 1,440 minutes.")
        channel = str(payload.get("channel", current.get("channel", "webhook"))).strip().lower()
        if channel not in {"webhook", "email", "both"}:
            raise ValueError("Channel must be webhook, email, or both.")
        if direction == "stale" and channel not in {"email", "both"}:
            raise ValueError("A freshness rule must include email notification.")
        return {
            "product": product,
            "basis": basis,
            "instrument": instrument,
            "direction": direction,
            "threshold": threshold,
            "channel": channel,
            "muted": cls._alert_bool(payload.get("muted", current.get("muted", False))),
            "enabled": cls._alert_bool(payload.get("enabled", current.get("enabled", True)), True),
        }

    @staticmethod
    def _alert_key(product: str, direction: str, threshold: float, basis: str = "quote", instrument: str | None = None) -> str:
        if direction == "stale":
            return f"live_stale:{product}:{threshold:g}"
        identity = instrument or ("asphalt_index" if basis == "index" else DEFAULT_INSTRUMENTS[product])
        return price_alert_key(product, identity, direction, threshold)

    def alert_rules(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        where = "" if include_disabled else " WHERE r.enabled=1"
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT r.* FROM alert_rules r{where} ORDER BY r.product, CASE r.direction WHEN 'above' THEN 1 WHEN 'below' THEN 2 ELSE 3 END, r.id"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for raw in rows:
                row = dict(raw)
                key = self._alert_key(row["product"], row["direction"], float(row["threshold"]), row["basis"], row["instrument"])
                state_row = conn.execute(
                    "SELECT active, `last_value`, last_notified_at, updated_at, last_status FROM alert_states WHERE alert_key=?",
                    (key,),
                ).fetchone()
                state = dict(state_row) if state_row else {}
                status = "muted" if bool(row["muted"]) else (
                    "active" if bool(state.get("active")) else
                    "recovered" if state.get("last_status") == "recovered" else "normal"
                )
                row.update({
                    "id": int(row["id"]),
                    "threshold": float(row["threshold"]),
                    "muted": bool(row["muted"]),
                    "enabled": bool(row["enabled"]),
                    "status": status,
                    "alert_key": key,
                    "active": bool(state.get("active")),
                    "last_value": state.get("last_value"),
                    "last_notified_at": state.get("last_notified_at"),
                    "last_evaluated_at": state.get("updated_at"),
                })
                result.append(row)
            return result

    def create_alert_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        rule = self._validate_alert_rule(payload)
        now = self._datetime_value(datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        with self._lock, self.connect() as conn:
            duplicate = conn.execute(
                "SELECT id FROM alert_rules WHERE product=? AND instrument=? AND direction=? AND basis=?",
                (rule["product"], rule["instrument"], rule["direction"], rule["basis"]),
            ).fetchone()
            if duplicate:
                raise ValueError("A rule already exists for this product and direction.")
            cursor = conn.execute(
                """INSERT INTO alert_rules
                (product, instrument, direction, threshold, channel, muted, enabled, created_at, updated_at, basis)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (rule["product"], rule["instrument"], rule["direction"], rule["threshold"], rule["channel"], int(rule["muted"]), int(rule["enabled"]), now, now, rule["basis"]),
            )
            rule_id = getattr(cursor, "lastrowid", None)
            if not rule_id:
                rule_id = conn.execute("SELECT MAX(id) FROM alert_rules").fetchone()[0]
        return next(item for item in self.alert_rules() if item["id"] == int(rule_id))

    def update_alert_rule(self, rule_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rule_id = int(rule_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid rule ID.") from None
        now = self._datetime_value(datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        with self._lock, self.connect() as conn:
            existing_row = conn.execute("SELECT * FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
            if not existing_row:
                raise ValueError("Rule not found.")
            existing = dict(existing_row)
            rule = self._validate_alert_rule(payload, existing)
            duplicate = conn.execute(
                "SELECT id FROM alert_rules WHERE product=? AND instrument=? AND direction=? AND basis=? AND id<>?",
                (rule["product"], rule["instrument"], rule["direction"], rule["basis"], rule_id),
            ).fetchone()
            if duplicate:
                raise ValueError("A rule already exists for this product and direction.")
            conn.execute(
                """UPDATE alert_rules SET product=?, instrument=?, direction=?, threshold=?, channel=?, muted=?, enabled=?, updated_at=?, basis=?
                WHERE id=?""",
                (rule["product"], rule["instrument"], rule["direction"], rule["threshold"], rule["channel"], int(rule["muted"]), int(rule["enabled"]), now, rule["basis"], rule_id),
            )
        return next(item for item in self.alert_rules() if item["id"] == rule_id)

    def delete_alert_rule(self, rule_id: int) -> None:
        try:
            rule_id = int(rule_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid rule ID.") from None
        with self._lock, self.connect() as conn:
            deleted = conn.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,)).rowcount
            if not deleted:
                raise ValueError("Rule not found.")


def _get_mysql_connector() -> Any:
    try:
        import mysql.connector
    except ImportError as exc:
        raise RuntimeError("Installez mysql-connector-python pour utiliser MySQL.") from exc
    return mysql.connector


class _MySQLConnection:
    """Small adapter exposing the SQLite connection methods used by the backend."""

    def __init__(self, connection: Any):
        self._connection = connection

    def __enter__(self) -> "_MySQLConnection":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        self._connection.close()

    @staticmethod
    def _sql(statement: str) -> str:
        return statement.replace("?", "%s")

    def execute(self, statement: str, params: Any = (), *, buffered: bool = True) -> Any:
        cursor = self._connection.cursor(dictionary=True, buffered=buffered)
        if params:
            cursor.execute(self._sql(statement), tuple(params))
        else:
            cursor.execute(self._sql(statement))
        return cursor

    def close(self) -> None:
        self._connection.close()

    def executemany(self, statement: str, params: Any) -> None:
        cursor = self._connection.cursor()
        cursor.executemany(self._sql(statement), params)
        cursor.close()

    def executescript(self, script: str) -> None:
        for statement in _split_sql_script(script):
            self.execute(statement)


class MySQLDatabase(SQLiteDatabase):
    """MySQL persistence backend using the same application-level contract."""

    def __init__(self, settings: MySQLSettings = MYSQL_SETTINGS):
        self.settings = settings
        self.initialize()

    def _ensure_database(self) -> None:
        connector = _get_mysql_connector()
        connection = connector.connect(**self.settings.connector_kwargs(include_database=False))
        cursor = connection.cursor()
        database_name = self.settings.database.replace("`", "``")
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
            f"CHARACTER SET {self.settings.charset} COLLATE {self.settings.charset}_unicode_ci"
        )
        connection.commit()
        cursor.close()
        connection.close()

    def connect(self) -> _MySQLConnection:
        borrowed = borrowed_connection(self)
        if borrowed is not None:
            return borrowed
        connector = _get_mysql_connector()
        return _MySQLConnection(connector.connect(**self.settings.connector_kwargs()))

    def healthcheck(self) -> dict[str, str]:
        with self.connect() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "backend": "mysql"}

    @staticmethod
    def _datetime_value(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    def initialize(self) -> None:
        self._ensure_database()
        with self.connect() as connection:
            connection.executescript(MYSQL_SCHEMA)
            run_mysql_migrations(connection)
            self._deduplicate_mysql_observations(connection)
            self._ensure_mysql_observation_identity(connection)
            self._seed_sources(connection)
            self._seed_supplier_channels(connection)
            count = connection.execute("SELECT COUNT(*) AS count FROM price_observations").fetchone()["count"]
            imported = self._seed_real_data(connection)
            self._seed_bitumen_data(connection)
            if count == 0 and imported == 0 and DEMO_MODE:
                self._seed_demo(connection)

    def _seed_sources(self, connection: _MySQLConnection) -> None:
        rows = [
            ("fred_diesel", "Gasoil / diesel", "EIA/FRED - DDFUELNYH", "quotidienne"),
            ("fred_brent", "Brent crude oil", "EIA/FRED - DCOILBRENTEU", "quotidienne"),
            ("alpha_brent", "Brent crude oil", "Alpha Vantage - BRENT", "quotidienne"),
            ("eia_diesel", "Gasoil / diesel", "U.S. EIA - PET.DDFUELNYH.D", "quotidienne"),
            ("eia_brent", "Brent crude oil", "U.S. EIA - PET.RBRTE.D", "quotidienne"),
            ("live_gasoil_proxy", "Diesel / heating oil proxy", "Yahoo Finance - Heating Oil HO=F (live proxy)", "temps réel"),
            ("live_brent", "Brent crude oil", "Yahoo Finance - Brent futures BZ=F", "temps réel"),
            ("internal_bitumen", "Bitumen", "Internal quotes and invoices", "à la demande"),
            ("bls_asphalt_ppi", "Bitumen/asphalt index (proxy)", "U.S. BLS - WPU058", "mensuelle"),
        ]
        connection.executemany(
            "INSERT IGNORE INTO data_sources(code, label, provider, frequency) VALUES(?,?,?,?)",
            rows,
        )

    def _seed_supplier_channels(self, connection: _MySQLConnection) -> None:
        connection.executemany(
            """INSERT IGNORE INTO supplier_channels
            (name, product, region, coverage, channel, typical_terms, specifications,
             website_url, contact_url, buyer_note, sort_order)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            SUPPLIER_CHANNEL_ROWS,
        )

    @staticmethod
    def _deduplicate_mysql_observations(connection: _MySQLConnection) -> None:
        connection.execute(
            """DELETE newer FROM price_observations AS newer
            INNER JOIN price_observations AS older
              ON older.product = newer.product
             AND older.source = newer.source
             AND older.instrument = newer.instrument
             AND older.source_date = newer.source_date
             AND older.price = newer.price
             AND older.id < newer.id"""
        )

    @staticmethod
    def _ensure_mysql_observation_identity(connection: _MySQLConnection) -> None:
        indexes = connection.execute(
            "SHOW INDEX FROM price_observations WHERE Key_name=?",
            ("uq_observation_identity",),
        ).fetchall()
        if not indexes:
            connection.execute(
                """ALTER TABLE price_observations
                ADD UNIQUE KEY uq_observation_identity(product, instrument, source, source_date, price)"""
            )


# Backwards-compatible name for callers that explicitly want the SQLite backend.
Database = SQLiteDatabase


def create_database() -> DatabaseBackend:
    """Create the configured persistence backend for the application."""
    if DB_BACKEND in {"sqlite", "sqlite3"}:
        return SQLiteDatabase()
    if DB_BACKEND in {"mysql", "mysql8"}:
        return MySQLDatabase()
    raise ValueError(f"Backend de base de données inconnu: {DB_BACKEND}")
