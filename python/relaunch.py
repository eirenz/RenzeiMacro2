"""
relaunch.py — Browserless Cookie-Based Roblox Relaunch
=======================================================
Handles the full reconnect flow:
1. Close existing Roblox process
2. Obtain an auth ticket from Roblox API using stored cookie
3. Launch Roblox via roblox-player:// protocol (no browser)
4. Wait for the game window to appear
"""

import logging
import os
import subprocess
import time
import urllib.parse
from typing import Optional

import psutil
import requests

from cookie_store import CookieStore

logger = logging.getLogger(__name__)


class RelaunchService:
    """
    Handles browserless Roblox game relaunch using the .ROBLOSECURITY cookie
    to obtain an authentication ticket, then launching via the
    roblox-player:// protocol handler.
    """

    # Roblox API endpoints
    AUTH_TICKET_URL = "https://auth.roblox.com/v1/authentication-ticket"
    CSRF_URL = "https://auth.roblox.com/v2/logout"  # Any POST endpoint for CSRF token

    # Roblox process names
    ROBLOX_PROCESSES = ["RobloxPlayerBeta.exe", "RobloxPlayerLauncher.exe"]

    def __init__(self, cookie_store: CookieStore):
        self.cookie_store = cookie_store
        self._last_place_id: str = ""
        self._last_job_id: str = ""
        self._last_link_code: str = ""
        self._last_private_server_url: str = ""

    def set_target(self, place_id: str, job_id: str = "", link_code: str = "", private_server_url: str = ""):
        """Set the place/server to rejoin on disconnect."""
        self._last_place_id = place_id
        self._last_job_id = job_id
        self._last_link_code = link_code
        self._last_private_server_url = private_server_url
        logger.info("Relaunch target set — place_id=%s, job_id=%s, url=%s", place_id, job_id or "(any)", private_server_url or "(none)")

    def relaunch(self, place_id: str = "", job_id: str = "", link_code: str = "", private_server_url: str = "") -> bool:
        """
        Full relaunch flow:
        1. Close existing Roblox
        2. Launch via Private Server URL (if provided) or Auth Ticket protocol

        Args:
            place_id: Target place ID.
            job_id: Target server job ID (optional).
            link_code: Target private server link code (optional).
            private_server_url: Full private server or share link URL (optional).

        Returns:
            True if relaunch was initiated successfully.
        """
        place_id = place_id or self._last_place_id
        job_id = job_id or self._last_job_id
        link_code = link_code or self._last_link_code
        private_server_url = private_server_url or self._last_private_server_url

        if not place_id and not private_server_url:
            logger.error("No place_id or URL set — cannot relaunch")
            return False

        # Step 1: Close existing Roblox processes
        logger.info("Step 1: Closing existing Roblox processes...")
        self._close_roblox()
        time.sleep(2)  # Wait for processes to fully terminate

        # Step 2: If Private Server URL is available, launch via system handler (avoids Error 524)
        if private_server_url and ("roblox.com" in private_server_url or "roblox:" in private_server_url):
            logger.info("Step 2: Launching Roblox via Private Server URL: %s", private_server_url)
            try:
                import webbrowser
                webbrowser.open(private_server_url)
                logger.info("Private Server URL launched via system handler")
                return True
            except Exception as e:
                logger.warning("Failed to open Private Server URL directly: %s — falling back to ticket launch", e)

        # Step 3: Fall back to browserless auth ticket protocol launch
        logger.info("Step 2: Obtaining auth ticket...")
        cookie = self.cookie_store.get_cookie()
        if not cookie:
            logger.error("No cookie available — cannot authenticate")
            return False

        auth_ticket = self._get_auth_ticket(cookie)
        if not auth_ticket:
            logger.error("Failed to obtain auth ticket")
            return False

        logger.info("Step 3: Launching Roblox via protocol...")
        success = self._launch_roblox(place_id, auth_ticket, job_id, link_code)

        if success:
            logger.info("Roblox launch initiated — waiting for window...")
        else:
            logger.error("Failed to launch Roblox")

        return success

    def wait_for_window(self, timeout: float = 60.0) -> bool:
        """
        Wait for the Roblox window to appear after launching.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            True if window appeared within timeout.
        """
        import win32gui

        start = time.time()
        while time.time() - start < timeout:
            hwnd = win32gui.FindWindow(None, "Roblox")
            if not hwnd:
                # Also try by class
                for cls in ["WINDOWSCLIENT", "RobloxPlayerBeta"]:
                    hwnd = win32gui.FindWindow(cls, None)
                    if hwnd:
                        break

            if hwnd and win32gui.IsWindowVisible(hwnd):
                logger.info("Roblox window detected (hwnd=%s)", hwnd)
                return True

            time.sleep(1)

        logger.warning("Roblox window did not appear within %.0fs", timeout)
        return False

    # === Private Methods ===================================================

    def _close_roblox(self):
        """Terminate all Roblox processes."""
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] in self.ROBLOX_PROCESSES:
                    logger.info("Terminating %s (PID %d)", proc.info["name"], proc.pid)
                    proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Wait for termination
        gone, alive = psutil.wait_procs(
            [p for p in psutil.process_iter(["name"])
             if p.info["name"] in self.ROBLOX_PROCESSES],
            timeout=5,
        )
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass

    def _get_csrf_token(self, cookie: str, max_retries: int = 10, retry_delay: float = 3.0) -> Optional[str]:
        """
        Obtain a CSRF token from Roblox.
        Roblox returns the CSRF token as x-csrf-token in a 403 response.
        Retries automatically if network connection is temporarily interrupted.
        """
        headers = {
            "Cookie": f".ROBLOSECURITY={cookie}",
            "Content-Type": "application/json",
        }

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(self.CSRF_URL, headers=headers, timeout=10)
                # 403 is expected — the CSRF token is in the response headers
                csrf_token = resp.headers.get("x-csrf-token")
                if csrf_token:
                    logger.info("CSRF token obtained successfully")
                    return csrf_token
                else:
                    logger.warning("No CSRF token in response (status=%d)", resp.status_code)
            except requests.RequestException as e:
                logger.warning("CSRF token attempt %d/%d failed (waiting for network): %s", attempt, max_retries, e)
            
            if attempt < max_retries:
                time.sleep(retry_delay)

        logger.error("Failed to get CSRF token after %d attempts", max_retries)
        return None

    def _get_auth_ticket(self, cookie: str, max_retries: int = 10, retry_delay: float = 3.0) -> Optional[str]:
        """
        Request a one-time authentication ticket from Roblox's API.
        This ticket is used to authenticate the game client launch.
        Retries automatically if network connection is temporarily interrupted.
        """
        # First, get a CSRF token
        csrf_token = self._get_csrf_token(cookie, max_retries=max_retries, retry_delay=retry_delay)
        if not csrf_token:
            return None

        headers = {
            "Cookie": f".ROBLOSECURITY={cookie}",
            "Content-Type": "application/json",
            "x-csrf-token": csrf_token,
            "Referer": "https://www.roblox.com/",
        }

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(
                    self.AUTH_TICKET_URL,
                    headers=headers,
                    json={},
                    timeout=10,
                )

                if resp.status_code == 200:
                    ticket = resp.headers.get("rbx-authentication-ticket")
                    if ticket:
                        logger.info("Auth ticket obtained successfully")
                        return ticket
                    else:
                        logger.error("No auth ticket in response headers")
                else:
                    logger.error("Auth ticket request failed (status=%d): %s",
                                 resp.status_code, resp.text[:200])

            except requests.RequestException as e:
                logger.warning("Auth ticket attempt %d/%d failed (waiting for network): %s", attempt, max_retries, e)

            if attempt < max_retries:
                time.sleep(retry_delay)

        logger.error("Auth ticket request failed after %d attempts", max_retries)
        return None

    def _launch_roblox(self, place_id: str, auth_ticket: str, job_id: str = "", link_code: str = "") -> bool:
        """
        Launch Roblox using the roblox-player:// protocol URI.
        This triggers the OS protocol handler → RobloxPlayerLauncher → RobloxPlayerBeta.
        """
        # Build the launch URI
        if link_code:
            launcher_url = f"https://assetgame.roblox.com/game/PlaceLauncher.ashx?request=RequestPrivateServer&placeId={place_id}&linkCode={link_code}&accessCode={link_code}&code={link_code}"
        elif job_id:
            launcher_url = f"https://assetgame.roblox.com/game/PlaceLauncher.ashx?request=RequestGame&placeId={place_id}&gameId={job_id}"
        else:
            launcher_url = f"https://assetgame.roblox.com/game/PlaceLauncher.ashx?request=RequestGame&placeId={place_id}"

        # URL-encode the launcher URL
        encoded_launcher = urllib.parse.quote(launcher_url, safe="")

        # Build the full protocol URI
        uri = (
            f"roblox-player:1"
            f"+launchmode:play"
            f"+gameinfo:{auth_ticket}"
            f"+launchtime:{int(time.time() * 1000)}"
            f"+placelauncherurl:{encoded_launcher}"
            f"+browsertrackerid:0"
            f"+robloxLocale:en_us"
            f"+gameLocale:en_us"
        )

        try:
            # Launch via the OS protocol handler
            os.startfile(uri)
            logger.info("Roblox launch URI invoked for place_id=%s", place_id)
            return True
        except Exception as e:
            logger.error("Failed to launch Roblox: %s", e)
            return False
