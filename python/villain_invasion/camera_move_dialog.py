"""
villain_invasion/camera_move_dialog.py
==========================================
Modal dialog for defining a camera_move event inside the Sequence Editor.

The user draws an arrow on a scaled preview canvas that represents the
Roblox container.  This avoids the HWND_TOPMOST z-order fight with the
_sync_loop in gui.py (which re-asserts Roblox on top every 250 ms).

Arrow phases:
    idle     -> waiting for the first click-and-drag
    drawing  -> mouse held; arrow drawn live with auto-snap
    done     -> arrow finalised; confirm / curve buttons active
    curve    -> one-more-click mode to place the bezier control point

Coordinate convention:
    All coordinates returned via on_complete() are NORMALISED (0.0-1.0)
    relative to the preview canvas dimensions, which match the container
    aspect ratio.  The caller re-projects them via container.denormalize_coords()
    during playback -- no absolute screen positions are stored here.
"""

import math
import tkinter as tk
from tkinter import messagebox
from typing import Callable, Optional, Tuple
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from popup_utils import get_safe_popup_coords

_C = {
    "bg":       "#1e1e2e",
    "panel":    "#181825",
    "fg":       "#cdd6f4",
    "accent":   "#89b4fa",
    "success":  "#a6e3a1",
    "warning":  "#f9e2af",
    "danger":   "#f38ba8",
    "muted":    "#585b70",
    "entry_bg": "#313244",
    "btn_bg":   "#45475a",
}

_CW: int = 420
_CH: int = 307

_SNAP_ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
_SNAP_THRESH = 10


class CameraMoveDialog(tk.Toplevel):
    """
    Modal dialog for creating a camera_move sequence event.

    Parameters
    ----------
    parent      : Parent Tk widget (the SequenceEditor Toplevel).
    on_complete : Callback(start_nx, start_ny, end_nx, end_ny,
                           ctrl_nx, ctrl_ny, duration_ms).
                  ctrl_nx/ctrl_ny are None for straight arrows.
    on_cancel   : Optional callback on dismiss without confirm.
    """

    def __init__(self, parent, container, container_canvas, on_complete, on_cancel=None):
        super().__init__(parent)
        self._container   = container
        self._container_canvas = container_canvas
        self._on_complete = on_complete
        self._on_cancel   = on_cancel

        self._phase       = "idle"
        self._start       = None
        self._end         = None
        self._ctrl        = None
        self._snap_active = False

        self._duration_var = tk.IntVar(value=500)
        self._sv_hint      = tk.StringVar(value="Click and drag to draw the camera movement arrow")
        self._sv_snap      = tk.StringVar(value="")

        self._build_ui()
        self.title("3D Camera Move  --  Draw Arrow")
        self.configure(bg=_C["bg"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.attributes("-topmost", True)
        
        self.update_idletasks()
        
        # Calculate dynamic position
        x, y = get_safe_popup_coords(parent, 500, 520, self._container_canvas)
        self.geometry(f"+{x}+{y}")
        
        self.focus_force()

    def _build_ui(self):
        C = _C

        hdr = tk.Frame(self, bg=C["panel"], height=40)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="3D Camera Move",
                 font=("Segoe UI", 11, "bold"), fg=C["accent"], bg=C["panel"]
                 ).pack(side="left", padx=14, pady=8)

        tk.Label(self, textvariable=self._sv_hint,
                 font=("Segoe UI", 8, "italic"), fg=C["warning"], bg=C["bg"]
                 ).pack(fill="x", padx=12, pady=(6, 0))

        tk.Label(self, textvariable=self._sv_snap,
                 font=("Segoe UI", 8, "bold"), fg="#ffd700", bg=C["bg"]
                 ).pack(fill="x", padx=12)

        cf = tk.Frame(self, bg=C["panel"], bd=1, relief="flat")
        cf.pack(padx=12, pady=6)

        self._canvas = tk.Canvas(cf, width=_CW, height=_CH, bg="#0d0d1a",
                                 highlightthickness=1,
                                 highlightbackground=C["muted"],
                                 cursor="crosshair")
        self._canvas.pack()

        m = 6
        self._canvas.create_rectangle(m, m, _CW-m, _CH-m,
                                       outline=C["muted"], width=1, dash=(4,4))
        self._canvas.create_text(_CW//2, m+10,
                                  text="Container Preview  --  draw arrow here",
                                  fill=C["muted"], font=("Segoe UI", 8))
        for frac in (0.25, 0.5, 0.75):
            gx = round(m + frac*(_CW-2*m))
            gy = round(m + frac*(_CH-2*m))
            self._canvas.create_line(gx, m, gx, _CH-m, fill="#16162a", dash=(2,4))
            self._canvas.create_line(m, gy, _CW-m, gy, fill="#16162a", dash=(2,4))

        self._canvas.bind("<ButtonPress-1>",   self._on_press)
        self._canvas.bind("<B1-Motion>",       self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Escape>", lambda e: self._cancel())

        cr = tk.Frame(self, bg=C["bg"])
        cr.pack(fill="x", padx=12, pady=(0, 4))

        btn_cfg = dict(bg=C["btn_bg"], fg=C["fg"], relief="flat", bd=0,
                       font=("Segoe UI", 9), cursor="hand2", padx=8, pady=4,
                       activebackground=C["accent"], activeforeground="#1e1e2e")

        tk.Button(cr, text="Clear", command=self._clear, **btn_cfg
                  ).pack(side="left", padx=(0,4))
        self._btn_curve = tk.Button(cr, text="Add Curve",
                                     command=self._toggle_curve, **btn_cfg)
        self._btn_curve.pack(side="left", padx=(0,12))

        tk.Label(cr, text="Duration:", font=("Segoe UI", 9),
                 fg=C["muted"], bg=C["bg"]).pack(side="left", padx=(0,4))
        tk.Spinbox(cr, from_=50, to=30000, increment=50,
                   textvariable=self._duration_var, width=6,
                   bg=C["entry_bg"], fg=C["fg"], insertbackground=C["fg"],
                   relief="flat", font=("Segoe UI", 9),
                   buttonbackground=C["btn_bg"]
                   ).pack(side="left")
        tk.Label(cr, text="ms", font=("Segoe UI", 9),
                 fg=C["muted"], bg=C["bg"]).pack(side="left", padx=(2,0))

        bot = tk.Frame(self, bg=C["panel"])
        bot.pack(fill="x", side="bottom")
        tk.Button(bot, text="Cancel", command=self._cancel,
                  bg=C["btn_bg"], fg=C["fg"], relief="flat", bd=0,
                  font=("Segoe UI", 9), cursor="hand2", padx=10, pady=6
                  ).pack(side="right", padx=12, pady=8)
        tk.Button(bot, text="Confirm", command=self._confirm,
                  bg=C["success"], fg="#1e1e2e", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), cursor="hand2", padx=14, pady=6
                  ).pack(side="right", padx=(0,4), pady=8)

    def _on_press(self, event):
        if self._phase == "idle":
            self._start = (event.x, event.y)
            self._end   = (event.x, event.y)
            self._phase = "drawing"
            self._sv_hint.set("Drag to set direction and length  --  Release to confirm")
        elif self._phase == "curve":
            self._ctrl  = (event.x, event.y)
            self._phase = "done"
            self._sv_hint.set("Curve point set  --  Press Confirm when ready")
            self._draw()

    def _on_drag(self, event):
        if self._phase != "drawing":
            return
        ex, ey, snapped = self._compute_snap(event.x, event.y)
        self._end = (ex, ey)
        self._snap_active = snapped
        self._sv_snap.set("Snapped to guide angle" if snapped else "")
        self._draw()

    def _on_release(self, event):
        if self._phase != "drawing":
            return
        ex, ey, snapped = self._compute_snap(event.x, event.y)
        self._end = (ex, ey)
        self._snap_active = snapped
        self._phase = "done"
        self._sv_snap.set("")
        self._sv_hint.set("Arrow set  --  adjust duration then Confirm, or Add Curve to bend the path")
        self._draw()

    def _compute_snap(self, ex, ey):
        if self._start is None:
            return ex, ey, False
        sx, sy = self._start
        dx, dy = ex - sx, ey - sy
        if dx == 0 and dy == 0:
            return ex, ey, False
        angle  = math.degrees(math.atan2(dy, dx)) % 360
        length = math.hypot(dx, dy)
        for sa in _SNAP_ANGLES:
            diff = min(abs(angle - sa), 360 - abs(angle - sa))
            if diff <= _SNAP_THRESH:
                rad = math.radians(sa)
                return sx + length*math.cos(rad), sy + length*math.sin(rad), True
        return ex, ey, False

    def _draw(self):
        self._canvas.delete("dyn")
        if self._start is None:
            return
        sx, sy = self._start

        if self._phase == "drawing":
            guide_r = max(_CW, _CH) * 2
            for sa in _SNAP_ANGLES:
                rad = math.radians(sa)
                self._canvas.create_line(sx, sy,
                                          sx + guide_r*math.cos(rad),
                                          sy + guide_r*math.sin(rad),
                                          fill="#1e1e40", dash=(3,6), tags="dyn")

        self._canvas.create_oval(sx-6, sy-6, sx+6, sy+6,
                                  fill=_C["success"], outline="", tags="dyn")
        self._canvas.create_text(sx, sy-13, text="START",
                                  fill=_C["success"], font=("Segoe UI", 7), tags="dyn")

        if self._end is None:
            return
        ex, ey = self._end
        color  = "#ffd700" if self._snap_active else _C["accent"]

        if self._ctrl is not None:
            self._draw_bezier_arrow(sx, sy, ex, ey, self._ctrl[0], self._ctrl[1], color)
        else:
            if abs(ex-sx) > 1 or abs(ey-sy) > 1:
                self._canvas.create_line(sx, sy, ex, ey, fill=color, width=2,
                                          arrow=tk.LAST, arrowshape=(12,14,4), tags="dyn")

        self._canvas.create_oval(ex-5, ey-5, ex+5, ey+5,
                                  fill=color, outline="", tags="dyn")
        self._canvas.create_text(ex, ey+13, text="END",
                                  fill=color, font=("Segoe UI", 7), tags="dyn")

        if self._snap_active:
            mx, my = (sx+ex)/2, (sy+ey)/2
            self._canvas.create_text(mx, my-11, text="snapped",
                                      fill="#ffd700", font=("Segoe UI", 7), tags="dyn")

    def _draw_bezier_arrow(self, sx, sy, ex, ey, cx, cy, color):
        steps = 40
        pts   = []
        for i in range(steps + 1):
            t  = i / steps
            bx = (1-t)**2*sx + 2*(1-t)*t*cx + t**2*ex
            by = (1-t)**2*sy + 2*(1-t)*t*cy + t**2*ey
            pts.extend([bx, by])
        if len(pts) >= 4:
            self._canvas.create_line(*pts, fill=color, width=2, smooth=True, tags="dyn")
        self._canvas.create_line(sx, sy, cx, cy, fill="#444466", dash=(2,4), tags="dyn")
        self._canvas.create_line(ex, ey, cx, cy, fill="#444466", dash=(2,4), tags="dyn")
        self._canvas.create_oval(cx-5, cy-5, cx+5, cy+5,
                                  fill=_C["warning"], outline="", tags="dyn")
        self._canvas.create_text(cx, cy-12, text="CTRL",
                                  fill=_C["warning"], font=("Segoe UI", 7), tags="dyn")
        if len(pts) >= 4:
            p1x, p1y = pts[-4], pts[-3]
            p2x, p2y = pts[-2], pts[-1]
            angle = math.atan2(p2y-p1y, p2x-p1x)
            alen  = 12
            self._canvas.create_polygon(
                p2x, p2y,
                p2x - alen*math.cos(angle-0.3), p2y - alen*math.sin(angle-0.3),
                p2x - alen*math.cos(angle+0.3), p2y - alen*math.sin(angle+0.3),
                fill=color, outline="", tags="dyn")

    def _toggle_curve(self):
        if self._ctrl is not None:
            self._ctrl = None
            self._btn_curve.config(text="Add Curve")
            self._sv_hint.set("Curve removed  --  click Add Curve to re-add")
            self._draw()
        elif self._phase == "done":
            self._phase = "curve"
            self._btn_curve.config(text="Remove Curve")
            self._sv_hint.set("Click anywhere on the canvas to place the curve control point")

    def _clear(self):
        self._start = self._end = self._ctrl = None
        self._phase = "idle"
        self._snap_active = False
        self._btn_curve.config(text="Add Curve")
        self._sv_snap.set("")
        self._sv_hint.set("Click and drag to draw the camera movement arrow")
        self._canvas.delete("dyn")

    def _confirm(self):
        if self._start is None or self._end is None:
            messagebox.showwarning("No Arrow", "Draw an arrow first.", parent=self)
            return
        sx, sy = self._start
        ex, ey = self._end
        snx, sny = sx/_CW, sy/_CH
        enx, eny = ex/_CW, ey/_CH
        ctrl_nx = ctrl_ny = None
        if self._ctrl is not None:
            ctrl_nx = self._ctrl[0]/_CW
            ctrl_ny = self._ctrl[1]/_CH
        try:
            duration_ms = max(50, int(self._duration_var.get()))
        except Exception:
            duration_ms = 500
        self._on_complete(snx, sny, enx, eny, ctrl_nx, ctrl_ny, duration_ms)
        self.destroy()

    def _cancel(self):
        if self._on_cancel:
            self._on_cancel()
        self.destroy()
