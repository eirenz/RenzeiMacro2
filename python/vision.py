"""
vision.py — Template Matching & OCR
====================================
Provides OpenCV template matching for known UI elements and
Tesseract OCR for text reading. All operations use normalized
container coordinates.
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import pytesseract
from PIL import Image

from capture import ScreenCapture
from container import Container

logger = logging.getLogger(__name__)

# Default template directory
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


class VisionService:
    """
    Computer vision service for Roblox UI element detection and text reading.
    Uses OpenCV template matching (fast, for known UI elements) and
    Tesseract OCR (slower, only when actual text must be read).
    """

    def __init__(self, container: Container, capture: ScreenCapture):
        self.container = container
        self.capture = capture
        self._templates: Dict[str, np.ndarray] = {}
        self._templates_gray: Dict[str, np.ndarray] = {}
        self._load_templates()

    def _load_templates(self):
        """Load all template images from the templates directory."""
        if not TEMPLATES_DIR.exists():
            TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
            logger.info("Created templates directory: %s", TEMPLATES_DIR)
            return

        for img_path in TEMPLATES_DIR.glob("*.png"):
            name = img_path.stem  # filename without extension
            template = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
            if template is not None:
                self._templates[name] = template
                self._templates_gray[name] = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
                logger.info("Loaded template: %s (%dx%d)", name, template.shape[1], template.shape[0])
            else:
                logger.warning("Failed to load template: %s", img_path)

    def reload_templates(self):
        """Reload all templates from disk."""
        self._templates.clear()
        self._templates_gray.clear()
        self._load_templates()

    def template_match(
        self,
        template_name: str,
        nx1: float = 0.0,
        ny1: float = 0.0,
        nx2: float = 1.0,
        ny2: float = 1.0,
        threshold: float = 0.8,
    ) -> dict:
        """
        Search for a template image within a container sub-region.

        Args:
            template_name: Name of the template (filename stem from templates/).
            nx1, ny1, nx2, ny2: Normalized region to search within.
            threshold: Minimum confidence threshold (0.0–1.0).

        Returns:
            dict with keys: found (bool), confidence (float),
            nx (float), ny (float) — center of match in container-normalized coords.
        """
        if template_name not in self._templates_gray:
            logger.warning("Template '%s' not found in loaded templates", template_name)
            return {"found": False, "confidence": 0.0, "nx": 0.0, "ny": 0.0,
                    "error": f"Template '{template_name}' not loaded"}

        # Capture the search region
        try:
            screen = self.capture.capture_region(nx1, ny1, nx2, ny2)
        except Exception as e:
            logger.error("Capture failed: %s", e)
            return {"found": False, "confidence": 0.0, "nx": 0.0, "ny": 0.0, "error": str(e)}

        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        template_gray = self._templates_gray[template_name]

        # Check that template is smaller than the search region
        if (template_gray.shape[0] > screen_gray.shape[0]
                or template_gray.shape[1] > screen_gray.shape[1]):
            return {"found": False, "confidence": 0.0, "nx": 0.0, "ny": 0.0,
                    "error": "Template larger than search region"}

        # Run template matching
        result = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val >= threshold:
            # Calculate center of match in the search region (pixels)
            th, tw = template_gray.shape[:2]
            center_x = max_loc[0] + tw / 2
            center_y = max_loc[1] + th / 2

            # Convert to normalized coords within the full container
            # First, normalize within the search region
            region_w = screen.shape[1]
            region_h = screen.shape[0]
            local_nx = center_x / region_w
            local_ny = center_y / region_h

            # Then map back to full container normalized coords
            full_nx = nx1 + local_nx * (nx2 - nx1)
            full_ny = ny1 + local_ny * (ny2 - ny1)

            return {
                "found": True,
                "confidence": round(float(max_val), 4),
                "nx": round(full_nx, 6),
                "ny": round(full_ny, 6),
            }

        return {"found": False, "confidence": round(float(max_val), 4), "nx": 0.0, "ny": 0.0}

    def ocr_read(
        self,
        nx1: float = 0.0,
        ny1: float = 0.0,
        nx2: float = 1.0,
        ny2: float = 1.0,
        preprocess: bool = True,
    ) -> dict:
        """
        Read text from a container sub-region using Tesseract OCR.

        Args:
            nx1, ny1, nx2, ny2: Normalized region to read from.
            preprocess: Apply image preprocessing for better OCR accuracy.

        Returns:
            dict with keys: text (str), confidence (float).
        """
        try:
            img = self.capture.capture_region(nx1, ny1, nx2, ny2)
        except Exception as e:
            logger.error("Capture failed for OCR: %s", e)
            return {"text": "", "confidence": 0.0, "error": str(e)}

        if preprocess:
            img = self._preprocess_for_ocr(img)

        # Convert to PIL for pytesseract
        if len(img.shape) == 2:
            # Already grayscale
            pil_img = Image.fromarray(img)
        else:
            pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

        try:
            # Get text with confidence data
            data = pytesseract.image_to_data(pil_img, output_type=pytesseract.Output.DICT)
            text_parts = []
            confidences = []

            for i, word in enumerate(data["text"]):
                conf = int(data["conf"][i])
                if conf > 0 and word.strip():
                    text_parts.append(word)
                    confidences.append(conf)

            text = " ".join(text_parts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

            return {
                "text": text,
                "confidence": round(avg_confidence / 100.0, 4),  # Normalize to 0–1
            }
        except Exception as e:
            logger.error("OCR failed: %s", e)
            return {"text": "", "confidence": 0.0, "error": str(e)}

    def is_disconnect_dialog_visible(
        self,
        nx1: float = 0.3,
        ny1: float = 0.3,
        nx2: float = 0.7,
        ny2: float = 0.7,
        threshold: float = 0.75,
    ) -> dict:
        """
        Check center-screen region for any disconnect dialog template.
        Tries all templates whose name starts with 'disconnect_'.

        Returns:
            dict with keys: found (bool), template (str), confidence (float).
        """
        disconnect_templates = [
            name for name in self._templates_gray if name.startswith("disconnect_")
        ]

        if not disconnect_templates:
            # No disconnect templates loaded — fall back to OCR-based detection
            return self._ocr_disconnect_check(nx1, ny1, nx2, ny2)

        best_match = {"found": False, "template": "", "confidence": 0.0}

        for template_name in disconnect_templates:
            result = self.template_match(template_name, nx1, ny1, nx2, ny2, threshold)
            if result["found"] and result["confidence"] > best_match["confidence"]:
                best_match = {
                    "found": True,
                    "template": template_name,
                    "confidence": result["confidence"],
                    "nx": result["nx"],
                    "ny": result["ny"],
                }

        return best_match

    def is_game_loaded(self) -> dict:
        """
        Check if the game appears to be loaded (not on a loading screen).
        Uses template matching for 'game_loaded' template, falls back to
        checking that the center-screen area doesn't show a loading indicator.

        Returns:
            dict with keys: loaded (bool), confidence (float).
        """
        # Try template matching first
        if "game_loaded" in self._templates_gray:
            result = self.template_match("game_loaded", threshold=0.7)
            return {"loaded": result["found"], "confidence": result["confidence"]}

        # Fallback: check that no loading screen is visible
        if "loading_screen" in self._templates_gray:
            result = self.template_match("loading_screen", 0.3, 0.3, 0.7, 0.7, 0.7)
            return {"loaded": not result["found"], "confidence": result["confidence"]}

        # No templates available — assume loaded (conservative)
        return {"loaded": True, "confidence": 0.0, "note": "No templates available"}

    # === Private Methods ===================================================

    def _preprocess_for_ocr(self, img: np.ndarray) -> np.ndarray:
        """
        Preprocess an image for better OCR accuracy on Roblox's stylized fonts.
        Steps: grayscale → denoise → threshold → slight dilation.
        """
        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Denoise
        denoised = cv2.fastNlMeansDenoising(gray, h=10)

        # Adaptive threshold (works better with varying backgrounds)
        thresh = cv2.adaptiveThreshold(
            denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        # Slight dilation to connect broken characters
        kernel = np.ones((2, 2), np.uint8)
        dilated = cv2.dilate(thresh, kernel, iterations=1)

        return dilated

    def _ocr_disconnect_check(
        self, nx1: float, ny1: float, nx2: float, ny2: float
    ) -> dict:
        """
        Fallback: use OCR to detect disconnect-related keywords in center screen.
        Only used if no disconnect template images are available.
        """
        result = self.ocr_read(nx1, ny1, nx2, ny2)
        text = result.get("text", "").lower()

        disconnect_keywords = [
            "disconnected",
            "connection lost",
            "kicked",
            "error",
            "lost connection",
            "teleport failed",
            "reconnect",
            "leave",
        ]

        for keyword in disconnect_keywords:
            if keyword in text:
                return {
                    "found": True,
                    "template": "ocr_fallback",
                    "confidence": result.get("confidence", 0.0),
                    "keyword": keyword,
                    "text": text,
                }

        return {"found": False, "template": "", "confidence": 0.0}
