"""
capture.py — Container-Scoped Screen Capture
=============================================
Uses mss for fast, region-specific screen capture.
All capture operations are scoped to the Container rectangle.
"""

import logging
from typing import Optional, Tuple

import mss
import mss.tools
import numpy as np
from PIL import Image

from container import Container

logger = logging.getLogger(__name__)


class ScreenCapture:
    """
    Provides fast screen capture scoped to the Container rectangle.
    Uses mss for minimal-overhead capture.
    """

    def __init__(self, container: Container):
        self.container = container
        self._sct: Optional[mss.mss] = None

    def _get_sct(self) -> mss.mss:
        """Lazy-initialize the mss instance."""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def capture_container(self) -> np.ndarray:
        """
        Capture the entire container region.
        Returns: numpy array (BGR format, ready for OpenCV).
        """
        if not self.container.rect.valid:
            raise ValueError("Container is not valid — cannot capture")

        region = {
            "left": self.container.rect.x,
            "top": self.container.rect.y,
            "width": self.container.rect.w,
            "height": self.container.rect.h,
        }

        sct = self._get_sct()
        screenshot = sct.grab(region)

        # Convert to numpy array (BGRA → BGR for OpenCV)
        img = np.array(screenshot)
        return img[:, :, :3]  # Drop alpha channel

    def capture_region(
        self,
        nx1: float = 0.0,
        ny1: float = 0.0,
        nx2: float = 1.0,
        ny2: float = 1.0,
    ) -> np.ndarray:
        """
        Capture a sub-region within the container, specified in normalized
        coordinates (0.0–1.0).
        Returns: numpy array (BGR format).
        """
        if not self.container.rect.valid:
            raise ValueError("Container is not valid — cannot capture")

        region = self.container.get_capture_region(nx1, ny1, nx2, ny2)

        sct = self._get_sct()
        screenshot = sct.grab(region)

        img = np.array(screenshot)
        return img[:, :, :3]

    def capture_region_pil(
        self,
        nx1: float = 0.0,
        ny1: float = 0.0,
        nx2: float = 1.0,
        ny2: float = 1.0,
    ) -> Image.Image:
        """
        Capture a sub-region and return as a PIL Image (RGB).
        Useful for OCR which often expects PIL input.
        """
        bgr = self.capture_region(nx1, ny1, nx2, ny2)
        # BGR to RGB
        rgb = bgr[:, :, ::-1]
        return Image.fromarray(rgb)

    def close(self):
        """Release mss resources."""
        if self._sct:
            self._sct.close()
            self._sct = None
