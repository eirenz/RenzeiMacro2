"""
villain_invasion/ocr_region_selector.py
=========================================
Semi-transparent overlay for selecting the disconnect-message OCR scan region.

How it works:
  1. A borderless Toplevel is created, sized and positioned to exactly cover
     the container canvas area (read from container.rect — never written to).
  2. The user drags a rectangle on the overlay.
  3. On release the normalized coords are computed and passed to the callback.
  4. The overlay is destroyed.

This code NEVER touches Roblox's HWND or container.set_manual().
It is purely a read-only consumer of container.rect for positioning itself.
"""

import logging
import tkinter as tk
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class OcrRegionSelector(tk.Toplevel):
    """
    Semi-transparent drag-to-select overlay positioned over the container.

    Parameters
    ----------
    parent      main tk.Tk root
    container   Container object (read-only: used only for .rect + .normalize())
    on_select   callback(nx, ny, nw, nh) called once on successful selection
    """

    def __init__(
        self,
        parent: tk.Tk,
        container,
        on_select: Callable[[float, float, float, float], None],
    ):
        super().__init__(parent)
        self._container = container
        self._on_select = on_select

        # Drag state
        self._drag_x0 = 0
        self._drag_y0 = 0
        self._rect_id: Optional[int] = None

        rect = container.rect
        if rect is None:
            logger.error("OcrRegionSelector: container.rect is None — cannot open overlay")
            self.destroy()
            return

        # Position the overlay exactly over the container canvas
        cx, cy, cw, ch = rect.x, rect.y, rect.w, rect.h

        # Borderless, semi-transparent window
        self.overrideredirect(True)
        self.geometry(f"{cw}x{ch}+{cx}+{cy}")
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.35)
        self.configure(bg="#000000")

        # Canvas fills the whole overlay
        self._canvas = tk.Canvas(self, bg="#000010", highlightthickness=0,
                                 cursor="crosshair")
        self._canvas.pack(fill="both", expand=True)

        # Instruction text
        self._canvas.create_text(
            cw // 2, 22,
            text="Drag to select the disconnect message region  •  Esc to cancel",
            fill="#f9e2af", font=("Segoe UI", 10, "bold"),
        )

        # Bindings
        self._canvas.bind("<ButtonPress-1>",   self._on_press)
        self._canvas.bind("<B1-Motion>",        self._on_drag)
        self._canvas.bind("<ButtonRelease-1>",  self._on_release)
        self.bind("<Escape>", lambda e: self.destroy())

        # Grab focus so Escape works
        self.grab_set()
        self.focus_set()

    # ── Drag handlers ────────────────────────────────────────────────────────

    def _on_press(self, event):
        self._drag_x0 = event.x
        self._drag_y0 = event.y
        if self._rect_id:
            self._canvas.delete(self._rect_id)
            self._rect_id = None

    def _on_drag(self, event):
        if self._rect_id:
            self._canvas.delete(self._rect_id)
        self._rect_id = self._canvas.create_rectangle(
            self._drag_x0, self._drag_y0, event.x, event.y,
            outline="#89b4fa", width=2, fill="#89b4fa",
            stipple="gray25",          # light hatching so content is visible
        )

    def _on_release(self, event):
        x0, y0 = min(self._drag_x0, event.x), min(self._drag_y0, event.y)
        x1, y1 = max(self._drag_x0, event.x), max(self._drag_y0, event.y)
        w, h = x1 - x0, y1 - y0

        if w < 10 or h < 10:
            # Too small — ignore
            self.destroy()
            return

        rect = self._container.rect
        # Convert overlay-local coords to screen coords, then normalize
        sx0 = rect.x + x0
        sy0 = rect.y + y0
        sx1 = rect.x + x1
        sy1 = rect.y + y1

        nx0, ny0 = self._container.normalize_coords(sx0, sy0)
        nx1, ny1 = self._container.normalize_coords(sx1, sy1)
        nw = nx1 - nx0
        nh = ny1 - ny0

        logger.info(
            "OCR region selected: screen (%d,%d)-(%d,%d) → normalised "
            "(%.3f, %.3f, %.3f, %.3f)", sx0, sy0, sx1, sy1, nx0, ny0, nw, nh
        )

        self.destroy()
        self._on_select(nx0, ny0, nw, nh)
