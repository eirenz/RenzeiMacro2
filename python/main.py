"""
main.py — Python Entry Point
==============================
Initializes all Python-side services:
- Container detection
- Screen capture
- Vision service (template matching + OCR)
- IPC server (named pipe)
- Disconnect watchdog
- Relaunch service
- Settings GUI (tkinter)

This process is launched by the AHK layer (main.ahk) and runs
alongside it, communicating over the named pipe.
"""

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from capture import ScreenCapture
from config import AppConfig
from container import Container
from cookie_store import CookieStore
from disconnect_watchdog import DisconnectWatchdog
from gui import SettingsGUI
from ipc_server import IPCServer
from playback_engine import PlaybackEngine
from relaunch import RelaunchService
from vision import VisionService
from automation_loop import AutomationStateMachine

# ============================================================================
# Logging Setup
# ============================================================================

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "renzei_python.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("RenzeiMacro")

# ============================================================================
# Main Application
# ============================================================================


class RenzeiPythonService:
    """
    Main orchestrator for the Python-side services.
    """

    def __init__(self):
        logger.info("=" * 60)
        logger.info("RenzeiMacro Python Service starting...")
        logger.info("=" * 60)

        # --- Configuration ---
        self.config = AppConfig()
        self.config.save()  # Ensure defaults are persisted

        # --- Container ---
        self.container = Container()
        # NOTE: Do NOT call auto_detect() here.  The GUI's polling loop
        # detects Roblox, resizes it to fit behind the container canvas,
        # and calls container.set_manual() from the canvas screen coords.
        # Any auto_detect() here would be immediately overwritten.
        logger.info("Container initialised (waiting for GUI to detect Roblox)")

        # --- Screen Capture ---
        self.capture = ScreenCapture(self.container)

        # --- Vision ---
        self.vision = VisionService(self.container, self.capture)

        # --- Cookie Store ---
        self.cookie_store = CookieStore()
        if self.cookie_store.has_cookie():
            logger.info("Encrypted cookie found on disk")
        else:
            logger.info("No stored cookie — auto-reconnect will require cookie setup")

        # --- Relaunch Service ---
        self.relaunch = RelaunchService(self.cookie_store)
        if self.config.place_id:
            self.relaunch.set_target(self.config.place_id, self.config.job_id)

        def get_active_ocr_region():
            if self.config.active_mode_name == "villain_invasion":
                from villain_invasion.vi_config import VillainInvasionConfig
                vi_cfg = VillainInvasionConfig.load()
                if vi_cfg.ocr_region:
                    return (vi_cfg.ocr_region.nx, vi_cfg.ocr_region.ny, vi_cfg.ocr_region.nw, vi_cfg.ocr_region.nh)
            return None

        # --- Disconnect Watchdog ---
        self.watchdog = DisconnectWatchdog(
            vision=self.vision,
            poll_interval=self.config.disconnect_poll_interval,
            on_disconnect=self._on_disconnect,
            get_ocr_region=get_active_ocr_region,
        )

        # --- IPC Server ---
        self.ipc = IPCServer()
        self._register_ipc_handlers()

        # --- Playback Engine ---
        self.playback = PlaybackEngine(self.container)
        
        # --- Automation State Machine ---
        self.state_machine = AutomationStateMachine(
            playback=self.playback,
            relaunch=self.relaunch,
            watchdog=self.watchdog,
            on_state_change=lambda s: self.gui.update_status(s) if hasattr(self, 'gui') else None
        )

        # --- GUI ---
        self.gui = SettingsGUI(
            config=self.config,
            cookie_store=self.cookie_store,
            container=self.container,
            on_config_changed=self._on_config_changed,
            # Called by the GUI's polling loop the first time the Roblox
            # window is found — this is when we activate the watchdog.
            on_container_found=self._on_container_found,
        )

    def start(self):
        """Start all services."""
        # Start IPC server (must be first — AHK may connect immediately)
        self.ipc.start()
        logger.info("IPC server started")

        # Start GUI — its internal polling loop will auto-detect the Roblox
        # window and call _on_container_found() once it's found, which in
        # turn starts the disconnect watchdog.  No manual setup required.
        self.gui.start()
        logger.info("Settings GUI started — container polling active")

        logger.info("All services running. Waiting for Roblox window...")

    def stop(self):
        """Stop all services cleanly."""
        logger.info("Shutting down...")
        self.playback.stop()
        self.watchdog.stop()
        self.ipc.stop()
        self.capture.close()
        logger.info("Shutdown complete.")

    # === IPC Handler Registration ==========================================

    def _register_ipc_handlers(self):
        """Register all IPC command handlers."""
        self.ipc.register_handler("template_match", self._handle_template_match)
        self.ipc.register_handler("ocr_read", self._handle_ocr_read)
        self.ipc.register_handler("get_container", self._handle_get_container)
        self.ipc.register_handler("refresh_container", self._handle_refresh_container)
        self.ipc.register_handler("is_game_loaded", self._handle_is_game_loaded)
        self.ipc.register_handler("is_disconnected", self._handle_is_disconnected)
        self.ipc.register_handler("start_reconnect", self._handle_start_reconnect)
        self.ipc.register_handler("get_config", self._handle_get_config)
        self.ipc.register_handler("ping", self._handle_ping)
        self.ipc.register_handler("play_macro", self._handle_play_macro)
        self.ipc.register_handler("stop_macro", self._handle_stop_macro)

    def _handle_template_match(self, query: dict) -> dict:
        template = query.get("template", "")
        region = query.get("region", {})
        threshold = query.get("threshold", 0.8)
        return self.vision.template_match(
            template,
            region.get("nx1", 0.0),
            region.get("ny1", 0.0),
            region.get("nx2", 1.0),
            region.get("ny2", 1.0),
            threshold,
        )

    def _handle_ocr_read(self, query: dict) -> dict:
        region = query.get("region", {})
        return self.vision.ocr_read(
            region.get("nx1", 0.0),
            region.get("ny1", 0.0),
            region.get("nx2", 1.0),
            region.get("ny2", 1.0),
        )

    def _handle_get_container(self, query: dict) -> dict:
        return self.container.rect.to_dict()

    def _handle_refresh_container(self, query: dict) -> dict:
        success = self.container.refresh()
        result = self.container.rect.to_dict()
        result["refreshed"] = success
        return result

    def _handle_is_game_loaded(self, query: dict) -> dict:
        return self.vision.is_game_loaded()

    def _handle_is_disconnected(self, query: dict) -> dict:
        return self.vision.is_disconnect_dialog_visible()

    def _handle_start_reconnect(self, query: dict) -> dict:
        """Manually trigger the reconnect flow from AHK."""
        if getattr(self, "state_machine", None) and self.state_machine.is_running:
            self.state_machine._on_disconnect_detected()
        else:
            self._on_disconnect()
        return {"status": "reconnect_initiated"}

    def _handle_get_config(self, query: dict) -> dict:
        return self.config.settings

    def _handle_ping(self, query: dict) -> dict:
        return {"pong": True, "timestamp": time.time()}

    def _handle_play_macro(self, query: dict) -> dict:
        path = query.get("file", "")
        if not path:
            return {"error": "No file specified"}
            
        # If active mode is villain_invasion, we use the state machine
        if self.config.active_mode_name == "villain_invasion":
            recon_path = self.config.reconnect_preset_path
            
            if not self.cookie_store.has_cookie():
                logger.warning("No cookie stored, reconnects will fail!")
                
            self.state_machine.start(path, recon_path)
            return {"status": "state_machine_started"}

        # Otherwise, just raw playback
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            events = data.get("events", [])
            if not events:
                return {"error": "Sequence is empty"}
            loop = query.get("loop", True)
            self.playback.play(events, loop)
            return {"status": "playing", "events": len(events)}
        except Exception as e:
            logger.error("Failed to play macro: %s", e)
            return {"error": str(e)}

    def _handle_stop_macro(self, query: dict) -> dict:
        if getattr(self, "state_machine", None) and self.state_machine.is_running:
            self.state_machine.stop()
        self.playback.stop()
        self.gui.update_status("Idle")
        return {"status": "stopped"}

    # === Reconnect Flow ====================================================

    def _on_disconnect(self):
        """
        Called when a disconnect is detected (by watchdog or manually).
        Executes the full reconnect pipeline.
        """
        logger.warning("=" * 40)
        logger.warning("DISCONNECT DETECTED — starting reconnect flow")
        logger.warning("=" * 40)

        self.gui.update_status("Reconnecting...")

        # Pause the watchdog during reconnect
        self.watchdog.pause()

        try:
            # Step 1: Relaunch Roblox
            place_id = self.config.place_id
            job_id = self.config.job_id
            link_code = ""
            private_server_url = ""
            
            # Fetch from active mode config if available
            if self.config.active_mode_name == "villain_invasion":
                from villain_invasion.vi_config import VillainInvasionConfig
                vi_cfg = VillainInvasionConfig.load()
                place_id = vi_cfg.place_id
                link_code = vi_cfg.link_code
                private_server_url = vi_cfg.private_server_url
                
            if not place_id and not private_server_url:
                logger.error("No place_id configured — cannot auto-reconnect")
                self.gui.update_status("Reconnect failed (no place_id)")
                self.watchdog.reset_cooldown()
                self.watchdog.resume()
                return

            success = self.relaunch.relaunch(place_id, job_id, link_code, private_server_url)
            if not success:
                logger.error("Relaunch failed")
                self.gui.update_status("Reconnect failed (relaunch)")
                self.watchdog.resume()
                return

            # Step 2: Wait for game window
            logger.info("Waiting for Roblox window to appear...")
            if not self.relaunch.wait_for_window(timeout=60):
                logger.error("Roblox window did not appear")
                self.gui.update_status("Reconnect failed (no window)")
                self.watchdog.resume()
                return

            # Step 3: Re-position Roblox via the GUI
            # The GUI polling loop will detect the new Roblox window and
            # re-position it behind the container canvas automatically.
            # We trigger an immediate recalibration so it doesn't wait for
            # the next 3-second poll cycle.
            time.sleep(5)  # Give the game a moment to fully render
            try:
                self.gui._recalibrate()
            except Exception:
                pass  # GUI thread will pick it up on next poll
            logger.info("Container recalibration triggered")

            # Step 4: Wait for game to fully load (vision check)
            logger.info("Waiting for game to load...")
            loaded = False
            for _ in range(30):  # Max 30 checks, ~30 seconds
                result = self.vision.is_game_loaded()
                if result.get("loaded", False):
                    loaded = True
                    break
                time.sleep(1)

            if not loaded:
                logger.warning("Game load check inconclusive — proceeding anyway")

            # Step 5: Signal AHK to play reconnect preset
            # (AHK will handle this via IPC — the reconnect preset path
            #  is in the config, and AHK's main loop will pick it up)
            logger.info("Reconnect complete — signaling AHK to play reconnect preset")
            self.gui.update_status("Reconnected — playing preset")

            # Resume watchdog
            self.watchdog.resume()

        except Exception as e:
            logger.error("Reconnect flow error: %s", e, exc_info=True)
            self.gui.update_status(f"Reconnect error: {e}")
            self.watchdog.resume()

    # === Container Found Callback ==========================================

    def _on_container_found(self):
        """
        Called by the GUI's polling loop the first time (or after a re-open)
        the Roblox window is successfully detected.
        Activates the disconnect watchdog and updates the relaunch target.
        """
        logger.info("Container found — activating services")
        self.gui.update_status("Idle")

        # Update relaunch target from config (in case place_id was already set)
        if self.config.place_id:
            self.relaunch.set_target(self.config.place_id, self.config.job_id)

        # Start the disconnect watchdog now that we have a valid container
        if self.config.auto_reconnect_enabled:
            if not self.watchdog.is_running:
                self.watchdog.start()
                logger.info("Disconnect watchdog started")
            else:
                logger.info("Disconnect watchdog already running")
        else:
            logger.info("Auto-reconnect disabled — watchdog not started")

    # === Config Change Handler =============================================

    def _on_config_changed(self):
        """Called when the user saves settings from the GUI."""
        logger.info("Configuration changed — reloading...")

        # Update relaunch target
        if self.config.place_id:
            self.relaunch.set_target(self.config.place_id, self.config.job_id)

        # Update watchdog interval
        self.watchdog.poll_interval = self.config.disconnect_poll_interval


# ============================================================================
# Entry Point
# ============================================================================

def main():
    service = RenzeiPythonService()
    service.start()

    # Handle graceful shutdown
    def signal_handler(sig, frame):
        service.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        service.stop()


if __name__ == "__main__":
    main()
