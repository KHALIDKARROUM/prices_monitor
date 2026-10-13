from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
SOURCE_CHECKOUT = (PROJECT_DIR / "pyproject.toml").exists()
RUNTIME_DIR = PROJECT_DIR if SOURCE_CHECKOUT else Path.cwd()
STORAGE_DIR = BASE_DIR / "storage" if SOURCE_CHECKOUT else RUNTIME_DIR / "storage"
BACKUP_DIR = Path(os.getenv("PRICE_MONITOR_BACKUP_DIR", RUNTIME_DIR / "backups"))


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE settings without overwriting real environment values."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(PROJECT_DIR / ".env")
if not SOURCE_CHECKOUT:
    _load_dotenv(Path.cwd() / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un entier positif") from exc
    if value <= 0:
        raise ValueError(f"{name} doit être un entier positif")
    return value


def _env_nonnegative_int(name: str, default: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError as exc:
        raise ValueError(f"{name} doit être un entier positif ou nul") from exc
    if value < 0:
        raise ValueError(f"{name} doit être un entier positif ou nul")
    return value


def _env_json_object(name: str) -> dict[str, dict[str, float]]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} doit contenir un objet JSON valide") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} doit contenir un objet JSON")
    result: dict[str, dict[str, float]] = {}
    for product, rules in value.items():
        if not isinstance(product, str) or not isinstance(rules, dict):
            raise ValueError(f"{name} contient une règle invalide pour {product!r}")
        normalized: dict[str, float] = {}
        for direction, threshold in rules.items():
            if direction not in {"above", "below"}:
                raise ValueError(f"{name}: direction inconnue {direction!r}")
            try:
                normalized[direction] = float(threshold)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name}: seuil invalide pour {product}/{direction}") from exc
        result[product] = normalized
    return result


@dataclass(frozen=True)
class MySQLSettings:
    host: str
    port: int
    database: str
    user: str
    password: str
    charset: str = "utf8mb4"

    def connector_kwargs(self, include_database: bool = True) -> dict[str, object]:
        settings: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "charset": self.charset,
        }
        if include_database:
            settings["database"] = self.database
        return settings


DB_PATH = Path(os.getenv("PRICE_MONITOR_DB", STORAGE_DIR / "price_monitor.db"))
DEFAULT_DATA_PATH = (PROJECT_DIR / "data" / "processed" / "market_prices.csv") if SOURCE_CHECKOUT else (RUNTIME_DIR / "data" / "processed" / "market_prices.csv")
REAL_DATA_PATH = Path(os.getenv("PRICE_MONITOR_DATA", DEFAULT_DATA_PATH))
BITUMEN_DATA_PATH = Path(os.getenv("PRICE_MONITOR_BITUMEN_DATA", REAL_DATA_PATH.parent / "bitumen_index.csv"))
DB_BACKEND = os.getenv("PRICE_MONITOR_DB_BACKEND", "sqlite").strip().lower()
MYSQL_SETTINGS = MySQLSettings(
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    database=os.getenv("MYSQL_DATABASE", "price_monitor"),
    user=os.getenv("MYSQL_USER", "price_monitor"),
    password=os.getenv("MYSQL_PASSWORD", ""),
    charset=os.getenv("MYSQL_CHARSET", "utf8mb4"),
)

HOST = os.getenv("PRICE_MONITOR_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("PRICE_MONITOR_PORT", "8080"))
ENVIRONMENT = os.getenv("PRICE_MONITOR_ENV", "development").strip().lower() or "development"
API_KEY = os.getenv("PRICE_MONITOR_API_KEY", "").strip()
TEAM_MODE = _env_bool("PRICE_MONITOR_TEAM_MODE", ENVIRONMENT in {"production", "prod"} or HOST not in {"127.0.0.1", "localhost", "::1"})
PUBLIC_ORIGIN = os.getenv("PRICE_MONITOR_PUBLIC_ORIGIN", "").strip().rstrip("/")
SECURE_COOKIES = _env_bool("PRICE_MONITOR_SECURE_COOKIES", TEAM_MODE)
SESSION_HOURS = _env_positive_int("PRICE_MONITOR_SESSION_HOURS", 8)
SESSION_IDLE_MINUTES = _env_positive_int("PRICE_MONITOR_SESSION_IDLE_MINUTES", 30)
TRUSTED_PROXY = os.getenv("PRICE_MONITOR_TRUSTED_PROXY", "").strip()
URL_SCHEME = os.getenv("PRICE_MONITOR_URL_SCHEME", "http").strip().lower()
RATE_LIMIT_REQUESTS = _env_positive_int("PRICE_MONITOR_RATE_LIMIT_REQUESTS", 60)
RATE_LIMIT_WINDOW_SECONDS = _env_positive_int("PRICE_MONITOR_RATE_LIMIT_WINDOW_SECONDS", 60)
RATE_LIMIT_ENABLED = (
    ENVIRONMENT in {"production", "prod"}
    or _env_bool("PRICE_MONITOR_RATE_LIMIT_ENABLED")
)
DEMO_MODE = os.getenv("PRICE_MONITOR_DEMO", "false").lower() in {"1", "true", "yes", "on"}
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")
EIA_API_KEY = os.getenv("EIA_API_KEY", "").strip()
LIVE_QUOTES_ENABLED = _env_bool("PRICE_MONITOR_LIVE_QUOTES_ENABLED", True)
# Legacy setting now controls only live quotes (and demo data).
COLLECTION_INTERVAL_MINUTES = _env_positive_int("PRICE_MONITOR_COLLECTION_INTERVAL_MINUTES", 1)
PROVIDER_INTERVAL_MINUTES = {
    "fred": _env_positive_int("PRICE_MONITOR_FRED_INTERVAL_MINUTES", 360),
    "eia": _env_positive_int("PRICE_MONITOR_EIA_INTERVAL_MINUTES", 360),
    "alpha_vantage": _env_positive_int("PRICE_MONITOR_ALPHA_VANTAGE_INTERVAL_MINUTES", 720),
    "bls": _env_positive_int("PRICE_MONITOR_BLS_INTERVAL_MINUTES", 1440),
    "yahoo": _env_positive_int("PRICE_MONITOR_YAHOO_INTERVAL_MINUTES", COLLECTION_INTERVAL_MINUTES),
    "demo": COLLECTION_INTERVAL_MINUTES,
}
PROVIDER_DAILY_LIMITS = {
    "alpha_vantage": _env_positive_int("PRICE_MONITOR_ALPHA_VANTAGE_DAILY_LIMIT", 25),
    "bls": _env_positive_int("PRICE_MONITOR_BLS_DAILY_LIMIT", 25),
    "fred": _env_nonnegative_int("PRICE_MONITOR_FRED_DAILY_LIMIT"),
    "eia": _env_nonnegative_int("PRICE_MONITOR_EIA_DAILY_LIMIT"),
    "yahoo": _env_nonnegative_int("PRICE_MONITOR_YAHOO_DAILY_LIMIT"),
}
COLLECTION_BACKOFF_SECONDS = _env_positive_int("PRICE_MONITOR_COLLECTION_BACKOFF_SECONDS", 60)
COLLECTION_MAX_BACKOFF_SECONDS = _env_positive_int("PRICE_MONITOR_COLLECTION_MAX_BACKOFF_SECONDS", 21600)
# ISO timestamps with offsets; supports holiday, early-close and exceptional
# closure windows without assuming equity-market holidays apply to futures.
try:
    MARKET_CLOSURES = json.loads(os.getenv("PRICE_MONITOR_MARKET_CLOSURES", "[]"))
except json.JSONDecodeError as exc:
    raise ValueError("PRICE_MONITOR_MARKET_CLOSURES doit contenir une liste JSON") from exc
if not isinstance(MARKET_CLOSURES, list):
    raise ValueError("PRICE_MONITOR_MARKET_CLOSURES doit contenir une liste JSON")
SERVICE_NAME = os.getenv("PRICE_MONITOR_SERVICE_NAME", "price-monitor")
LOG_LEVEL = os.getenv("PRICE_MONITOR_LOG_LEVEL", "INFO").strip().upper()

# Alerts are opt-in. Thresholds use product keys and ``above``/``below`` rules,
# for example: {"brent":{"above":90,"below":70},"gasoil":{"above":3.5}}.
ALERT_THRESHOLDS = _env_json_object("PRICE_MONITOR_ALERT_THRESHOLDS")
ALERT_WEBHOOK_URL = os.getenv("PRICE_MONITOR_ALERT_WEBHOOK_URL", "").strip()
ALERT_WEBHOOK_TIMEOUT_SECONDS = _env_positive_int("PRICE_MONITOR_ALERT_WEBHOOK_TIMEOUT_SECONDS", 10)
ALERT_EMAIL_TO = tuple(
    address.strip()
    for address in os.getenv("PRICE_MONITOR_ALERT_EMAIL_TO", "").split(",")
    if address.strip()
)
ALERT_SMTP_HOST = os.getenv("PRICE_MONITOR_ALERT_SMTP_HOST", "").strip()
ALERT_SMTP_PORT = _env_positive_int("PRICE_MONITOR_ALERT_SMTP_PORT", 587)
ALERT_SMTP_USER = os.getenv("PRICE_MONITOR_ALERT_SMTP_USER", "").strip()
ALERT_SMTP_PASSWORD = os.getenv("PRICE_MONITOR_ALERT_SMTP_PASSWORD", "")
ALERT_SMTP_FROM = os.getenv("PRICE_MONITOR_ALERT_SMTP_FROM", "").strip()
ALERT_SMTP_USE_TLS = _env_bool("PRICE_MONITOR_ALERT_SMTP_USE_TLS", True)
ALERT_NOTIFY_RECOVERY = _env_bool("PRICE_MONITOR_ALERT_NOTIFY_RECOVERY", True)

# Operational email reports use the same SMTP account as price alerts, but
# have their own recipient and cadence.
DIGEST_EMAIL_TO = tuple(
    address.strip()
    for address in os.getenv("PRICE_MONITOR_DIGEST_EMAIL_TO", "").split(",")
    if address.strip()
)
DIGEST_INTERVAL_HOURS = _env_positive_int("PRICE_MONITOR_DIGEST_INTERVAL_HOURS", 5)
