"""
villain_invasion/vi_config.py
==============================
Loads and saves the Villain Invasion mode configuration from:
  presets/villain_invasion/config.json

Contents:
  - private_server_url  : full Roblox private server URL
  - place_id            : parsed from URL
  - link_code           : parsed from URL
  - ocr_region          : normalized rect {nx, ny, nw, nh} — saved once by the
                          OCR region selector overlay
  - reconnect_preset    : path to a JSON reconnect macro preset file
"""

import json
import logging
import os
import re
import requests
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Path relative to the project root (d:\RenzeiMacro)
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__),          # python/villain_invasion/
    "..", "..",                          # project root
    "presets", "villain_invasion", "config.json"
)
_DEFAULT_SEQUENCE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..",
    "presets", "villain_invasion", "sequence.json"
)

# Regex to parse place_id and privateServerLinkCode from a Roblox URL.
# Supports both:
#   https://www.roblox.com/games/PLACEID/Name?privateServerLinkCode=CODE
#   https://www.roblox.com/share?code=CODE&type=Server   (newer share links)
_PLACE_RE       = re.compile(r"roblox\.com/games/(\d+)", re.IGNORECASE)
_PSLC_RE        = re.compile(r"privateServerLinkCode=([A-Za-z0-9_\-]+)", re.IGNORECASE)
_SHARE_CODE     = re.compile(r"roblox\.com/share\?.*code=([A-Za-z0-9_\-]+)", re.IGNORECASE)
_START_PLACE_RE = re.compile(r'roblox:start_place_id["\']?\s+content=["\']?(\d+)', re.IGNORECASE)


def parse_private_server_url(url: str, resolve_redirects: bool = True) -> Dict[str, str]:
    """
    Parse a Roblox private server URL into its components.

    Returns a dict with keys:
      place_id   (str, may be "" if not found)
      link_code  (str, may be "" if not found)
      error      (str, empty if parse succeeded)
    """
    url = url.strip()
    if not url:
        return {"place_id": "", "link_code": "", "error": "URL is empty"}

    place_id = ""
    link_code = ""
    error = ""

    m = _PLACE_RE.search(url)
    if m:
        place_id = m.group(1)

    m = _PSLC_RE.search(url)
    if m:
        link_code = m.group(1)

    if not link_code:
        m = _SHARE_CODE.search(url)
        if m:
            link_code = m.group(1)

    # If place_id is missing (common with roblox.com/share links), fetch HTML to extract start_place_id
    if not place_id and resolve_redirects:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = requests.get(url, headers=headers, timeout=8)
            
            # Check meta tag in HTML page
            m_place = _START_PLACE_RE.search(resp.text)
            if m_place:
                place_id = m_place.group(1)
                logger.info("Parsed start_place_id=%s from share page HTML", place_id)
            else:
                # Fallback to checking final URL if redirected
                final_url = resp.url
                if final_url and final_url != url:
                    m = _PLACE_RE.search(final_url)
                    if m:
                        place_id = m.group(1)

            if not link_code:
                m_code = _PSLC_RE.search(resp.text) or _SHARE_CODE.search(resp.text)
                if m_code:
                    link_code = m_code.group(1)
        except Exception as e:
            logger.warning("Could not resolve share link HTML: %s", e)

    if not place_id and not link_code:
        error = "Could not parse place ID or link code from URL"
    elif not place_id:
        error = "Could not determine place ID from link (check internet connection)"
    elif not link_code:
        error = "No privateServerLinkCode found — may be a public server link"

    return {"place_id": place_id, "link_code": link_code, "error": error}


@dataclass
class OcrRegion:
    """Normalized rectangle (0.0–1.0) within the container."""
    nx: float = 0.1
    ny: float = 0.35
    nw: float = 0.8
    nh: float = 0.3

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OcrRegion":
        return cls(
            nx=float(d.get("nx", 0.1)),
            ny=float(d.get("ny", 0.35)),
            nw=float(d.get("nw", 0.8)),
            nh=float(d.get("nh", 0.3)),
        )


@dataclass
class VillainInvasionConfig:
    """
    All persistent settings for the Villain Invasion macro mode.
    Saved to presets/villain_invasion/config.json.
    """
    private_server_url: str = ""
    place_id: str = ""
    link_code: str = ""
    ocr_region: Optional[OcrRegion] = None
    reconnect_preset: str = ""          # path to reconnect macro JSON
    config_path: str = field(default=_DEFAULT_CONFIG_PATH, repr=False)

    # ── I/O ─────────────────────────────────────────────────────────────────

    def save(self) -> bool:
        path = os.path.normpath(self.config_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            data = {
                "private_server_url": self.private_server_url,
                "place_id":           self.place_id,
                "link_code":          self.link_code,
                "ocr_region":         self.ocr_region.to_dict() if self.ocr_region else None,
                "reconnect_preset":   self.reconnect_preset,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info("VI config saved → %s", path)
            return True
        except Exception as e:
            logger.error("VI config save failed: %s", e)
            return False

    @classmethod
    def load(cls, config_path: str = _DEFAULT_CONFIG_PATH) -> "VillainInvasionConfig":
        path = os.path.normpath(config_path)
        instance = cls(config_path=path)
        if not os.path.exists(path):
            logger.info("VI config not found at %s — using defaults", path)
            return instance
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            instance.private_server_url = data.get("private_server_url", "")
            instance.place_id           = data.get("place_id", "")
            instance.link_code          = data.get("link_code", "")
            instance.reconnect_preset   = data.get("reconnect_preset", "")
            raw_ocr = data.get("ocr_region")
            if raw_ocr:
                instance.ocr_region = OcrRegion.from_dict(raw_ocr)

            # Auto-heal: If we have a URL saved but missing place_id or link_code, resolve it now
            if instance.private_server_url and (not instance.place_id or not instance.link_code):
                parsed = parse_private_server_url(instance.private_server_url)
                if parsed["place_id"]:
                    instance.place_id = parsed["place_id"]
                if parsed["link_code"]:
                    instance.link_code = parsed["link_code"]
                instance.save()

            logger.info("VI config loaded from %s (place_id=%s, link_code=%s)", path, instance.place_id, instance.link_code)
        except Exception as e:
            logger.error("VI config load failed: %s — using defaults", e)
        return instance

    # ── Helpers ──────────────────────────────────────────────────────────────

    def set_private_server_url(self, url: str) -> Dict[str, str]:
        """Parse and store a private server URL.  Returns the parse result."""
        self.private_server_url = url
        result = parse_private_server_url(url)
        self.place_id  = result["place_id"]
        self.link_code = result["link_code"]
        return result

    def set_ocr_region(self, nx: float, ny: float, nw: float, nh: float):
        """Update the OCR region (normalized coords) and save immediately."""
        self.ocr_region = OcrRegion(nx=nx, ny=ny, nw=nw, nh=nh)
        self.save()

    def has_server(self) -> bool:
        return bool(self.place_id)

    def has_ocr_region(self) -> bool:
        return self.ocr_region is not None

    def default_sequence_path(self) -> str:
        """Returns the default sequence path."""
        return os.path.normpath(_DEFAULT_SEQUENCE_PATH)
