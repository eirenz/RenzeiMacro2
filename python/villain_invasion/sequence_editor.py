"""
villain_invasion/sequence_editor.py
=====================================
Floating popup window for editing the Villain Invasion click/key sequence.

Events supported:
  click     – normalized (nx, ny) within container, button, delay_after_ms
  keypress  – key name (e.g. "e", "space", "f"), delay_after_ms
  delay     – ms
  mousemove – normalized (nx, ny) within container, delay_after_ms

All coords are stored NORMALISED (0.0–1.0). Playback re-projects via
container.denormalize() at runtime — never absolute pixels here.

The editor does NOT touch Roblox's HWND or the container rect in any way.
It reads container.rect ONLY to know where to capture point-clicks.
"""

import ctypes
import ctypes.wintypes
import json
import logging
import math
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from typing import Callable, Dict, List, Optional, Any
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from popup_utils import get_safe_popup_coords

try:
    from .camera_move_dialog import CameraMoveDialog
except ImportError:
    from camera_move_dialog import CameraMoveDialog
from playback_engine import PlaybackEngine

logger = logging.getLogger(__name__)

# ── Colour palette (matches main GUI) ───────────────────────────────────────
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

EVENT_TYPES = ["click", "keypress", "delay", "mousemove", "camera_move"]

# ── Win32 VK code map for recording and playback ─────────────────────────────
# Maps key-name string (as stored in events) → Windows Virtual-Key code.
KEY_VK_MAP: Dict[str, int] = {
    # Letters
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"},
    # Digits (top row)
    **{str(d): 0x30 + d for d in range(10)},
    # Common keys
    "space":    0x20, "enter":    0x0D, "return":   0x0D,
    "escape":   0x1B, "esc":      0x1B, "tab":      0x09,
    "backspace":0x08, "delete":   0x2E, "insert":   0x2D,
    "home":     0x24, "end":      0x23,
    "pageup":   0x21, "pagedown": 0x22,
    "left":     0x25, "up":       0x26, "right":    0x27, "down":    0x28,
    # Function keys
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
    # Modifiers (recorded alone; not injected as hold-combos)
    "shift":    0x10, "ctrl":     0x11, "alt":      0x12,
    "lshift":   0xA0, "rshift":   0xA1,
    "lctrl":    0xA2, "rctrl":    0xA3,
    "lalt":     0xA4, "ralt":     0xA5,
    # Numpad
    **{f"num{d}": 0x60 + d for d in range(10)},
}
# Reverse map: VK code → key name (first match wins)
_VK_TO_NAME: Dict[int, str] = {}
for _kname, _vk in KEY_VK_MAP.items():
    _VK_TO_NAME.setdefault(_vk, _kname)

# VK codes to poll during recording (avoid checking every possible VK)
_RECORD_VK_CODES = list({v for v in KEY_VK_MAP.values()})


# ── Key Mode Dialog ──────────────────────────────────────────────────────────────
class _KeyModeDialog(tk.Toplevel):
    """
    Small modal asking whether a keypress should be instant (press)
    or held for a configurable duration (hold).

    Attributes
    ----------
    result : ("press", 0) | ("hold", hold_ms) | None (cancelled)
    """

    def __init__(self, parent, key_name: str):
        super().__init__(parent)
        self.result = None
        self._hold_var = tk.IntVar(value=500)
        self._mode_var = tk.StringVar(value="press")

        self.title("Key Action")
        self.configure(bg=_C["bg"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.attributes("-topmost", True)
        
        self.update_idletasks()
        # The _KeyModeDialog is 200x200 roughly.
        x, y = get_safe_popup_coords(parent, 200, 200, None)
        self.geometry(f"+{x}+{y}")

        C = _C
        btn_cfg = dict(
            bg=C["btn_bg"], fg=C["fg"], relief="flat", bd=0,
            font=("Segoe UI", 9), cursor="hand2", padx=8, pady=4,
        )

        tk.Label(self, text=f'Key:  "{key_name}"',
                 font=("Segoe UI", 11, "bold"), fg=C["accent"], bg=C["bg"]
                 ).pack(padx=20, pady=(14, 8))

        # Radio buttons
        rf = tk.Frame(self, bg=C["bg"])
        rf.pack(padx=20, pady=4)
        radio_cfg = dict(bg=C["bg"], fg=C["fg"], selectcolor=C["entry_bg"],
                         activebackground=C["bg"], font=("Segoe UI", 9),
                         variable=self._mode_var, command=self._on_mode)
        tk.Radiobutton(rf, text="Press  (instant tap)",
                       value="press", **radio_cfg).grid(row=0, column=0,
                                                        sticky="w", pady=2)
        tk.Radiobutton(rf, text="Hold...",
                       value="hold",  **radio_cfg).grid(row=1, column=0,
                                                        sticky="w", pady=2)

        # Hold duration entry (enabled only in hold mode)
        hf = tk.Frame(self, bg=C["bg"])
        hf.pack(padx=20, pady=(0, 8))
        tk.Label(hf, text="Hold for:", font=("Segoe UI", 9),
                 fg=C["muted"], bg=C["bg"]).pack(side="left")
        self._hold_spin = tk.Spinbox(
            hf, from_=50, to=60000, increment=50,
            textvariable=self._hold_var, width=7,
            bg=C["entry_bg"], fg=C["fg"], insertbackground=C["fg"],
            relief="flat", font=("Segoe UI", 9),
            buttonbackground=C["btn_bg"], state="disabled",
        )
        self._hold_spin.pack(side="left", padx=(6, 2))
        tk.Label(hf, text="ms", font=("Segoe UI", 9),
                 fg=C["muted"], bg=C["bg"]).pack(side="left")

        # Bottom buttons
        bot = tk.Frame(self, bg=C["panel"])
        bot.pack(fill="x")
        tk.Button(bot, text="Cancel", command=self.destroy, **btn_cfg
                  ).pack(side="right", padx=12, pady=8)
        tk.Button(bot, text="Confirm",
                  bg=C["success"], fg="#1e1e2e", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), cursor="hand2", padx=10, pady=4,
                  command=self._confirm).pack(side="right", padx=(0, 4), pady=8)

        self.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self.destroy())
        self.focus_force()

    def _on_mode(self):
        state = "normal" if self._mode_var.get() == "hold" else "disabled"
        self._hold_spin.config(state=state)

    def _confirm(self):
        mode = self._mode_var.get()
        if mode == "hold":
            try:
                hold_ms = max(50, int(self._hold_var.get()))
            except Exception:
                hold_ms = 500
            self.result = ("hold", hold_ms)
        else:
            self.result = ("press", 0)
        self.destroy()


# ── Human-readable event labels ───────────────────────────────────────────────────
# Human-readable labels for the list
def _event_label(ev: Dict[str, Any]) -> str:
    t = ev.get("type", "?")
    d = ev.get("delay_after_ms", 0)
    suffix = f"  +{d}ms" if d else ""
    if t == "click":
        b = ev.get("button", "left")
        return f"\U0001f5b1 Click ({ev.get('nx', 0):.3f}, {ev.get('ny', 0):.3f}) [{b}]{suffix}"
    if t == "keypress":
        hold = ev.get("hold_ms", 0)
        if hold > 0:
            return f"\u2328 Hold [{ev.get('key', '?')}] {hold}ms{suffix}"
        return f"\u2328 Key [{ev.get('key', '?')}]{suffix}"
    if t == "delay":
        return f"\u23f1 Delay {ev.get('ms', 0)} ms"
    if t == "mousemove":
        return f"\u27a4 Move ({ev.get('nx', 0):.3f}, {ev.get('ny', 0):.3f}){suffix}"
    if t == "camera_move":
        snx = ev.get("start_nx", 0)
        sny = ev.get("start_ny", 0)
        enx = ev.get("end_nx",   0)
        eny = ev.get("end_ny",   0)
        dur = ev.get("duration_ms", 0)
        curve = " [curved]" if ev.get("ctrl_nx") is not None else ""
        return (f"\U0001f3a5 3D Move ({snx:.2f},{sny:.2f})\u2192"
                f"({enx:.2f},{eny:.2f}) {dur}ms{curve}{suffix}")
    return f"? {t}"


class SequenceEditor(tk.Toplevel):
    """
    Floating popup for editing a Villain Invasion event sequence.

    Parameters
    ----------
    parent          root Tk window
    container       Container object (used read-only for normalize())
    sequence_path   JSON file to load/save
    on_save         optional callback fired after successful save
    """

    def __init__(
        self,
        parent: tk.Tk,
        container,
        container_canvas: tk.Widget,
        sequence_path: str,
        on_save: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self._container = container
        self._container_canvas = container_canvas
        self._sequence_path = sequence_path
        self._on_save = on_save

        self._playback_engine = PlaybackEngine(self._container)

        self._events: List[Dict[str, Any]] = []
        self._loop = tk.BooleanVar(value=False)
        # ── Point-capture poll state ──────────────────────────────────────
        self._capture_mode: Optional[str] = None   # "click" | "move" | None
        self._poll_active = False
        self._poll_after_id: Optional[str] = None
        self._poll_was_down = False
        self._esc_bind_id: Optional[str] = None
        # ── Recording state ───────────────────────────────────────────────
        self._recording = False
        self._rec_poll_id: Optional[str] = None
        self._rec_last_pos = (0, 0)       # last recorded mouse pos (abs)
        self._rec_last_ts: float = 0.0    # time.monotonic() of last event
        self._rec_lb_was_down = False     # left-button edge detection
        self._rec_rb_was_down = False     # right-button edge detection
        self._rec_key_was_down: Dict[int, bool] = {}  # vk → was_down
        self._rec_active_keys: Dict[int, Dict[str, Any]] = {} # vk -> {"event": dict, "start_ts": float}
        # ── Playback state ────────────────────────────────────────────────
        self._playback_thread: Optional[threading.Thread] = None
        self._playback_stop = False
        # ── Hotkey poll (F9 = instant record toggle) ───────────────────────
        self._hotkey_after_id: Optional[str] = None
        self._hotkey_f9_was_down = False

        self.title("Sequence Editor — Villain Invasion")
        self.configure(bg=_C["bg"])
        self.geometry("600x640")
        self.minsize(480, 400)
        self.resizable(True, True)
        self.transient(parent)              # stays above main window
        self.attributes("-topmost", True)
        
        self.update_idletasks()
        # Dynamically calculate safe non-overlapping coordinates for this 600x640 popup
        x, y = get_safe_popup_coords(parent, 600, 640, self._container_canvas)
        self.geometry(f"+{x}+{y}")

        self._build_ui()
        self._load()

        # Keyboard shortcut: Delete key removes selected item
        self.bind("<Delete>", lambda e: self._delete_selected())

        # Start the global F9 hotkey poll (runs for the window's lifetime)
        self._start_hotkey_poll()

    def destroy(self):
        """Clean up polls before closing."""
        self._poll_active = False
        self._recording = False
        self._playback_stop = True
        if self._hotkey_after_id:
            try:
                self.after_cancel(self._hotkey_after_id)
            except Exception:
                pass
        super().destroy()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        C = _C

        # Title bar area
        top = tk.Frame(self, bg=C["panel"], height=44)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="⚔  Sequence Editor",
                 font=("Segoe UI", 12, "bold"), fg=C["accent"], bg=C["panel"]
                 ).pack(side="left", padx=14, pady=10)

        # ── Toolbar ──────────────────────────────────────────────────────────
        tb = tk.Frame(self, bg=C["bg"])
        tb.pack(fill="x", padx=8, pady=(6, 2))

        btn_cfg = dict(bg=C["btn_bg"], fg=C["fg"], relief="flat", bd=0,
                       font=("Segoe UI", 9), cursor="hand2", padx=8, pady=4,
                       activebackground=C["accent"], activeforeground="#1e1e2e")

        self._btn_add_click = tk.Button(tb, text="+ Click", command=self._start_add_click, **btn_cfg)
        self._btn_add_click.pack(side="left", padx=(0, 4))

        self._btn_add_move = tk.Button(tb, text="+ Move", command=self._start_add_move, **btn_cfg)
        self._btn_add_move.pack(side="left", padx=(0, 4))

        tk.Button(tb, text="+ Key",   command=self._add_keypress, **btn_cfg).pack(side="left", padx=(0, 4))
        tk.Button(tb, text="+ Delay", command=self._add_delay,    **btn_cfg).pack(side="left", padx=(0, 4))
        tk.Button(tb, text="+ 3D",    command=self._add_camera_move,
                  bg="#cba6f7", fg="#1e1e2e", relief="flat", bd=0,
                  font=("Segoe UI", 9), cursor="hand2", padx=8, pady=4,
                  activebackground="#b4befe", activeforeground="#1e1e2e"
                  ).pack(side="left", padx=(0, 4))
        tk.Button(tb, text="Delete", command=self._delete_selected,
                  bg=C["danger"], fg="#1e1e2e", relief="flat", bd=0,
                  font=("Segoe UI", 9), cursor="hand2", padx=8, pady=4).pack(side="left", padx=(0, 4))

        # Up/Down reorder
        tk.Button(tb, text="▲", command=self._move_up,   **btn_cfg).pack(side="left", padx=(8, 2))
        tk.Button(tb, text="▼", command=self._move_down, **btn_cfg).pack(side="left", padx=(0, 4))

        # ── Record / Test toolbar row ───────────────────────────────────────
        tb2 = tk.Frame(self, bg=C["bg"])
        tb2.pack(fill="x", padx=8, pady=(0, 2))

        self._btn_record = tk.Button(
            tb2, text="⏺ Record  [F9]",
            bg="#f38ba8", fg="#1e1e2e", relief="flat", bd=0,
            font=("Segoe UI", 9, "bold"), cursor="hand2", padx=10, pady=4,
            activebackground="#eba0ac", activeforeground="#1e1e2e",
            command=self._toggle_record,
        )
        self._btn_record.pack(side="left", padx=(0, 6))

        self._btn_test = tk.Button(
            tb2, text="▶ Test Sequence",
            bg="#a6e3a1", fg="#1e1e2e", relief="flat", bd=0,
            font=("Segoe UI", 9, "bold"), cursor="hand2", padx=10, pady=4,
            activebackground="#94e2d5", activeforeground="#1e1e2e",
            command=self._toggle_test,
        )
        self._btn_test.pack(side="left", padx=(0, 6))

        # Countdown label shown during 3-2-1 before recording/testing
        self._sv_countdown = tk.StringVar(value="")
        tk.Label(tb2, textvariable=self._sv_countdown,
                 font=("Segoe UI", 9, "bold"), fg=C["warning"], bg=C["bg"]
                 ).pack(side="left", padx=4)

        # ── Status bar (shows "click inside container" hint) ─────────────────
        self._sv_status = tk.StringVar(value="")
        self._lbl_status = tk.Label(self, textvariable=self._sv_status,
                                    font=("Segoe UI", 9, "italic"), fg=C["warning"],
                                    bg=C["bg"], anchor="w")
        self._lbl_status.pack(fill="x", padx=12)

        # ── Event list ───────────────────────────────────────────────────────
        list_frame = tk.Frame(self, bg=C["bg"])
        list_frame.pack(fill="both", expand=True, padx=8, pady=(4, 0))

        sb = ttk.Scrollbar(list_frame, orient="vertical")
        self._listbox = tk.Listbox(
            list_frame,
            bg=C["entry_bg"], fg=C["fg"], selectbackground=C["accent"],
            selectforeground="#1e1e2e", font=("Segoe UI", 10),
            relief="flat", bd=0, activestyle="none",
            yscrollcommand=sb.set,
        )
        sb.config(command=self._listbox.yview)
        sb.pack(side="right", fill="y")
        self._listbox.pack(side="left", fill="both", expand=True)
        self._listbox.bind("<Double-Button-1>", self._edit_selected)

        # ── Bottom bar ───────────────────────────────────────────────────────
        bot = tk.Frame(self, bg=C["panel"])
        bot.pack(fill="x", side="bottom")

        # Loop checkbox
        tk.Checkbutton(bot, text="Loop", variable=self._loop,
                       bg=C["panel"], fg=C["fg"], selectcolor=C["entry_bg"],
                       activebackground=C["panel"], font=("Segoe UI", 9)
                       ).pack(side="left", padx=12, pady=8)

        # Save / Load
        btn2 = dict(bg=C["btn_bg"], fg=C["fg"], relief="flat", bd=0,
                    font=("Segoe UI", 9), cursor="hand2", padx=10, pady=5,
                    activebackground=C["accent"], activeforeground="#1e1e2e")
        tk.Button(bot, text="📂 Load", command=self._load_dialog, **btn2).pack(side="left", padx=4, pady=8)
        tk.Button(bot, text="💾 Save", command=self._save,
                  bg=_C["success"], fg="#1e1e2e", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), cursor="hand2", padx=10, pady=5).pack(side="left", padx=4, pady=8)

        # Clear all
        tk.Button(bot, text="Clear All", command=self._clear_all,
                  bg=_C["danger"], fg="#1e1e2e", relief="flat", bd=0,
                  font=("Segoe UI", 9), cursor="hand2", padx=8, pady=5).pack(side="right", padx=12, pady=8)

    # ── List management ──────────────────────────────────────────────────────

    def _refresh_list(self):
        self._listbox.delete(0, tk.END)
        for ev in self._events:
            self._listbox.insert(tk.END, "  " + _event_label(ev))

    def _selected_index(self) -> Optional[int]:
        sel = self._listbox.curselection()
        return sel[0] if sel else None

    def _move_up(self):
        i = self._selected_index()
        if i is None or i == 0:
            return
        self._events[i - 1], self._events[i] = self._events[i], self._events[i - 1]
        self._refresh_list()
        self._listbox.selection_set(i - 1)

    def _move_down(self):
        i = self._selected_index()
        if i is None or i >= len(self._events) - 1:
            return
        self._events[i + 1], self._events[i] = self._events[i], self._events[i + 1]
        self._refresh_list()
        self._listbox.selection_set(i + 1)

    def _delete_selected(self):
        i = self._selected_index()
        if i is None:
            return
        self._events.pop(i)
        self._refresh_list()

    def _clear_all(self):
        if not self._events:
            return
        if messagebox.askyesno("Clear All", "Remove all events from the sequence?", parent=self):
            self._events.clear()
            self._refresh_list()

    # ── Add events ───────────────────────────────────────────────────────────

    def _start_add_click(self):
        """Enter 'add click' mode — poll for mouse click inside container."""
        self._cancel_point_capture()
        self._capture_mode = "click"
        self._sv_status.set(
            "\U0001f5b1 Click inside the Roblox container to place a click point\u2026  (Esc to cancel)"
        )
        self._btn_add_click.config(bg=_C["accent"], fg="#1e1e2e")
        self._start_mouse_poll()

    def _start_add_move(self):
        """Enter 'add move' mode — poll for mouse click inside container."""
        self._cancel_point_capture()
        self._capture_mode = "move"
        self._sv_status.set(
            "\u27a4 Click inside the Roblox container to place a move target\u2026  (Esc to cancel)"
        )
        self._btn_add_move.config(bg=_C["accent"], fg="#1e1e2e")
        self._start_mouse_poll()

    def _start_mouse_poll(self):
        """
        Poll the OS-level mouse state every 50 ms via GetAsyncKeyState.

        This works regardless of which window is on top (Roblox, overlay, etc.)
        because it reads the hardware key state directly from Windows, not from
        Tkinter events.  No overlay needed — no z-order fight with Roblox.
        """
        import ctypes
        self._poll_was_down = False   # track press→release edge
        self._poll_active = True

        # Bind Escape on the editor window itself to cancel
        self._esc_bind_id = self.bind("<Escape>", lambda e: self._cancel_point_capture())

        def _poll():
            if not self._poll_active:
                return

            VK_LBUTTON = 0x01
            state = ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON)
            is_down = bool(state & 0x8000)

            if is_down and not self._poll_was_down:
                # Button just pressed — record the location
                self._poll_was_down = True
            elif not is_down and self._poll_was_down:
                # Button just released — this is the click moment
                self._poll_was_down = False

                # Read cursor position
                pt = ctypes.wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                sx, sy = pt.x, pt.y

                # Check if inside container bounds
                if self._container.is_point_in_container(sx, sy):
                    mode = self._capture_mode
                    self._cancel_point_capture()
                    self._commit_point(sx, sy, mode)
                    return
                # else: click was outside container, keep polling

            # Schedule next poll
            if self._poll_active:
                self._poll_after_id = self.after(50, _poll)

        self._poll_after_id = self.after(50, _poll)

    def _commit_point(self, sx: int, sy: int, mode: str):
        """Normalize the screen coords and append to the event list."""
        nx, ny = self._container.normalize_coords(sx, sy)
        # Clamp to [0, 1] in case of minor floating point edge
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))

        is_click = (mode == "click")
        delay = self._ask_delay_after()

        if is_click:
            ev = {"type": "click", "nx": round(nx, 4), "ny": round(ny, 4),
                  "button": "left", "delay_after_ms": delay}
        else:
            ev = {"type": "mousemove", "nx": round(nx, 4), "ny": round(ny, 4),
                  "delay_after_ms": delay}

        self._events.append(ev)
        self._refresh_list()
        self._listbox.selection_set(tk.END)
        self._sv_status.set(
            f"\u2705 {'Click' if is_click else 'Move'} recorded at "
            f"({nx:.3f}, {ny:.3f})"
        )
        self.after(2000, lambda: self._sv_status.set(""))

    def _cancel_point_capture(self):
        """Stop the mouse poll and reset UI state."""
        self._poll_active = False
        self._capture_mode = None
        self._sv_status.set("")

        # Cancel any pending poll timer
        poll_id = getattr(self, "_poll_after_id", None)
        if poll_id:
            self.after_cancel(poll_id)
            self._poll_after_id = None

        # Unbind Escape
        esc_id = getattr(self, "_esc_bind_id", None)
        if esc_id:
            self.unbind("<Escape>", esc_id)
            self._esc_bind_id = None

        # Reset button colours
        self._btn_add_click.config(bg=_C["btn_bg"], fg=_C["fg"])
        self._btn_add_move.config(bg=_C["btn_bg"], fg=_C["fg"])

    def _add_keypress(self):
        key = simpledialog.askstring("Add Key",
                                     "Enter key name (e.g. e, space, w, f):",
                                     parent=self)
        if not key:
            return
        key = key.strip()
        dlg = _KeyModeDialog(self, key)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        mode, hold_ms = dlg.result
        delay = self._ask_delay_after()
        self._events.append({
            "type": "keypress",
            "key": key,
            "hold_ms": hold_ms,
            "delay_after_ms": delay,
        })
        self._refresh_list()
        self._listbox.selection_set(tk.END)

    def _add_camera_move(self):
        """Open the CameraMoveDialog; append camera_move event on confirm."""
        def _on_complete(snx, sny, enx, eny, ctrl_nx, ctrl_ny, duration_ms):
            delay = self._ask_delay_after()
            self._events.append({
                "type":        "camera_move",
                "start_nx":    round(snx, 4),
                "start_ny":    round(sny, 4),
                "end_nx":      round(enx, 4),
                "end_ny":      round(eny, 4),
                "ctrl_nx":     round(ctrl_nx, 4) if ctrl_nx is not None else None,
                "ctrl_ny":     round(ctrl_ny, 4) if ctrl_ny is not None else None,
                "duration_ms": duration_ms,
                "delay_after_ms": delay,
            })
            self._refresh_list()
            self._listbox.selection_set(tk.END)

        CameraMoveDialog(self, container=self._container, container_canvas=self._container_canvas, on_complete=_on_complete)

    def _add_delay(self):
        ms = simpledialog.askinteger("Add Delay", "Delay duration (ms):",
                                     initialvalue=1000, minvalue=1, maxvalue=60000, parent=self)
        if ms is None:
            return
        self._events.append({"type": "delay", "ms": ms})
        self._refresh_list()
        self._listbox.selection_set(tk.END)

    def _ask_delay_after(self) -> int:
        ms = simpledialog.askinteger("Delay After",
                                     "Delay after this action (ms, 0 = none):",
                                     initialvalue=200, minvalue=0, maxvalue=60000, parent=self)
        return ms if ms is not None else 0

    # ── Edit ─────────────────────────────────────────────────────────────────

    def _edit_selected(self, _=None):
        i = self._selected_index()
        if i is None:
            return
        ev = self._events[i]
        t  = ev.get("type")

        if t == "delay":
            ms = simpledialog.askinteger("Edit Delay", "Delay (ms):",
                                         initialvalue=ev.get("ms", 1000),
                                         minvalue=1, maxvalue=60000, parent=self)
            if ms is not None:
                ev["ms"] = ms
        elif t in ("click", "mousemove"):
            delay = simpledialog.askinteger("Edit Delay After",
                                            "Delay after action (ms):",
                                            initialvalue=ev.get("delay_after_ms", 0),
                                            minvalue=0, maxvalue=60000, parent=self)
            if delay is not None:
                ev["delay_after_ms"] = delay
            if t == "click":
                btn = simpledialog.askstring("Edit Button",
                                             "Mouse button (left/right/middle):",
                                             initialvalue=ev.get("button", "left"), parent=self)
                if btn:
                    ev["button"] = btn.strip().lower()
        elif t == "keypress":
            key = simpledialog.askstring("Edit Key",
                                         "Key name:", initialvalue=ev.get("key", ""), parent=self)
            if key:
                ev["key"] = key.strip()
            # Hold duration
            hold_ms = simpledialog.askinteger(
                "Hold Duration",
                "Hold duration in ms (0 = instant press):",
                initialvalue=ev.get("hold_ms", 0),
                minvalue=0, maxvalue=60000, parent=self,
            )
            if hold_ms is not None:
                ev["hold_ms"] = hold_ms
            delay = simpledialog.askinteger("Edit Delay After",
                                            "Delay after (ms):",
                                            initialvalue=ev.get("delay_after_ms", 0),
                                            minvalue=0, maxvalue=60000, parent=self)
            if delay is not None:
                ev["delay_after_ms"] = delay
        elif t == "camera_move":
            dur = simpledialog.askinteger(
                "Edit Duration",
                "Camera move duration (ms):",
                initialvalue=ev.get("duration_ms", 500),
                minvalue=50, maxvalue=30000, parent=self,
            )
            if dur is not None:
                ev["duration_ms"] = dur
            delay = simpledialog.askinteger(
                "Edit Delay After",
                "Delay after (ms):",
                initialvalue=ev.get("delay_after_ms", 0),
                minvalue=0, maxvalue=60000, parent=self,
            )
            if delay is not None:
                ev["delay_after_ms"] = delay

        self._refresh_list()

    # ── Save / Load ──────────────────────────────────────────────────────────

    def _load(self):
        path = self._sequence_path
        if not os.path.exists(path):
            logger.info("No sequence file at %s — starting empty", path)
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            self._events = data.get("events", [])
            self._loop.set(bool(data.get("loop", False)))
            self._refresh_list()
            logger.info("Sequence loaded: %d events from %s", len(self._events), path)
        except Exception as e:
            logger.error("Sequence load failed: %s", e)
            messagebox.showerror("Load Error", f"Could not load sequence:\n{e}", parent=self)

    def _save(self):
        path = self._sequence_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            data = {"version": 1, "events": self._events, "loop": self._loop.get()}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info("Sequence saved: %d events → %s", len(self._events), path)
            self._sv_status.set(f"✅ Saved {len(self._events)} events.")
            self.after(2000, lambda: self._sv_status.set(""))
            if self._on_save:
                self._on_save(self._events)
        except Exception as e:
            logger.error("Sequence save failed: %s", e)
            messagebox.showerror("Save Error", f"Could not save sequence:\n{e}", parent=self)

    def _load_dialog(self):
        path = filedialog.askopenfilename(
            title="Load Sequence",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialdir=os.path.dirname(self._sequence_path),
            parent=self,
        )
        if path:
            self._sequence_path = path
            self._events.clear()
            self._load()

    def get_events(self) -> List[Dict[str, Any]]:
        return list(self._events)

    def get_loop(self) -> bool:
        return self._loop.get()

    def _start_hotkey_poll(self):
        """Persistent 100ms poll that listens for F9 (record toggle) globally."""
        VK_F9 = 0x78

        def _poll():
            if not self.winfo_exists():
                return
            try:
                state = ctypes.windll.user32.GetAsyncKeyState(VK_F9)
                is_down = bool(state & 0x8000)
                if is_down and not self._hotkey_f9_was_down:
                    self._toggle_record_instant()
                self._hotkey_f9_was_down = is_down
            except Exception:
                pass
            self._hotkey_after_id = self.after(100, _poll)

        self._hotkey_after_id = self.after(100, _poll)

    def _toggle_record_instant(self):
        """F9 handler — toggle recording with NO countdown."""
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _toggle_record(self):
        if self._recording:
            self._stop_recording()
        else:
            self._countdown(3, self._start_recording)


    def _countdown(self, n: int, callback):
        """Show n-2-1 countdown in the toolbar label, then call callback."""
        if n <= 0:
            self._sv_countdown.set("")
            callback()
            return
        self._sv_countdown.set(f"Starting in {n}...")
        self.after(1000, lambda: self._countdown(n - 1, callback))

    def _start_recording(self):
        """Begin recording mouse + keyboard events inside the container."""
        if not self._container.rect.valid:
            self._sv_status.set("⚠ Container not active — start Roblox first.")
            return
        self._stop_test()   # stop playback if running
        self._recording = True
        self._rec_last_ts = time.monotonic()
        self._rec_key_was_down = {vk: False for vk in _RECORD_VK_CODES}
        self._rec_lb_was_down = False
        self._rec_rb_was_down = False
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        self._rec_last_pos = (pt.x, pt.y)
        self._btn_record.config(text="⏹ Stop Recording", bg="#f38ba8")
        self._sv_status.set("⏺ Recording — click/type inside Roblox container. Press Stop to finish.")
        self._rec_poll_id = self.after(20, self._recording_poll)
        logger.info("Sequence recording started")

    def _stop_recording(self):
        self._recording = False
        if self._rec_poll_id:
            self.after_cancel(self._rec_poll_id)
            self._rec_poll_id = None
        self._btn_record.config(text="⏺ Record", bg="#f38ba8")
        self._sv_status.set(f"✅ Recording stopped — {len(self._events)} events total.")
        self.after(3000, lambda: self._sv_status.set(""))
        logger.info("Sequence recording stopped — %d events", len(self._events))

    def _recording_poll(self):
        """20ms poll: captures mouse clicks, moves, and key presses."""
        if not self._recording:
            return

        now = time.monotonic()
        elapsed_ms = round((now - self._rec_last_ts) * 1000)

        # ── Cursor position ───────────────────────────────────────────────
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        cx, cy = pt.x, pt.y
        inside = self._container.is_point_in_container(cx, cy)

        # ── Left click ────────────────────────────────────────────────────
        lb_state = ctypes.windll.user32.GetAsyncKeyState(0x01)
        lb_down = bool(lb_state & 0x8000)
        if lb_down and not self._rec_lb_was_down and inside:
            nx, ny = self._container.normalize_coords(cx, cy)
            if self._events:
                self._events[-1]["delay_after_ms"] = elapsed_ms
            self._events.append({
                "type": "click",
                "nx": round(nx, 4), "ny": round(ny, 4),
                "button": "left",
                "delay_after_ms": 0,
            })
            self._rec_last_ts = now
            elapsed_ms = 0
            self._refresh_list()
            self._listbox.see(tk.END)
        self._rec_lb_was_down = lb_down

        # ── Right click ───────────────────────────────────────────────────
        rb_state = ctypes.windll.user32.GetAsyncKeyState(0x02)
        rb_down = bool(rb_state & 0x8000)
        if rb_down and not self._rec_rb_was_down and inside:
            nx, ny = self._container.normalize_coords(cx, cy)
            if self._events:
                self._events[-1]["delay_after_ms"] = elapsed_ms
            self._events.append({
                "type": "click",
                "nx": round(nx, 4), "ny": round(ny, 4),
                "button": "right",
                "delay_after_ms": 0,
            })
            self._rec_last_ts = now
            elapsed_ms = 0
            self._refresh_list()
            self._listbox.see(tk.END)
        self._rec_rb_was_down = rb_down

        # ── Mouse move (only inside container, throttled to >5px) ─────────
        if inside and not lb_down and not rb_down:
            lx, ly = self._rec_last_pos
            dist = abs(cx - lx) + abs(cy - ly)  # Manhattan distance
            if dist >= 6:
                nx, ny = self._container.normalize_coords(cx, cy)
                # Only append a move if the previous event wasn't also a move
                # at essentially the same location (prevents duplicate spam)
                if not self._events or self._events[-1].get("type") != "mousemove" or \
                        abs(self._events[-1].get("nx", 0) - nx) > 0.003 or \
                        abs(self._events[-1].get("ny", 0) - ny) > 0.003:
                    if self._events:
                        self._events[-1]["delay_after_ms"] = elapsed_ms
                    self._events.append({
                        "type": "mousemove",
                        "nx": round(nx, 4), "ny": round(ny, 4),
                        "delay_after_ms": 0,
                    })
                    self._rec_last_ts = now
                    elapsed_ms = 0
                    self._refresh_list()
                    self._listbox.see(tk.END)
                self._rec_last_pos = (cx, cy)

        # ── Keys (any key in whitelist, regardless of container focus) ────
        for vk in _RECORD_VK_CODES:
            state = ctypes.windll.user32.GetAsyncKeyState(vk)
            is_down = bool(state & 0x8000)
            was_down = self._rec_key_was_down.get(vk, False)
            if is_down and not was_down:
                key_name = _VK_TO_NAME.get(vk, f"vk{vk:02x}")
                if self._events:
                    self._events[-1]["delay_after_ms"] = elapsed_ms
                
                event_dict = {
                    "type": "keypress",
                    "key": key_name,
                    "hold_ms": 0,
                    "delay_after_ms": 0,
                }
                self._events.append(event_dict)
                self._rec_active_keys[vk] = {"event": event_dict, "start_ts": now}
                self._rec_last_ts = now
                elapsed_ms = 0
                self._refresh_list()
                self._listbox.see(tk.END)
            elif not is_down and was_down:
                active = self._rec_active_keys.pop(vk, None)
                if active:
                    hold_time = int((now - active["start_ts"]) * 1000)
                    ev = active["event"]
                    ev["hold_ms"] = hold_time
                    # If delay_after_ms was already set (because another event happened while holding),
                    # we must subtract hold_time from it to maintain correct timing.
                    if ev["delay_after_ms"] > 0:
                        ev["delay_after_ms"] = max(0, ev["delay_after_ms"] - hold_time)
                    self._refresh_list()
            self._rec_key_was_down[vk] = is_down

        if self._recording:
            self._rec_poll_id = self.after(20, self._recording_poll)

    # ── Playback ──────────────────────────────────────────────────────────────

    def _toggle_test(self):
        if self._playback_engine.is_playing:
            self._stop_test()
        else:
            if not self._events:
                messagebox.showinfo("Empty Sequence",
                                    "No events to play — add or record some first.",
                                    parent=self)
                return
            if not self._container.rect.valid:
                messagebox.showwarning("Container not active",
                                       "Start Roblox first so the container is active.",
                                       parent=self)
                return
            self._countdown(3, self._start_test)

    def _start_test(self):
        self._stop_recording()  # safety: stop recording if still active
        events_snapshot = list(self._events)
        loop = self._loop.get()
        self._btn_test.config(text="⏹ Stop Test", bg="#f9e2af")
        self._sv_status.set("▶ Playing sequence…  press Stop to cancel.")
        self._playback_engine.play(
            events=events_snapshot,
            loop=loop,
            on_finish=lambda: self.after(0, self._reset_test_ui)
        )
        logger.info("Sequence playback started (%d events, loop=%s)", len(events_snapshot), loop)

    def _stop_test(self):
        self._playback_engine.stop()
        self.after(0, self._reset_test_ui)

    def _reset_test_ui(self):
        self._btn_test.config(text="▶ Test Sequence", bg="#a6e3a1")
        self._sv_status.set("✅ Playback finished.")
        self.after(2500, lambda: self._sv_status.set(""))


