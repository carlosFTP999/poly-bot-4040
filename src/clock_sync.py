"""Offset-based clock synchronization with Polymarket CLOB server.

Periodically calibrates local clock against the server's /time endpoint
to prevent timestamp-related auth failures (e.g. "expired" errors).
"""

import time
import logging

import httpx

logger = logging.getLogger(__name__)

CALIBRATION_INTERVAL = 300  # 5 minutes
DRIFT_WARN_THRESHOLD = 30   # seconds


class ClockSync:
    """Offset-based clock sync with Polymarket CLOB server."""

    def __init__(self, base_url: str = "https://clob.polymarket.com"):
        self._base_url = base_url
        self._offset: float = 0.0
        self._calibrated_at: float = 0.0
        self._calibrate()

    def _calibrate(self) -> None:
        try:
            t1 = time.monotonic()
            resp = httpx.get(f"{self._base_url}/time", timeout=5.0)
            t4 = time.monotonic()
            resp.raise_for_status()
            server_time = float(resp.text.strip())
            rtt = t4 - t1
            self._offset = server_time - (t1 + rtt / 2)
            self._calibrated_at = t4
            if abs(self._offset) > DRIFT_WARN_THRESHOLD:
                logger.warning("Clock drift vs Polymarket server: %.1f seconds", self._offset)
            else:
                logger.info("Clock sync: offset=%.3fs, RTT=%.3fs", self._offset, rtt)
        except Exception as e:
            logger.warning("Clock calibration failed: %s — using offset=0", e)
            self._offset = 0.0

    def server_time(self) -> int:
        now = time.monotonic()
        if (now - self._calibrated_at) > CALIBRATION_INTERVAL:
            self._calibrate()
        return int(time.time() + self._offset)

    def force_recalibrate(self) -> int:
        self._calibrate()
        return self.server_time()
