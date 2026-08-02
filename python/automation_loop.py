"""
automation_loop.py — The Core Reconnect State Machine
======================================================
Orchestrates the entire "fire-and-forget" macro loop:
1. Plays the infinite Farm Sequence.
2. Listens for DisconnectWatchdog triggers.
3. On disconnect: kills Roblox, gets auth ticket, relaunches via protocol.
4. Waits for the window to appear and game to load.
5. Plays the single-shot Reconnect Sequence.
6. Resumes the infinite Farm Sequence.
"""

import json
import logging
import os
import threading
import time
from typing import Callable, Optional

from playback_engine import PlaybackEngine
from relaunch import RelaunchService
from disconnect_watchdog import DisconnectWatchdog

logger = logging.getLogger(__name__)

class AutomationStateMachine:
    def __init__(
        self,
        playback: PlaybackEngine,
        relaunch: RelaunchService,
        watchdog: DisconnectWatchdog,
        on_state_change: Optional[Callable[[str], None]] = None
    ):
        self.playback = playback
        self.relaunch = relaunch
        self.watchdog = watchdog
        self.on_state_change = on_state_change

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._disconnect_event = threading.Event()
        self._is_running = False

        self._farm_seq_path: str = ""
        self._recon_seq_path: str = ""

        # Save the original callback so we can route to it if the state machine isn't running
        self._original_on_disconnect = self.watchdog.on_disconnect
        
        # Overwrite the watchdog's callback
        self.watchdog.on_disconnect = self._on_disconnect_detected

    def _set_state(self, state: str):
        logger.info(f"Automation State: {state}")
        if self.on_state_change:
            self.on_state_change(state)

    def _on_disconnect_detected(self):
        """Called by the Watchdog thread when OCR detects a disconnect."""
        if self._is_running:
            logger.warning("Disconnect detected by Watchdog! Signalling State Machine...")
            self._disconnect_event.set()
        else:
            logger.info("State Machine not running — routing disconnect to global handler...")
            if self._original_on_disconnect:
                self._original_on_disconnect()

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self, farm_seq_path: str, recon_seq_path: str):
        """Starts the main automation loop."""
        if self._is_running:
            return

        self._farm_seq_path = farm_seq_path
        self._recon_seq_path = recon_seq_path
        self._stop_event.clear()
        self._disconnect_event.clear()
        self._is_running = True

        self._thread = threading.Thread(target=self._loop, daemon=True, name="AutomationStateMachine")
        self._thread.start()

    def stop(self):
        """Stops the automation loop entirely."""
        if not self._is_running:
            return

        self._stop_event.set()
        self.watchdog.stop()
        self.playback.stop()
        self._is_running = False

        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
            
        self._set_state("Stopped")

    def _loop(self):
        """The main state machine loop."""
        while not self._stop_event.is_set():
            
            # --- State: FARMING ---
            self._set_state("Farming")
            
            # Start Watchdog
            if not self.watchdog.is_running:
                self.watchdog.start()
            self.watchdog.resume()
            
            # Load farm events
            farm_events = []
            if os.path.exists(self._farm_seq_path):
                try:
                    with open(self._farm_seq_path, "r", encoding="utf-8-sig") as f:
                        farm_events = json.load(f).get("events", [])
                except Exception as e:
                    logger.error(f"Failed to load farm sequence: {e}")

            if not farm_events:
                logger.warning("Farm sequence is empty or invalid! Waiting for stop...")
                while not self._stop_event.is_set():
                    time.sleep(1)
                break
            
            # Start Playback (Loops infinitely)
            self.playback.play(farm_events, loop=True)
            
            # Wait until either stopped OR a disconnect is detected
            while not self._stop_event.is_set() and not self._disconnect_event.is_set():
                time.sleep(0.1)
                
            if self._stop_event.is_set():
                break
                
            # --- State: DISCONNECTED ---
            # If we reach here, _disconnect_event was set.
            self._disconnect_event.clear()
            self._set_state("Reconnecting...")
            
            # Pause watchdog and kill playback
            self.watchdog.pause()
            self.playback.stop()
            time.sleep(1) # Let threads die
            
            # Relaunch Roblox
            success = self.relaunch.relaunch()
            if not success:
                logger.error("Relaunch failed! Retrying in 30 seconds...")
                for _ in range(300):
                    if self._stop_event.is_set(): break
                    time.sleep(0.1)
                self._disconnect_event.set() # Loop back to reconnect
                continue
                
            # Wait for window
            self._set_state("Waiting for Window...")
            if not self.relaunch.wait_for_window(timeout=60):
                logger.error("Roblox window did not appear! Retrying...")
                self._disconnect_event.set()
                continue
                
            # Wait for game to actually load (15 seconds default)
            self._set_state("Waiting for Game Load (15s)...")
            for _ in range(150):
                if self._stop_event.is_set(): break
                time.sleep(0.1)
                
            if self._stop_event.is_set():
                break
                
            # Load reconnect events
            recon_events = []
            if os.path.exists(self._recon_seq_path):
                try:
                    with open(self._recon_seq_path, "r", encoding="utf-8-sig") as f:
                        recon_events = json.load(f).get("events", [])
                except Exception as e:
                    logger.error(f"Failed to load reconnect sequence: {e}")

            # Play Reconnect Sequence (runs exactly once)
            if recon_events:
                self._set_state("Running Reconnect Sequence...")
                self.playback.play(recon_events, loop=False)
                
                # Wait for it to finish playing
                while self.playback.is_playing and not self._stop_event.is_set():
                    time.sleep(0.1)
            else:
                logger.info("No reconnect sequence defined, skipping...")
                
            if self._stop_event.is_set():
                break
                
            # Loop restarts, going back to FARMING
            logger.info("Reconnect sequence finished, resuming Farm Sequence.")
