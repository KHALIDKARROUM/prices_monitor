"""Check external reachability independently of the minute-by-minute collection."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..observability import logger


CHECK_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 2
# Use existing providers, with a fallback so one provider outage does not imply
# that the computer has lost its Internet connection. No price payload is read.
PROBE_URLS = (
    "https://fred.stlouisfed.org/",
    "https://query1.finance.yahoo.com/",
)


class WebsiteStatus:
    """Announce collection availability once per change in this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._available: bool | None = None
        self._internet_online: bool | None = None
        self._generation = 0

    def begin_collection(self) -> int:
        with self._lock:
            return self._generation

    def record_internet(self, online: bool) -> None:
        with self._lock:
            if not online:
                if self._internet_online is not False:
                    self._generation += 1
                self._announce(False)
            self._internet_online = online
            # Reachability alone cannot confirm that the price APIs work.
            # Wait for a collection after recovery before announcing success.

    def record_collection(self, available: bool, generation: int) -> None:
        with self._lock:
            # An in-flight request from before a network loss cannot recover
            # the website using a response from the previous connection.
            if generation != self._generation:
                return
            self._announce(available and self._internet_online is not False)

    def _announce(self, available: bool) -> None:
        if available == self._available:
            return
        self._available = available
        logger.info(
            "the website is up to date" if available else "no collections , try again",
            extra={"event": "website_status_changed", "status": "available" if available else "unavailable"},
        )


# Scheduled and manually triggered collections share the connectivity monitor's
# notification state; saved logs from a previous launch do not suppress startup.
website_status = WebsiteStatus()


def internet_reachable() -> bool:
    for url in PROBE_URLS:
        request = Request(url, method="HEAD", headers={"User-Agent": "PriceMonitor/1.0"})
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS):
                return True
        except HTTPError as response:
            # A provider refusing a request still proves external connectivity.
            response.close()
            return True
        except (URLError, OSError, ValueError):
            continue
    return False


class ConnectivityMonitor:
    def __init__(self, notifier: WebsiteStatus | None = None) -> None:
        self._notifier = notifier if notifier is not None else website_status
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status = "checking"
        self._checked_at: str | None = None
        self._changed_at: str | None = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self._status,
                "checked_at": self._checked_at,
                "changed_at": self._changed_at,
            }

    def check(self) -> None:
        status = "online" if internet_reachable() else "offline"
        checked_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            changed = status != self._status
            self._status = status
            self._checked_at = checked_at
            if changed:
                self._changed_at = checked_at
            self._notifier.record_internet(status == "online")
        if changed:
            logger.debug(
                "Internet connection restored" if status == "online" else "Internet connection unavailable",
                extra={"event": "connectivity_changed", "status": status},
            )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="internet-connectivity", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.check()
            except Exception:
                logger.exception("Connection check failed", extra={"event": "connectivity_check_failed"})
            if self._stop.wait(max(0.1, CHECK_INTERVAL_SECONDS - (time.monotonic() - started))):
                break
