"""
disconnect_watchdog.py — Center-Screen Disconnect Detection
============================================================
Background thread that polls the center-screen region of the Container
every 1–2 seconds looking for disconnect dialogs. On detection, signals
the reconnect pipeline.
"""

import logging
import threading
import time
from typing import Callable, Optional

from capture import ScreenCapture
from container import Container
from vision import VisionService

logger = logging.getLogger(__name__)


class DisconnectWatchdog:
    """
    Background watchdog that periodically checks the center of the container
    for disconnect/error dialogs.

    Design decisions:
    - Polls every 1–2 seconds (configurable), NOT every frame.
    - Scans only the center ~40% of the container (where Roblox shows dialogs).
    - Presence-only detection: any disconnect dialog triggers the same reconnect
      flow. No text parsing or branching by disconnect reason in this build.
    """

    # Center-screen region in normalized coords (center 40%)
    DEFAULT_REGION = (0.3, 0.3, 0.7, 0.7)

    def __init__(
        self,
        vision: VisionService,
        poll_interval: float = 2.0,
        on_disconnect: Optional[Callable[[], None]] = None,
        get_ocr_region: Optional[Callable[[], Optional[tuple[float, float, float, float]]]] = None,
    ):
        """
        Args:
            vision: VisionService instance for template matching / OCR.
            poll_interval: Seconds between checks (default 2.0).
            on_disconnect: Callback fired when a disconnect is detected.
            get_ocr_region: Optional callback to fetch dynamic (nx, ny, nw, nh) OCR region bounds.
        """
        self.vision = vision
        self.poll_interval = poll_interval
        self.on_disconnect = on_disconnect
        self.get_ocr_region = get_ocr_region

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False
        self._is_paused = False
        self._last_detection_time: float = 0
        self._cooldown_seconds: float = 30.0  # Avoid re-triggering during reconnect

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self):
        """Start the watchdog background thread."""
        if self._is_running:
            logger.warning("Watchdog is already running")
            return

        self._stop_event.clear()
        self._is_running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="DisconnectWatchdog")
        self._thread.start()
        logger.info("Disconnect watchdog started (poll interval: %.1fs)", self.poll_interval)

    def stop(self):
        """Stop the watchdog."""
        if not self._is_running:
            return

        self._stop_event.set()
        self._is_running = False

        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

        logger.info("Disconnect watchdog stopped")

    def pause(self):
        """Pause checking (e.g., during reconnect)."""
        self._is_paused = True
        logger.info("Disconnect watchdog paused")

    def resume(self):
        """Resume checking."""
        self._is_paused = False
        logger.info("Disconnect watchdog resumed")

    def reset_cooldown(self):
        """Reset the detection cooldown so the next poll can re-detect immediately."""
        self._last_detection_time = 0
        logger.info("Disconnect watchdog cooldown reset")

    def _poll_loop(self):
        """Main polling loop (runs in background thread)."""
        while not self._stop_event.is_set():
            if not self._is_paused:
                try:
                    self._check_for_disconnect()
                except Exception as e:
                    logger.error("Watchdog check error: %s", e)

            # Wait for the poll interval or until stopped
            self._stop_event.wait(timeout=self.poll_interval)

    def _check_for_disconnect(self):
        """Perform a single disconnect check."""
        # Check cooldown to avoid re-triggering during reconnect
        if self._last_detection_time > 0:
            elapsed = time.time() - self._last_detection_time
            if elapsed < self._cooldown_seconds:
                return

        # Check center-screen region for disconnect dialog (or user-defined region)
        nx1, ny1, nx2, ny2 = self.DEFAULT_REGION
        if self.get_ocr_region:
            region = self.get_ocr_region()
            if region:
                nx, ny, nw, nh = region
                nx1, ny1, nx2, ny2 = nx, ny, nx + nw, ny + nh
                
        result = self.vision.is_disconnect_dialog_visible(nx1, ny1, nx2, ny2)

        if result.get("found", False):
            self._last_detection_time = time.time()
            confidence = result.get("confidence", 0.0)
            template = result.get("template", "unknown")
            logger.warning(
                "DISCONNECT DETECTED — template='%s', confidence=%.3f",
                template,
                confidence,
            )

            # Pause self during reconnect
            self.pause()

            # Fire callback
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception as e:
                    logger.error("Disconnect callback error: %s", e)
                    # Resume watchdog even if callback fails
                    self.resume()
