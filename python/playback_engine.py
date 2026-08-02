"""
playback_engine.py - Standalone Python Playback Engine
======================================================
Executes recorded JSON events (click, move, keypress, delay, camera_move)
using ctypes and Win32API. Supports looping and DirectX/RawInput injection.
"""

import ctypes
import logging
import threading
import time
import win32gui
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PlaybackEngine")

# Key mapping from string to Virtual Key Code
KEY_VK_MAP = {
    # Alphanumeric
    **{chr(c): c for c in range(0x30, 0x3A)},  # 0-9
    **{chr(c).lower(): c for c in range(0x41, 0x5B)},  # A-Z
    
    # Common keys
    "space": 0x20,
    "enter": 0x0D,
    "esc": 0x1B,
    "tab": 0x09,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "backspace": 0x08,
    "w": 0x57,
    "a": 0x41,
    "s": 0x53,
    "d": 0x44,
    "e": 0x45,
    "f": 0x46,
    "q": 0x51,
    "r": 0x52,
    "t": 0x54,
    "y": 0x59,
    "u": 0x55,
    "i": 0x49,
    "o": 0x4F,
    "p": 0x50,
    "g": 0x47,
    "h": 0x48,
    "j": 0x4A,
    "k": 0x4B,
    "l": 0x4C,
    "z": 0x5A,
    "x": 0x58,
    "c": 0x43,
    "v": 0x56,
    "b": 0x42,
    "n": 0x4E,
    "m": 0x4D,
    
    # Function keys F1-F12
    **{f"f{i}": 0x70 + i - 1 for i in range(1, 13)},
    
    # Punctuation
    "/": 0xBF,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "`": 0xC0,
}

class PlaybackEngine:
    def __init__(self, container):
        self._container = container
        self._playback_thread: Optional[threading.Thread] = None
        self._playback_stop = False
        self._on_finish_callback = None

    def play(self, events: List[Dict[str, Any]], loop: bool = False, on_finish=None):
        """Start playing the sequence in a background thread."""
        self.stop()
        self._playback_stop = False
        self._on_finish_callback = on_finish
        
        self._playback_thread = threading.Thread(
            target=self._worker,
            args=(list(events), loop),
            daemon=True
        )
        self._playback_thread.start()

    def stop(self):
        """Signal the playback thread to stop."""
        self._playback_stop = True
        if self._playback_thread and self._playback_thread.is_alive():
            self._playback_thread.join(timeout=1.0)
        self._playback_thread = None

    @property
    def is_playing(self) -> bool:
        return self._playback_thread is not None and self._playback_thread.is_alive()

    def _worker(self, events: List[Dict[str, Any]], loop: bool):
        try:
            if self._container and self._container.hwnd:
                win32gui.SetForegroundWindow(self._container.hwnd)
                time.sleep(0.2)
        except Exception as e:
            logger.warning("Failed to focus Roblox window for playback: %s", e)

        user32 = ctypes.windll.user32
        
        MOUSEEVENTF_MOVE       = 0x0001
        MOUSEEVENTF_LEFTDOWN   = 0x0002
        MOUSEEVENTF_LEFTUP     = 0x0004
        MOUSEEVENTF_RIGHTDOWN  = 0x0008
        MOUSEEVENTF_RIGHTUP    = 0x0010
        KEYEVENTF_KEYUP        = 0x0002

        def should_stop():
            return self._playback_stop or threading.current_thread() != self._playback_thread

        def interruptible_sleep(duration_sec: float) -> bool:
            """Sleeps for duration_sec. Returns False if interrupted by a stop signal."""
            if duration_sec <= 0.1:
                time.sleep(duration_sec)
                return not should_stop()
            
            end_time = time.time() + duration_sec
            while time.time() < end_time:
                if should_stop():
                    return False
                time.sleep(0.01)
            return True

        def set_cursor_absolute(x: float, y: float):
            user32.SetCursorPos(int(x), int(y))
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            if sw > 0 and sh > 0:
                mx = int((x * 65535) / sw)
                my = int((y * 65535) / sh)
                user32.mouse_event(MOUSEEVENTF_MOVE | 0x8000, mx, my, 0, 0)

        def move_cursor(nx: float, ny: float):
            try:
                ax, ay = self._container.denormalize_coords(nx, ny)
                set_cursor_absolute(ax, ay)
            except Exception as e:
                logger.debug("playback move_cursor error: %s", e)

        def click_mouse(nx: float, ny: float, button: str):
            try:
                ax, ay = self._container.denormalize_coords(nx, ny)
                set_cursor_absolute(ax, ay)
                time.sleep(0.02)
                if button == "right":
                    user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                    time.sleep(0.05)
                    user32.mouse_event(MOUSEEVENTF_RIGHTUP,   0, 0, 0, 0)
                else:
                    user32.mouse_event(MOUSEEVENTF_LEFTDOWN,  0, 0, 0, 0)
                    time.sleep(0.05)
                    user32.mouse_event(MOUSEEVENTF_LEFTUP,    0, 0, 0, 0)
            except Exception as e:
                logger.debug("playback click_mouse error: %s", e)

        def press_key(key_name: str, hold_ms: int = 0):
            vk = KEY_VK_MAP.get(key_name.lower())
            if vk is None:
                if len(key_name) > 1:
                    # Treat as a string and type it out character by character
                    for char in key_name:
                        if should_stop():
                            break
                        press_key(char, 0)
                        interruptible_sleep(0.05)
                    return
                else:
                    logger.warning("playback: unknown key '%s'", key_name)
                    return
            try:
                # DirectX games like Roblox require the hardware scan code
                scan_code = user32.MapVirtualKeyA(vk, 0)
                KEYEVENTF_SCANCODE = 0x0008
                
                user32.keybd_event(vk, scan_code, KEYEVENTF_SCANCODE, 0)
                if not interruptible_sleep(max(0.05, hold_ms / 1000)):
                    # Ensure key is released even if interrupted
                    user32.keybd_event(vk, scan_code, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)
                    return
                user32.keybd_event(vk, scan_code, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)
            except Exception as e:
                logger.debug("playback press_key error: %s", e)

        def camera_move(ev: Dict[str, Any]):
            """Simulate 3D camera movement: hold RMB + move cursor step-by-step."""
            try:
                sx, sy = self._container.denormalize_coords(
                    ev["start_nx"], ev["start_ny"])
                ex, ey = self._container.denormalize_coords(
                    ev["end_nx"], ev["end_ny"])
                ctrl_nx = ev.get("ctrl_nx")
                ctrl_ny = ev.get("ctrl_ny")
                duration = max(0.05, ev.get("duration_ms", 500) / 1000)

                steps = max(10, round(duration * 60))  # ~60 steps/s

                # Teleport cursor to the center of the container to avoid hitting screen edges in Third Person.
                # In First Person, the cursor is already at the center, so this produces a 0-delta (no flick).
                cx, cy = self._container.denormalize_coords(0.5, 0.5)
                set_cursor_absolute(cx, cy)
                time.sleep(0.05)
                user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                time.sleep(0.05)

                step_delay = duration / steps
                last_px = sx
                last_py = sy
                err_x = 0.0
                err_y = 0.0
                
                start_time = time.time()
                for i in range(1, steps + 1):
                    if should_stop():
                        break
                    t = i / steps
                    if ctrl_nx is not None and ctrl_ny is not None:
                        cx, cy = self._container.denormalize_coords(ctrl_nx, ctrl_ny)
                        px = (1-t)**2*sx + 2*(1-t)*t*cx + t**2*ex
                        py = (1-t)**2*sy + 2*(1-t)*t*cy + t**2*ey
                    else:
                        px = sx + t*(ex - sx)
                        py = sy + t*(ey - sy)
                    
                    SENSITIVITY_MULTIPLIER = 3.5
                    dx_float = (px - last_px) * SENSITIVITY_MULTIPLIER
                    dy_float = (py - last_py) * SENSITIVITY_MULTIPLIER
                    
                    move_x = int(dx_float + err_x)
                    move_y = int(dy_float + err_y)
                    
                    err_x = (dx_float + err_x) - move_x
                    err_y = (dy_float + err_y) - move_y
                    
                    user32.mouse_event(MOUSEEVENTF_MOVE, move_x, move_y, 0, 0)
                    last_px = px
                    last_py = py
                    
                    expected_time = start_time + (i * step_delay)
                    sleep_sec = expected_time - time.time()
                    if sleep_sec > 0:
                        if not interruptible_sleep(sleep_sec):
                            break

                user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
            except Exception as e:
                logger.debug("playback camera_move error: %s", e)

        iteration = 0
        while not should_stop():
            iteration += 1
            for i, ev in enumerate(events):
                if should_stop():
                    break
                t = ev.get("type")
                try:
                    if t == "click":
                        click_mouse(ev["nx"], ev["ny"], ev.get("button", "left"))
                    elif t == "mousemove":
                        move_cursor(ev["nx"], ev["ny"])
                    elif t == "keypress":
                        press_key(ev.get("key", ""), ev.get("hold_ms", 0))
                    elif t == "delay":
                        if not interruptible_sleep(ev.get("ms", 0) / 1000):
                            break
                    elif t == "camera_move":
                        camera_move(ev)
                except Exception as e:
                    logger.debug("playback event %d error: %s", i, e)

                # delay_after_ms between events
                # delay_after_ms between events
                dam = ev.get("delay_after_ms", 0)
                if dam > 0 and not should_stop():
                    if not interruptible_sleep(dam / 1000):
                        break

            if not loop:
                break

        logger.info("Sequence playback finished (%d iterations)", iteration)
        if self._on_finish_callback:
            self._on_finish_callback()
