"""
container.py — Python-side Container Rect Management
=====================================================
Mirror of the AHK container logic for the Python vision/capture layer.
Uses ctypes to call the same Windows API functions to get the Roblox
client-area rectangle. The container rect defines the bounds for all
screen capture and vision operations.
"""

import ctypes
import ctypes.wintypes as wintypes
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import win32gui
import win32process

logger = logging.getLogger(__name__)

# Windows API constants
CURSOR_SHOWING = 0x00000001


@dataclass
class ContainerRect:
    """Represents the container's absolute screen rectangle."""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    valid: bool = False

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "valid": self.valid}

    @classmethod
    def from_dict(cls, d: dict) -> "ContainerRect":
        return cls(x=d["x"], y=d["y"], w=d["w"], h=d["h"], valid=d.get("valid", True))

    def __str__(self) -> str:
        if not self.valid:
            return "ContainerRect [INVALID]"
        return f"ContainerRect [{self.x}, {self.y} — {self.w}x{self.h}]"


class Container:
    """
    Manages the Roblox client window's container rectangle.
    All vision/capture operations are scoped to this rectangle.
    """

    PROCESS_NAME = "RobloxPlayerBeta.exe"
    WINDOW_CLASSES = ["WINDOWSCLIENT", "RobloxPlayerBeta"]

    def __init__(self):
        self.rect = ContainerRect()
        self.hwnd: Optional[int] = None

    def auto_detect(self) -> bool:
        """
        Find the Roblox window and extract its client-area rectangle.
        Tries: process name → window class → window title.
        Returns True if successful.
        """
        self.hwnd = (
            self._find_by_process()
            or self._find_by_class()
            or self._find_by_title()
        )

        if not self.hwnd:
            self.rect.valid = False
            logger.warning("Roblox window not found")
            return False

        return self._extract_client_rect()

    def set_manual(self, x: int, y: int, w: int, h: int) -> bool:
        """Manual override for the container rectangle."""
        self.rect = ContainerRect(x=x, y=y, w=w, h=h, valid=(w > 0 and h > 0))
        logger.info("Container set manually: %s", self.rect)
        return self.rect.valid

    def refresh(self) -> bool:
        """Re-detect the container rect. Call before playback sessions."""
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            return self._extract_client_rect()
        return self.auto_detect()

    def normalize_coords(self, abs_x: int, abs_y: int) -> Tuple[float, float]:
        """
        Convert absolute screen coordinates to normalized (0.0–1.0).
        Returns (nx, ny).
        """
        if not self.rect.valid or self.rect.w == 0 or self.rect.h == 0:
            raise ValueError("Container is not valid — cannot normalize coordinates")

        nx = (abs_x - self.rect.x) / self.rect.w
        ny = (abs_y - self.rect.y) / self.rect.h
        return (nx, ny)

    def denormalize_coords(self, nx: float, ny: float) -> Tuple[int, int]:
        """
        Convert normalized (0.0–1.0) coordinates to absolute screen coordinates.
        Returns (abs_x, abs_y).
        """
        if not self.rect.valid:
            raise ValueError("Container is not valid — cannot denormalize coordinates")

        abs_x = round(self.rect.x + nx * self.rect.w)
        abs_y = round(self.rect.y + ny * self.rect.h)
        return (abs_x, abs_y)

    def is_point_in_container(self, abs_x: int, abs_y: int) -> bool:
        """Check if an absolute screen point is within the container."""
        if not self.rect.valid:
            return False
        return (
            self.rect.x <= abs_x < self.rect.x + self.rect.w
            and self.rect.y <= abs_y < self.rect.y + self.rect.h
        )

    def get_capture_region(
        self,
        nx1: float = 0.0,
        ny1: float = 0.0,
        nx2: float = 1.0,
        ny2: float = 1.0,
    ) -> dict:
        """
        Get an absolute pixel region for screen capture, given normalized
        sub-region coordinates within the container.
        Returns dict with keys: left, top, width, height (for mss).
        """
        if not self.rect.valid:
            raise ValueError("Container is not valid")

        left = round(self.rect.x + nx1 * self.rect.w)
        top = round(self.rect.y + ny1 * self.rect.h)
        right = round(self.rect.x + nx2 * self.rect.w)
        bottom = round(self.rect.y + ny2 * self.rect.h)

        return {
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top,
        }

    # --- Private Methods ---------------------------------------------------

    def _find_by_process(self) -> Optional[int]:
        """Find the Roblox window by process name."""
        target_lower = self.PROCESS_NAME.lower()
        result = None

        def enum_callback(hwnd, _):
            nonlocal result
            if not win32gui.IsWindowVisible(hwnd):
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                # Open process to get name
                import psutil
                proc = psutil.Process(pid)
                if proc.name().lower() == target_lower:
                    result = hwnd
                    return False  # Stop enumeration
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(enum_callback, None)
        except Exception:
            pass

        if result:
            logger.info("Found Roblox window by process name, hwnd=%s", result)
        return result

    def _find_by_class(self) -> Optional[int]:
        """Find the Roblox window by known window class names."""
        for cls_name in self.WINDOW_CLASSES:
            try:
                hwnd = win32gui.FindWindow(cls_name, None)
                if hwnd and win32gui.IsWindowVisible(hwnd):
                    logger.info(
                        "Found Roblox window by class '%s', hwnd=%s", cls_name, hwnd
                    )
                    return hwnd
            except Exception:
                continue
        return None

    def _find_by_title(self) -> Optional[int]:
        """Find the Roblox window by title containing 'Roblox'."""
        result = None

        def enum_callback(hwnd, _):
            nonlocal result
            if not win32gui.IsWindowVisible(hwnd):
                return True
            try:
                title = win32gui.GetWindowText(hwnd)
                if "roblox" in title.lower():
                    result = hwnd
                    return False
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(enum_callback, None)
        except Exception:
            pass

        if result:
            logger.info("Found Roblox window by title, hwnd=%s", result)
        return result

    def _extract_client_rect(self) -> bool:
        """Extract the client-area rectangle from the stored window handle."""
        if not self.hwnd:
            return False

        try:
            # GetClientRect gives us width/height (left/top are always 0)
            client_rect = win32gui.GetClientRect(self.hwnd)
            client_w = client_rect[2]  # right
            client_h = client_rect[3]  # bottom

            # ClientToScreen converts client (0,0) to absolute screen coords
            origin = win32gui.ClientToScreen(self.hwnd, (0, 0))

            self.rect = ContainerRect(
                x=origin[0],
                y=origin[1],
                w=client_w,
                h=client_h,
                valid=(client_w > 0 and client_h > 0),
            )
            logger.info("Container detected: %s", self.rect)
            return self.rect.valid

        except Exception as e:
            logger.error("Failed to extract client rect: %s", e)
            self.rect.valid = False
            return False
