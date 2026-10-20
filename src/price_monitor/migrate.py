"""Explicit database initialization/migration command."""

from __future__ import annotations

from .config import DB_BACKEND
from .database import create_database


def main() -> int:
    # Database constructors are idempotent and apply all pending migrations.
    create_database()
    print(f"Database initialized and migrations applied ({DB_BACKEND}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
