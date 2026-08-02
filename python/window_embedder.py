"""
window_embedder.py — Win32 Window Embedding
=============================================
Reparents an external window (Roblox) into a tkinter frame using
SetParent, strips its title bar / borders, and resizes it to fill
the frame exactly.  On release, the original parent and style are
restored so Roblox can continue as a normal window.

Input fix:
    After SetParent, Roblox is a child of the tkinter frame but its
    Win32 message queue is still owned by the Roblox process thread.
    SetFocus() silently fails across process boundaries.
    Fix: AttachThreadInput(tkinter_tid, roblox_tid, TRUE) links the
    two input queues so keyboard focus can be transferred, then
    SetFocus(roblox_hwnd) works correctly.

DPI fix:
    Call set_dpi_awareness() BEFORE creating any window (i.e. before
    Tk()) so that coordinate systems match between Python and Roblox.
"""

import ctypes
import ctypes.wintypes as wintypes
import logging
from typing import Optional

logger = logging.getLogger(__name__)

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 style constants
GWL_STYLE           = -16
GWL_EXSTYLE         = -20
WS_CHILD            = 0x40000000
WS_VISIBLE          = 0x10000000
WS_CLIPCHILDREN     = 0x02000000   # parent doesn't paint over children
WS_CLIPSIBLINGS     = 0x04000000
WS_CAPTION          = 0x00C00000
WS_THICKFRAME       = 0x00040000
WS_MINIMIZEBOX      = 0x00020000
WS_MAXIMIZEBOX      = 0x00010000
WS_SYSMENU          = 0x00080000
WS_OVERLAPPED       = 0x00000000
WS_POPUP            = 0x80000000
WS_OVERLAPPEDWINDOW = (WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU
                       | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
WS_EX_APPWINDOW     = 0x00040000
WS_EX_WINDOWEDGE    = 0x00000100
SWP_NOZORDER        = 0x0004
SWP_NOACTIVATE      = 0x0010
SWP_NOSIZE          = 0x0001
SWP_NOMOVE          = 0x0002
SWP_FRAMECHANGED    = 0x0020
SW_SHOW             = 5
SW_RESTORE          = 9


def set_dpi_awareness():
    """
    Declare the process as Per-Monitor DPI Aware.
    MUST be called before any window (including Tk()) is created.
    Without this, Windows virtualises coordinates between the Python
    app (System DPI by default) and Roblox (Per-Monitor DPI aware),
    causing clicks to land in the wrong position inside the game.
    """
    try:
        # Windows 8.1+: SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        logger.debug("DPI awareness: PROCESS_PER_MONITOR_DPI_AWARE (shcore)")
        return
    except Exception:
        pass
    try:
        # Windows Vista/7 fallback
        user32.SetProcessDPIAware()
        logger.debug("DPI awareness: SetProcessDPIAware (user32)")
    except Exception:
        logger.warning("Could not set DPI awareness")


class WindowEmbedder:
    """
    Embeds an external Win32 window (from any process) into a host
    HWND (e.g. a tkinter Frame) and handles input queue linking.

    Usage:
        embedder = WindowEmbedder()
        embedder.embed(roblox_hwnd, host_hwnd, width, height)
        ...
        embedder.release()   # restore Roblox before app exits
    """

    def __init__(self):
        self._target_hwnd:       int  = 0
        self._original_style:    int  = 0
        self._original_exstyle:  int  = 0
        self._original_parent:   int  = 0
        self._attached_tid:      int  = 0   # Roblox's thread ID we attached to
        self.is_embedded:        bool = False

    # ── Public API ──────────────────────────────────────────────────────────

    def embed(self, target_hwnd: int, host_hwnd: int,
              width: int, height: int) -> bool:
        """
        Embed target_hwnd inside host_hwnd and resize to (width, height).

        1. Add WS_CLIPCHILDREN to the host frame so it doesn't overdraw Roblox
        2. Strip title bar / borders from Roblox, set WS_CHILD
        3. SetParent → reparent into host frame
        4. MoveWindow / SetWindowPos to fill the host
        5. AttachThreadInput + SetFocus so keyboard input flows to Roblox
        """
        if self.is_embedded:
            logger.warning("Already embedded — release first")
            return False

        if not target_hwnd or not host_hwnd:
            logger.error("Invalid HWND(s): target=%s host=%s", target_hwnd, host_hwnd)
            return False

        try:
            self._target_hwnd = target_hwnd

            # ── 1. Make the host frame clip its children ────────────────────
            host_style = user32.GetWindowLongW(host_hwnd, GWL_STYLE)
            user32.SetWindowLongW(host_hwnd, GWL_STYLE,
                                  host_style | WS_CLIPCHILDREN | WS_CLIPSIBLINGS)

            # ── 2. Save + strip Roblox's window decoration ──────────────────
            self._original_style   = user32.GetWindowLongW(target_hwnd, GWL_STYLE)
            self._original_exstyle = user32.GetWindowLongW(target_hwnd, GWL_EXSTYLE)
            self._original_parent  = user32.GetParent(target_hwnd) or 0

            new_style = (
                (self._original_style & ~WS_OVERLAPPEDWINDOW & ~WS_POPUP)
                | WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS
            )
            user32.SetWindowLongW(target_hwnd, GWL_STYLE, new_style)

            new_exstyle = (self._original_exstyle
                           & ~WS_EX_APPWINDOW & ~WS_EX_WINDOWEDGE)
            user32.SetWindowLongW(target_hwnd, GWL_EXSTYLE, new_exstyle)

            # ── 3. Reparent into host frame ─────────────────────────────────
            user32.SetParent(target_hwnd, host_hwnd)

            # ── 4. Resize to fill the host frame ────────────────────────────
            user32.MoveWindow(target_hwnd, 0, 0, width, height, True)
            user32.SetWindowPos(
                target_hwnd, 0, 0, 0, width, height,
                SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )

            # ── 5. Link input queues + hand keyboard focus to Roblox ────────
            self._attach_and_focus(target_hwnd)

            self.is_embedded = True
            logger.info("Embedded hwnd=%s → host=%s  size=%dx%d",
                        target_hwnd, host_hwnd, width, height)
            return True

        except Exception as e:
            logger.error("embed() failed: %s", e, exc_info=True)
            return False

    def give_focus(self):
        """
        Re-transfer keyboard focus to Roblox.
        Call this whenever the macro tool window regains the foreground
        and you want input to keep flowing into the game.
        """
        if self.is_embedded and self._target_hwnd:
            self._attach_and_focus(self._target_hwnd)

    def resize(self, width: int, height: int):
        """Resize the embedded window when the host frame changes size."""
        if not self.is_embedded or not self._target_hwnd:
            return
        try:
            user32.MoveWindow(self._target_hwnd, 0, 0, width, height, True)
        except Exception as e:
            logger.warning("resize() failed: %s", e)

    def release(self) -> bool:
        """Restore Roblox's original parent and window style."""
        if not self.is_embedded or not self._target_hwnd:
            return True
        try:
            hwnd = self._target_hwnd

            # Detach the input queues before releasing
            self._detach_input()

            # Restore original styles
            user32.SetWindowLongW(hwnd, GWL_STYLE,   self._original_style)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, self._original_exstyle)

            # Reparent back to desktop (or original parent)
            user32.SetParent(hwnd, self._original_parent)

            # Force frame change, then show normally
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
                | SWP_NOSIZE | SWP_NOMOVE,
            )
            user32.ShowWindow(hwnd, SW_RESTORE)

            self.is_embedded   = False
            self._target_hwnd  = 0
            logger.info("Released embedded window")
            return True

        except Exception as e:
            logger.error("release() failed: %s", e, exc_info=True)
            return False

    def is_target_alive(self) -> bool:
        """Return True if the embedded window still exists."""
        if not self._target_hwnd:
            return False
        return bool(user32.IsWindow(self._target_hwnd))

    # ── Internal helpers ────────────────────────────────────────────────────

    def _attach_and_focus(self, target_hwnd: int):
        """
        Use AttachThreadInput so that SetFocus works across process
        boundaries, then transfer keyboard focus to target_hwnd.
        """
        try:
            # Get thread IDs
            roblox_tid  = user32.GetWindowThreadProcessId(target_hwnd, None)
            current_tid = kernel32.GetCurrentThreadId()

            if roblox_tid == current_tid:
                # Same thread — SetFocus works directly
                user32.SetFocus(target_hwnd)
                return

            # Link the two input queues
            user32.AttachThreadInput(current_tid, roblox_tid, True)
            self._attached_tid = roblox_tid

            # Now SetFocus works
            user32.SetFocus(target_hwnd)

            # Post a WM_ACTIVATE message so Roblox knows it's "active"
            WM_ACTIVATE = 0x0006
            WA_ACTIVE   = 1
            user32.PostMessageW(target_hwnd, WM_ACTIVATE, WA_ACTIVE, 0)

            logger.debug("AttachThreadInput: tkinter_tid=%s ↔ roblox_tid=%s",
                         current_tid, roblox_tid)

        except Exception as e:
            logger.warning("_attach_and_focus failed: %s", e)

    def _detach_input(self):
        """Detach thread input queues if they were attached."""
        if not self._attached_tid:
            return
        try:
            current_tid = kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(current_tid, self._attached_tid, False)
            self._attached_tid = 0
            logger.debug("AttachThreadInput detached")
        except Exception as e:
            logger.warning("_detach_input failed: %s", e)
