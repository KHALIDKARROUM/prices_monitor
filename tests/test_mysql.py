from __future__ import annotations

import os
from pathlib import Path

import pytest

from price_monitor import config
from price_monitor import database as database_module
from price_monitor.config import MySQLSettings
from price_monitor.database import MySQLDatabase, create_database, run_mysql_migrations


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchall(self):
        return self.rows


class FakeMigrationConnection:
    def __init__(self):
        self.versions: set[str] = set()
        self.executed: list[str] = []

    def execute(self, statement, params=()):
        normalized = " ".join(statement.split()).lower()
        if normalized.startswith("select version from schema_migrations"):
            return FakeCursor([{"version": version} for version in sorted(self.versions)])
        if normalized.startswith("insert into schema_migrations"):
            self.versions.add(params[0])
            return FakeCursor()
        self.executed.append(statement)
        return FakeCursor()


def test_dotenv_loader_sets_missing_values_without_overwriting_environment(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "MYSQL_HOST=dotenv-host\n"
        "MYSQL_PASSWORD=\"quoted secret\"\n"
        "# ignored comment\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    monkeypatch.setenv("MYSQL_PASSWORD", "existing secret")

    config._load_dotenv(dotenv)

    assert os.environ["MYSQL_HOST"] == "dotenv-host"
    assert os.environ["MYSQL_PASSWORD"] == "existing secret"


def test_mysql_schema_uses_mysql_types_and_declares_all_tables():
    schema = (REPO_ROOT / "sql" / "mysql_schema.sql").read_text(encoding="utf-8")

    assert "CREATE DATABASE" not in schema
    assert "USE price_monitor" not in schema
    assert "AUTO_INCREMENT" in schema
    assert "AUTOINCREMENT" not in schema
    for table in ("price_observations", "data_sources", "collection_logs", "schema_migrations", "procurement_purchases", "market_benchmarks", "supplier_channels"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema


def test_mysql_migrations_are_tracked_and_idempotent():
    connection = FakeMigrationConnection()

    first_run = run_mysql_migrations(connection)
    second_run = run_mysql_migrations(connection)

    assert first_run == ["001", "002", "003", "004", "005", "006", "007", "008", "009", "010"]
    assert second_run == []
    assert connection.versions == {"001", "002", "003", "004", "005", "006", "007", "008", "009", "010"}
    assert sum("idx_collection_logs_status" in statement for statement in connection.executed) == 1


def test_mysql_settings_produce_connector_configuration():
    settings = MySQLSettings(
        host="db.example",
        port=3307,
        database="prices",
        user="app",
        password="secret",
    )

    assert settings.connector_kwargs() == {
        "host": "db.example",
        "port": 3307,
        "user": "app",
        "password": "secret",
        "charset": "utf8mb4",
        "database": "prices",
    }
    assert "database" not in settings.connector_kwargs(include_database=False)


def test_database_factory_selects_configured_mysql_backend(monkeypatch):
    class StubMySQLDatabase:
        pass

    monkeypatch.setattr(database_module, "DB_BACKEND", "mysql")
    monkeypatch.setattr(database_module, "MySQLDatabase", StubMySQLDatabase)

    assert isinstance(create_database(), StubMySQLDatabase)


@pytest.mark.mysql
@pytest.mark.skipif(
    os.getenv("RUN_MYSQL_TESTS") != "1",
    reason="Set RUN_MYSQL_TESTS=1 to run against the configured MySQL server",
)
def test_mysql_backend_applies_schema_and_migrations(monkeypatch):
    monkeypatch.setattr(database_module, "REAL_DATA_PATH", Path("__missing_real_data__.csv"))
    monkeypatch.setattr(database_module, "DEMO_MODE", False)

    first = MySQLDatabase()
    second = MySQLDatabase(first.settings)

    with second.connect() as connection:
        tables = {
            row["Tables_in_" + second.settings.database]
            for row in connection.execute("SHOW TABLES").fetchall()
        }
        versions = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }

    assert {"price_observations", "data_sources", "collection_logs", "schema_migrations", "alert_rules", "procurement_purchases", "market_benchmarks", "supplier_channels"} <= tables
    assert versions == {"001", "002", "003", "004", "005", "006", "007", "008", "009", "010"}
