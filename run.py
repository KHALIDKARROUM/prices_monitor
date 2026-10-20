"""Start Price Monitor with one command on Windows, macOS, or Linux."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
REQUIRED_MODULES = ("waitress", "argon2", "tzdata", "openpyxl", "mysql.connector", "apscheduler")


def venv_python() -> Path:
    """Create the project virtual environment when it does not exist."""
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or a newer version is required.")
    executable = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if executable.exists():
        return executable
    print("Creating the local Python environment...")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    if not executable.exists():
        raise RuntimeError("The local Python environment could not be created.")
    return executable


def dependencies_available(python: Path) -> bool:
    imports = "; ".join(f"import {name}" for name in REQUIRED_MODULES)
    return subprocess.run([str(python), "-c", imports], check=False).returncode == 0


def ensure_dependencies(python: Path) -> None:
    if dependencies_available(python):
        return
    print("Installing application dependencies...")
    subprocess.run([str(python), "-m", "pip", "install", "-e", "."], cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the local setup without starting the server")
    args = parser.parse_args()

    python = venv_python()
    ensure_dependencies(python)
    if args.check:
        print("Price Monitor is ready.")
        return 0

    environment = os.environ.copy()
    source_path = str(ROOT / "src")
    environment["PYTHONPATH"] = source_path + os.pathsep + environment.get("PYTHONPATH", "")
    print("Starting Price Monitor at http://127.0.0.1:8080")
    os.execvpe(str(python), [str(python), "-m", "price_monitor.app"], environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
