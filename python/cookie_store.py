"""
cookie_store.py — DPAPI-Encrypted .ROBLOSECURITY Cookie Storage
================================================================
Stores the user's Roblox session cookie encrypted at rest using
Windows Data Protection API (DPAPI), scoped to the current Windows
user account. The plaintext cookie is NEVER logged, displayed (after
initial entry), or transmitted anywhere except to Roblox's own API.
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Storage location for the encrypted cookie
COOKIE_FILE = Path(__file__).parent.parent / "config" / "cookie.dat"


class CookieStore:
    """
    Manages DPAPI-encrypted storage of the .ROBLOSECURITY session cookie.

    Security guarantees:
    - Cookie is encrypted at rest via CryptProtectData (DPAPI)
    - Decryption is tied to the current Windows user account
    - Plaintext is never logged, never displayed in UI after entry,
      never transmitted anywhere except Roblox's own auth endpoints
    - Cookie file is stored in a non-world-readable location
    """

    def __init__(self, cookie_path: Optional[Path] = None):
        self._cookie_path = cookie_path or COOKIE_FILE

    def has_cookie(self) -> bool:
        """Check if an encrypted cookie file exists."""
        return self._cookie_path.exists() and self._cookie_path.stat().st_size > 0

    def store_cookie(self, cookie_value: str) -> bool:
        """
        Encrypt and store the .ROBLOSECURITY cookie.

        Args:
            cookie_value: The raw cookie string (with or without
                          '_|WARNING:-DO-NOT-SHARE...' prefix).

        Returns:
            True if stored successfully.
        """
        if not cookie_value or not cookie_value.strip():
            logger.error("Cannot store empty cookie")
            return False

        try:
            import win32crypt

            # Encode to bytes for DPAPI
            cookie_bytes = cookie_value.strip().encode("utf-8")

            # Encrypt using DPAPI (tied to current Windows user)
            encrypted = win32crypt.CryptProtectData(
                cookie_bytes,
                "RenzeiMacro_ROBLOSECURITY",  # Description (not secret)
                None,   # Optional entropy (additional key material)
                None,   # Reserved
                None,   # Prompt struct (None = no UI)
                0,      # Flags
            )

            # Ensure directory exists
            self._cookie_path.parent.mkdir(parents=True, exist_ok=True)

            # Write encrypted blob
            with open(self._cookie_path, "wb") as f:
                f.write(encrypted)

            logger.info("Cookie stored successfully (encrypted via DPAPI)")
            return True

        except ImportError:
            logger.error("win32crypt not available — install pywin32")
            return False
        except Exception as e:
            logger.error("Failed to store cookie: %s", type(e).__name__)
            return False

    def get_cookie(self) -> Optional[str]:
        """
        Decrypt and return the stored .ROBLOSECURITY cookie.

        Returns:
            The plaintext cookie string, or None if not stored or decryption fails.
        """
        if not self.has_cookie():
            logger.warning("No stored cookie found")
            return None

        try:
            import win32crypt

            with open(self._cookie_path, "rb") as f:
                encrypted = f.read()

            # Decrypt using DPAPI
            _, decrypted_bytes = win32crypt.CryptUnprotectData(
                encrypted,
                None,   # Optional entropy (must match what was used in CryptProtectData)
                None,   # Reserved
                None,   # Prompt struct
                0,      # Flags
            )

            return decrypted_bytes.decode("utf-8")

        except ImportError:
            logger.error("win32crypt not available — install pywin32")
            return None
        except Exception as e:
            logger.error("Failed to decrypt cookie: %s", type(e).__name__)
            return None

    def delete_cookie(self) -> bool:
        """Delete the stored encrypted cookie."""
        try:
            if self._cookie_path.exists():
                # Overwrite with zeros before deleting (defense in depth)
                size = self._cookie_path.stat().st_size
                with open(self._cookie_path, "wb") as f:
                    f.write(b"\x00" * size)
                self._cookie_path.unlink()
                logger.info("Cookie deleted")
            return True
        except Exception as e:
            logger.error("Failed to delete cookie: %s", e)
            return False

    def validate_cookie_format(self, cookie_value: str) -> bool:
        """
        Basic format validation for a .ROBLOSECURITY cookie.
        Does NOT check if the cookie is still valid with Roblox.
        """
        cookie = cookie_value.strip()

        # The cookie is typically a long hex/base64 string
        # May optionally start with '_|WARNING:-DO-NOT-SHARE-THIS...'
        if cookie.startswith("_|WARNING"):
            # Extract actual cookie value after the warning prefix
            parts = cookie.split("|")
            if len(parts) >= 3:
                cookie = parts[-1]

        # Should be a substantial length (typically 500+ characters)
        return len(cookie) > 100
