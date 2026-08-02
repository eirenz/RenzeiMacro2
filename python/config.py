"""
config.py — Configuration Management
======================================
Manages settings.json: keybinds, macro modes, active mode, and
general configuration. Designed as a mode→preset map from day one
for extensibility, but only one mode ("default") is used initially.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default configuration path
CONFIG_DIR = Path(__file__).parent.parent / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# Default settings
DEFAULT_SETTINGS = {
    "keybinds": {
        "record": "F6",
        "stop": "F7",
        "play": "F8",
        "emergency_stop": "F12",
    },
    "active_mode": "default",
    "modes": {
        "default": {
            "name": "Default Mode",
            "reconnect_preset": "presets/default_reconnect.json",
            "place_id": "",
            "job_id": "",
        }
    },
    "capture_method": "mss",
    "ocr_engine": "tesseract",
    "disconnect_poll_interval_ms": 2000,
    "auto_reconnect_enabled": True,
    "dev_mode": False,
}


class AppConfig:
    """
    Manages application configuration with JSON persistence.
    Thread-safe for read operations; writes should be done from
    the main thread or with external synchronization.
    """

    def __init__(self, settings_path: Optional[Path] = None):
        self._path = settings_path or SETTINGS_FILE
        self._settings: Dict[str, Any] = {}
        self.load()

    def load(self) -> Dict[str, Any]:
        """Load settings from disk, falling back to defaults."""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Merge with defaults (loaded values override defaults)
                self._settings = self._deep_merge(DEFAULT_SETTINGS, loaded)
                logger.info("Configuration loaded from %s", self._path)
            except Exception as e:
                logger.error("Failed to load config: %s — using defaults", e)
                self._settings = dict(DEFAULT_SETTINGS)
        else:
            self._settings = dict(DEFAULT_SETTINGS)
            logger.info("No config file found — using defaults")
        return self._settings

    def save(self) -> bool:
        """Persist current settings to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2, ensure_ascii=False)
            logger.info("Configuration saved to %s", self._path)
            return True
        except Exception as e:
            logger.error("Failed to save config: %s", e)
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """Get a top-level setting."""
        return self._settings.get(key, default)

    def set(self, key: str, value: Any):
        """Set a top-level setting."""
        self._settings[key] = value

    @property
    def settings(self) -> Dict[str, Any]:
        """Direct access to the settings dict."""
        return self._settings

    # --- Keybinds ---

    @property
    def keybinds(self) -> Dict[str, str]:
        return self._settings.get("keybinds", DEFAULT_SETTINGS["keybinds"])

    def set_keybind(self, action: str, key: str):
        """Set a keybind (e.g., 'record' → 'F5')."""
        if "keybinds" not in self._settings:
            self._settings["keybinds"] = {}
        self._settings["keybinds"][action] = key

    # --- Macro Modes ---

    @property
    def active_mode_name(self) -> str:
        return self._settings.get("active_mode", "default")

    @active_mode_name.setter
    def active_mode_name(self, name: str):
        self._settings["active_mode"] = name

    @property
    def active_mode(self) -> Dict[str, Any]:
        """Get the active mode's configuration."""
        modes = self._settings.get("modes", {})
        return modes.get(self.active_mode_name, modes.get("default", {}))

    def get_mode(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a specific mode's configuration."""
        return self._settings.get("modes", {}).get(name)

    def set_mode(self, name: str, mode_config: Dict[str, Any]):
        """Set or update a mode's configuration."""
        if "modes" not in self._settings:
            self._settings["modes"] = {}
        self._settings["modes"][name] = mode_config

    @property
    def reconnect_preset_path(self) -> str:
        """Get the reconnect preset path for the active mode."""
        mode = self.active_mode
        return mode.get("reconnect_preset", "")

    @property
    def place_id(self) -> str:
        """Get the place ID for the active mode."""
        return self.active_mode.get("place_id", "")

    @property
    def job_id(self) -> str:
        """Get the job ID for the active mode."""
        return self.active_mode.get("job_id", "")

    # --- Feature Flags ---

    @property
    def auto_reconnect_enabled(self) -> bool:
        return self._settings.get("auto_reconnect_enabled", True)

    @auto_reconnect_enabled.setter
    def auto_reconnect_enabled(self, value: bool):
        self._settings["auto_reconnect_enabled"] = value

    @property
    def disconnect_poll_interval(self) -> float:
        """Return disconnect poll interval in seconds."""
        ms = self._settings.get("disconnect_poll_interval_ms", 2000)
        return ms / 1000.0

    # --- Utility ---

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """Deep merge override into base (override wins)."""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = AppConfig._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
