"""Database backup commands for SQLite and MySQL deployments."""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import BACKUP_DIR, DB_BACKEND, DB_PATH, MYSQL_SETTINGS


def _default_destination() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = ".sql" if DB_BACKEND in {"mysql", "mysql8"} else ".db"
    return BACKUP_DIR / f"price_monitor-{stamp}{suffix}"


def backup_sqlite(destination: Path, source: Path = DB_PATH) -> Path:
    """Create a consistent SQLite backup using SQLite's online backup API."""
    if not source.exists():
        raise FileNotFoundError(f"Base SQLite introuvable: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    os.replace(temporary, destination)
    return destination


def backup_mysql(destination: Path) -> Path:
    """Create a transactional MySQL dump without putting the password in argv."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    command = [
        "mysqldump",
        f"--host={MYSQL_SETTINGS.host}",
        f"--port={MYSQL_SETTINGS.port}",
        f"--user={MYSQL_SETTINGS.user}",
        "--single-transaction",
        "--hex-blob",
        "--routines",
        "--events",
        "--set-gtid-purged=OFF",
        MYSQL_SETTINGS.database,
    ]
    environment = os.environ.copy()
    environment["MYSQL_PWD"] = MYSQL_SETTINGS.password
    try:
        with temporary.open("wb") as handle:
            subprocess.run(command, stdout=handle, stderr=subprocess.PIPE, check=True, env=environment)
        os.replace(temporary, destination)
    except subprocess.CalledProcessError as exc:
        if temporary.exists():
            temporary.unlink()
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"mysqldump a échoué: {detail}") from exc
    return destination


def backup_database(destination: Path | str | None = None) -> Path:
    target = Path(destination) if destination else _default_destination()
    if DB_BACKEND in {"sqlite", "sqlite3"}:
        return backup_sqlite(target)
    if DB_BACKEND in {"mysql", "mysql8"}:
        return backup_mysql(target)
    raise ValueError(f"Backend de base de données inconnu: {DB_BACKEND}")


def prune_backups(directory: Path, retention_days: int) -> list[Path]:
    """Remove old generated backups only when retention is explicitly requested."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed: list[Path] = []
    for path in directory.glob("price_monitor-*"):
        if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Créer une sauvegarde cohérente de Price Monitor.")
    parser.add_argument("--destination", type=Path, help="Chemin de sortie; sinon BACKUP_DIR et un horodatage sont utilisés.")
    parser.add_argument("--retention-days", type=int, help="Supprimer les sauvegardes générées plus anciennes que ce nombre de jours.")
    args = parser.parse_args()
    if args.retention_days is not None and args.retention_days < 1:
        parser.error("--retention-days doit être positif")
    output = backup_database(args.destination)
    print(output)
    if args.retention_days is not None:
        for removed in prune_backups(output.parent, args.retention_days):
            print(f"removed: {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
