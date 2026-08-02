"""
gui.py — Two-Panel UI: Controls + Container
=============================================
Left panel  : compact macro controls (record/stop/play, status, settings)
Right panel : the CONTAINER — Roblox sits ON TOP of this area.

How the container works:
  1. Python detects the Roblox window.
  2. Roblox is stripped of its title bar/borders (stays top-level).
  3. Roblox is resized and positioned to exactly cover the container
     canvas, sitting ABOVE the macro tool in Z-order.
  4. You click Roblox DIRECTLY — there is no overlay, no invisible wall.
     Roblox is the topmost window in that region.
  5. The left panel (controls) is never covered by Roblox, so it stays
     fully clickable.
  6. Container rect = the canvas screen position, synced continuously.
     Recordings use normalised (0-1) coords → resolution-independent.
"""

import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Optional, Callable

import win32con
import win32gui
import win32process
import psutil

from config import AppConfig
from container import Container
from cookie_store import CookieStore
from window_embedder import set_dpi_awareness
from popup_utils import get_safe_popup_coords

logger = logging.getLogger(__name__)

# ── Layout ──────────────────────────────────────────────────────────────────
CONTROLS_W        = 340
CONTAINER_POLL_MS = 3000
CONTAINER_SYNC_MS = 250     # fast sync for smooth tracking on drag

# ── Win32 decoration masks ──────────────────────────────────────────────────
STYLE_MASK = (
    win32con.WS_CAPTION | win32con.WS_THICKFRAME
    | win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX | win32con.WS_SYSMENU
)
EXSTYLE_MASK = (
    win32con.WS_EX_CLIENTEDGE | win32con.WS_EX_WINDOWEDGE
    | win32con.WS_EX_DLGMODALFRAME
)


class SettingsGUI:
    """
    Two-panel RenzeiMacro UI.
    Left  : controls + settings (scrollable)
    Right : container canvas — Roblox is placed ON TOP of this area
    """

    def __init__(
        self,
        config: AppConfig,
        cookie_store: CookieStore,
        container: Container,
        on_config_changed: Optional[Callable] = None,
        on_container_found: Optional[Callable] = None,
    ):
        self.config = config
        self.cookie_store = cookie_store
        self.container = container
        self.on_config_changed = on_config_changed
        self.on_container_found = on_container_found

        self._root: Optional[tk.Tk] = None
        self._thread: Optional[threading.Thread] = None
        self._roblox_hwnd: int = 0
        self._roblox_positioned: bool = False
        self._container_found_fired: bool = False
        self._saved_roblox_rect: Optional[tuple] = None
        self._saved_roblox_style: Optional[int] = None
        self._saved_roblox_exstyle: Optional[int] = None
        self._container_canvas: Optional[tk.Canvas] = None
        # Minimum size Roblox actually accepted (may be > canvas size)
        self._roblox_min_w: int = 0
        self._roblox_min_h: int = 0
        # Pending after-id for <Configure> debounce
        self._configure_after_id: Optional[str] = None
        self._ocr_selector_open: bool = False
        self._ocr_selector_hwnd: Optional[int] = None

        # Status strings
        self._status_text     = "Idle"
        self._mouse_mode_text = "2D"
        self._connection_text = "Disconnected"

        self._sv_status:     Optional[tk.StringVar] = None
        self._sv_mouse_mode: Optional[tk.StringVar] = None
        self._sv_connection: Optional[tk.StringVar] = None
        self._sv_container:  Optional[tk.StringVar] = None

        self._C = dict(
            bg       = "#1e1e2e",
            panel    = "#181825",
            fg       = "#cdd6f4",
            accent   = "#89b4fa",
            success  = "#a6e3a1",
            warning  = "#f9e2af",
            danger   = "#f38ba8",
            muted    = "#585b70",
            entry_bg = "#313244",
            btn_bg   = "#45475a",
        )

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="SettingsGUI")
        self._thread.start()

    def update_status(self, status: str):
        self._status_text = status
        if self._sv_status:
            try: self._sv_status.set(status)
            except Exception: pass

    def update_mouse_mode(self, mode: str):
        self._mouse_mode_text = mode
        if self._sv_mouse_mode:
            try: self._sv_mouse_mode.set(mode)
            except Exception: pass

    def update_connection(self, status: str):
        self._connection_text = status
        if self._sv_connection:
            try: self._sv_connection.set(status)
            except Exception: pass

    # ── Build ───────────────────────────────────────────────────────────────

    def _run(self):
        set_dpi_awareness()

        C = self._C
        self._root = tk.Tk()
        root = self._root
        root.title("RenzeiMacro")
        # Roblox min borderless ≈ 850×650.  Add controls panel + padding.
        root.geometry("1240x740")
        root.resizable(True, True)
        root.minsize(1240, 740)
        root.configure(bg=C["bg"])
        root.wm_attributes("-topmost", True)

        self._apply_style()

        # ── Title bar ───────────────────────────────────────────────────────
        tb = tk.Frame(root, bg=C["panel"], height=46)
        tb.pack(fill="x")
        tb.pack_propagate(False)
        tk.Label(tb, text="\u26a1  RenzeiMacro",
                 font=("Segoe UI", 14, "bold"),
                 fg=C["accent"], bg=C["panel"]).pack(side="left", padx=14, pady=10)
        tk.Label(tb, text="v1.0",
                 font=("Segoe UI", 9), fg=C["muted"], bg=C["panel"]).pack(side="left", pady=14)

        # ── Body ────────────────────────────────────────────────────────────
        body = tk.Frame(root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=8, pady=8)

        left = tk.Frame(body, bg=C["panel"], width=CONTROLS_W)
        left.pack(side="left", fill="y", padx=(0, 6))
        left.pack_propagate(False)

        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)

        self._left_panel = left
        self._build_mode_selector(left)
        self._build_container_panel(right)

        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Startup check for cookie
        if not self.cookie_store.has_cookie():
            root.after(500, self._show_cookie_manager)

        # Re-position Roblox whenever the macro window moves or resizes
        root.bind("<Configure>", self._on_window_configure)

        # Re-raise Roblox whenever the macro tool regains focus
        root.bind("<FocusIn>", self._raise_roblox)

        root.after(500, self._poll_for_roblox)

        root.mainloop()

    def _apply_style(self):
        C = self._C
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".",              background=C["bg"],     foreground=C["fg"],   fieldbackground=C["entry_bg"])
        s.configure("TLabel",         background=C["bg"],     foreground=C["fg"],   font=("Segoe UI", 10))
        s.configure("TEntry",         fieldbackground=C["entry_bg"], foreground=C["fg"])
        s.configure("TButton",        background=C["btn_bg"], foreground=C["fg"],   font=("Segoe UI", 10))
        s.configure("Accent.TButton", background=C["accent"], foreground="#1e1e2e", font=("Segoe UI", 10, "bold"))
        s.configure("Danger.TButton", background=C["danger"], foreground="#1e1e2e", font=("Segoe UI", 10, "bold"))
        s.configure("Rec.TButton",    background=C["danger"], foreground="#1e1e2e", font=("Segoe UI", 11, "bold"))
        s.configure("Play.TButton",   background=C["success"],foreground="#1e1e2e", font=("Segoe UI", 11, "bold"))
        s.configure("Stop.TButton",   background=C["btn_bg"], foreground=C["fg"],   font=("Segoe UI", 11, "bold"))

    def _show_cookie_manager(self):
        C = self._C
        top = tk.Toplevel(self._root)
        top.title("Roblox Cookie Manager")
        top.geometry("320x350")
        top.configure(bg=C["panel"])
        top.resizable(False, False)
        top.transient(self._root)
        top.grab_set()
        top.attributes("-topmost", True)

        top.update_idletasks()
        # Dynamically calculate safe non-overlapping coordinates for this 320x350 popup
        x, y = get_safe_popup_coords(self._root, 320, 350, self._container_canvas)
        top.geometry(f"+{x}+{y}")

        tk.Label(top, text="\U0001f512 Roblox Authentication", font=("Segoe UI", 12, "bold"),
                 fg=C["accent"], bg=C["panel"]).pack(pady=(20, 5))

        msg = ("To enable Auto-Reconnect, you must log in to Roblox.\n\n"
               "A secure mini-browser will open. Once you log in, your session "
               "cookie will be automatically grabbed and encrypted via Windows DPAPI.\n"
               "It never leaves your computer.")
        tk.Label(top, text=msg, font=("Segoe UI", 9), fg=C["fg"], bg=C["panel"],
                 wraplength=280, justify="center").pack(pady=(0, 20), padx=10)

        err_var = tk.StringVar()
        if self.cookie_store.has_cookie():
            err_var.set("\u2705 Encrypted Cookie already stored.")
        tk.Label(top, textvariable=err_var, font=("Segoe UI", 9, "bold"),
                 fg=C["success"] if self.cookie_store.has_cookie() else C["danger"], bg=C["panel"]).pack()

        btn_frame = tk.Frame(top, bg=C["panel"])
        btn_frame.pack(side="bottom", pady=20)

        def _login():
            import subprocess
            import sys
            import os
            
            script_path = os.path.join(os.path.dirname(__file__), "roblox_login.py")
            err_var.set("Opening browser... Please log in.")
            top.update_idletasks()
            
            try:
                # Calculate safe coordinates for the 330x650 browser window
                x, y = get_safe_popup_coords(self._root, 330, 650, self._container_canvas)
                x_pos = str(x)
                y_pos = str(y)
                
                # Run the login script as a subprocess to avoid blocking Tkinter entirely,
                # passing the dynamic coordinates as arguments.
                p = subprocess.Popen([sys.executable, script_path, x_pos, y_pos], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                
                # Poll the subprocess so we don't freeze the GUI completely
                def check_subprocess():
                    ret = p.poll()
                    if ret is None:
                        top.after(500, check_subprocess)
                    else:
                        out, err = p.communicate()
                        if "SUCCESS" in out:
                            err_var.set("\u2705 Cookie saved successfully!")
                            if hasattr(self, "_sv_cookie_status"):
                                self._sv_cookie_status.set("\u2705 Cookie stored")
                                self._lbl_cookie.config(fg=self._C["success"])
                            top.after(1500, top.destroy)
                        else:
                            err_var.set("\u274c Login window closed or failed.")
                
                check_subprocess()
                
            except Exception as e:
                err_var.set(f"Error launching browser: {e}")

        def _skip():
            top.destroy()

        def _delete():
            self.cookie_store.delete_cookie()
            err_var.set("\u274c Cookie deleted.")
            if hasattr(self, "_sv_cookie_status"):
                self._sv_cookie_status.set("\u274c No cookie stored")
                self._lbl_cookie.config(fg=self._C["danger"])

        tk.Button(btn_frame, text="Skip", command=_skip,
                  bg=C["btn_bg"], fg=C["fg"], relief="flat", padx=10, pady=5, cursor="hand2").pack(side="left", padx=10)
        
        tk.Button(btn_frame, text="Delete Cookie", command=_delete,
                  bg=C["danger"], fg="#1e1e2e", relief="flat", padx=10, pady=5, cursor="hand2").pack(side="left", padx=10)
                  
        tk.Button(btn_frame, text="Log In to Roblox", command=_login,
                  bg=C["accent"], fg="#1e1e2e", relief="flat", font=("Segoe UI", 9, "bold"), padx=10, pady=5, cursor="hand2").pack(side="left", padx=10)

    # ── Mode selector ────────────────────────────────────────────────────────

    def _build_mode_selector(self, parent):
        """First screen shown on launch — pick a macro game mode."""
        C = self._C

        for w in parent.winfo_children():
            w.destroy()

        # Logo / prompt
        tk.Label(parent, text="⚡  RenzeiMacro",
                 font=("Segoe UI", 14, "bold"), fg=C["accent"], bg=C["panel"]
                 ).pack(pady=(32, 2))
        tk.Label(parent, text="Select a game mode to begin",
                 font=("Segoe UI", 9), fg=C["muted"], bg=C["panel"]
                 ).pack(pady=(0, 24))

        _hsep(parent, C, panel=True)

        _sect(parent, "GAME MODE", C, panel=True, top=12)

        modes = [
            ("None",                              "General macro controls\n(record / stop / play)",  "default"),
            ("Villain Invasion\n(Villain Coin Farm)", "Automated villain coin\nfarming — work in progress", "villain_invasion"),
        ]

        for name, desc, key in modes:
            card = tk.Frame(parent, bg=C["entry_bg"], cursor="hand2")
            card.pack(fill="x", padx=14, pady=6)

            inner = tk.Frame(card, bg=C["entry_bg"])
            inner.pack(fill="x", padx=12, pady=10)

            tk.Label(inner, text=name,
                     font=("Segoe UI", 10, "bold"), fg=C["fg"], bg=C["entry_bg"],
                     justify="left", anchor="w").pack(anchor="w")
            tk.Label(inner, text=desc,
                     font=("Segoe UI", 8), fg=C["muted"], bg=C["entry_bg"],
                     justify="left", anchor="w").pack(anchor="w", pady=(2, 0))

            # Bind the whole card + children
            for widget in (card, inner) + tuple(inner.winfo_children()):
                widget.bind("<Button-1>", lambda e, k=key: self._select_mode(k))
                widget.bind("<Enter>",    lambda e, f=card: f.config(bg=C["btn_bg"]))
                widget.bind("<Leave>",    lambda e, f=card: f.config(bg=C["entry_bg"]))

    def _select_mode(self, mode_key: str):
        """Switch the left panel to the chosen mode."""
        parent = self._left_panel
        for w in parent.winfo_children():
            w.destroy()
            
        self.config.active_mode_name = mode_key
        self.config.save()
        
        if mode_key == "default":
            self._build_back_button(parent, label="None")
            self._build_controls(parent)
        elif mode_key == "villain_invasion":
            self._build_back_button(parent, label="Villain Invasion")
            self._build_villain_invasion_panel(parent)

    def _build_back_button(self, parent, label: str):
        """Small header strip with active mode name + back link."""
        C = self._C
        bar = tk.Frame(parent, bg=C["panel"])
        bar.pack(fill="x")
        tk.Label(bar, text=f"● {label}",
                 font=("Segoe UI", 8, "bold"), fg=C["accent"], bg=C["panel"]
                 ).pack(side="left", padx=10, pady=6)
        back_lbl = tk.Label(bar, text="\u27f5 Change mode",
                            font=("Segoe UI", 8), fg=C["muted"], bg=C["panel"],
                            cursor="hand2")
        back_lbl.pack(side="right", padx=10, pady=6)
        back_lbl.bind("<Button-1>", lambda e: self._build_mode_selector(self._left_panel))
        tk.Frame(parent, bg=C["muted"], height=1).pack(fill="x", padx=6)

    def _build_villain_invasion_panel(self, parent):
        """Villain Invasion (Villain Coin Farm) — full panel."""
        import os as _os
        from villain_invasion.vi_config import VillainInvasionConfig
        from villain_invasion.sequence_editor import SequenceEditor
        from villain_invasion.ocr_region_selector import OcrRegionSelector

        C = self._C
        self._vi_config = VillainInvasionConfig.load()

        outer = tk.Frame(parent, bg=C["panel"])
        outer.pack(fill="both", expand=True)

        # Scrollable inner frame
        cv = tk.Canvas(outer, bg=C["panel"], highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        sf = tk.Frame(cv, bg=C["panel"])
        sf.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        win_id = cv.create_window((0, 0), window=sf, anchor="nw")
        cv.bind("<Configure>", lambda e: cv.itemconfig(win_id, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def _hsep_vi():
            tk.Frame(sf, bg=C["muted"], height=1).pack(fill="x", padx=10, pady=(10, 6))

        def _sect_vi(title):
            tk.Label(sf, text=title, font=("Segoe UI", 8, "bold"),
                     fg=C["accent"], bg=C["panel"]).pack(anchor="w", padx=12, pady=(4, 2))

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(sf, bg=C["panel"])
        hdr.pack(fill="x", pady=(14, 2))
        
        tk.Label(hdr, text="\u2694  Villain Invasion",
                 font=("Segoe UI", 12, "bold"), fg=C["accent"], bg=C["panel"]
                 ).pack(side="left", padx=(12, 0))
                 
        tk.Button(hdr, text="\U0001f512 Auth",
                  bg=C["btn_bg"], fg=C["fg"], relief="flat", bd=0,
                  font=("Segoe UI", 8), cursor="hand2", padx=6, pady=2,
                  activebackground=C["accent"], activeforeground="#1e1e2e",
                  command=self._show_cookie_manager).pack(side="right", padx=(0, 12))

        tk.Label(sf, text="Villain Coin Farm",
                 font=("Segoe UI", 9), fg=C["muted"], bg=C["panel"]).pack()

        _hsep_vi()

        # ── Private server URL ────────────────────────────────────────────────
        _sect_vi("PRIVATE SERVER")
        tk.Label(sf, text="Paste your Roblox private server URL:",
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["panel"]).pack(anchor="w", padx=12)
        self._vi_url_var = tk.StringVar(value=self._vi_config.private_server_url)
        ttk.Entry(sf, textvariable=self._vi_url_var).pack(fill="x", padx=12, pady=(2, 4))

        self._vi_parse_var = tk.StringVar(value="")
        self._vi_parse_lbl = tk.Label(sf, textvariable=self._vi_parse_var,
                                      font=("Segoe UI", 8, "italic"),
                                      fg=C["muted"], bg=C["panel"],
                                      wraplength=280, justify="left")
        self._vi_parse_lbl.pack(anchor="w", padx=12)

        def _refresh_parse():
            url = self._vi_url_var.get().strip()
            if not url:
                self._vi_parse_var.set("")
                return
            result = self._vi_config.set_private_server_url(url)
            if result["error"]:
                self._vi_parse_var.set(f"\u26a0 {result['error']}")
                self._vi_parse_lbl.config(fg=C["warning"])
            else:
                self._vi_parse_var.set(
                    f"\u2705 Place {result['place_id']}  \u00b7  Link code parsed"
                )
                self._vi_parse_lbl.config(fg=C["success"])

        def _save_url():
            _refresh_parse()
            self._vi_config.save()

        tk.Button(sf, text="\U0001f4be Save URL",
                  bg=C["btn_bg"], fg=C["fg"], relief="flat", bd=0,
                  font=("Segoe UI", 9), cursor="hand2", padx=8, pady=4,
                  activebackground=C["accent"], activeforeground="#1e1e2e",
                  command=_save_url).pack(anchor="w", padx=12, pady=(0, 4))

        if self._vi_config.private_server_url:
            _refresh_parse()

        _hsep_vi()

        # ── OCR disconnect region ─────────────────────────────────────────────
        _sect_vi("DISCONNECT DETECTION")
        has_region = self._vi_config.has_ocr_region()
        self._vi_ocr_var = tk.StringVar(
            value="\u2705 Region saved \u2014 click to reset" if has_region else "\u2b1c No region set"
        )
        tk.Label(sf, textvariable=self._vi_ocr_var,
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["panel"]).pack(anchor="w", padx=12)

        def _open_ocr_selector():
            if not self._roblox_positioned:
                messagebox.showwarning("Container not active",
                                       "Start Roblox first so the container is active.",
                                       parent=self._root)
                return

            def _on_sel(nx, ny, nw, nh):
                self._vi_config.set_ocr_region(nx, ny, nw, nh)
                self._vi_ocr_var.set(
                    f"\u2705 Region saved  ({nx:.2f}, {ny:.2f})  {nw:.2f}\u00d7{nh:.2f}"
                )

            def _on_destroy(event=None):
                self._ocr_selector_open = False
                self._ocr_selector_hwnd = None

            self._ocr_selector_open = True
            selector = OcrRegionSelector(self._root, self.container, on_select=_on_sel)
            selector.update_idletasks()
            try:
                self._ocr_selector_hwnd = int(selector.wm_frame(), 16)
            except Exception as e:
                logger.error("Failed to get OCR selector HWND: %s", e)
            selector.bind("<Destroy>", _on_destroy, add="+")

        tk.Button(sf, text="\U0001f3af Set OCR Region",
                  bg=C["btn_bg"], fg=C["fg"], relief="flat", bd=0,
                  font=("Segoe UI", 9), cursor="hand2", padx=8, pady=4,
                  activebackground=C["accent"], activeforeground="#1e1e2e",
                  command=_open_ocr_selector).pack(anchor="w", padx=12, pady=(4, 0))

        _hsep_vi()

        # ── Sequence editors ──────────────────────────────────────────────────
        _sect_vi("SEQUENCES")

        # 1. Farm Sequence
        farm_frame = tk.Frame(sf, bg=C["panel"])
        farm_frame.pack(fill="x", padx=12, pady=(4, 0))
        tk.Label(farm_frame, text="Farm Sequence (Loops indefinitely)",
                 font=("Segoe UI", 9, "bold"), fg=C["fg"], bg=C["panel"]).pack(anchor="w")

        self._vi_seq_var = tk.StringVar(value="")
        tk.Label(farm_frame, textvariable=self._vi_seq_var,
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["panel"]).pack(anchor="w")
        self._vi_editor = None

        def _count_events(path: str, var: tk.StringVar):
            import json as _json
            if not _os.path.exists(path):
                var.set("No sequence saved yet")
                return
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    n = len(_json.load(f).get("events", []))
                var.set(f"{n} event{'s' if n != 1 else ''} recorded")
            except Exception:
                var.set("Sequence file unreadable")

        _count_events(self._vi_config.default_sequence_path(), self._vi_seq_var)

        def _open_editor():
            if self._vi_editor and self._vi_editor.winfo_exists():
                self._vi_editor.lift()
                return
            self._vi_editor = SequenceEditor(
                parent=self._root,
                container=self.container,
                container_canvas=self._container_canvas,
                sequence_path=self._vi_config.default_sequence_path(),
                on_save=lambda _: _count_events(self._vi_config.default_sequence_path(), self._vi_seq_var),
            )

        tk.Button(farm_frame, text="\u270f Edit Farm Sequence",
                  bg=C["accent"], fg="#1e1e2e", relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), cursor="hand2", padx=10, pady=5,
                  activebackground=C["fg"], activeforeground="#1e1e2e",
                  command=_open_editor).pack(fill="x", pady=(4, 8))

        _hsep_vi()

        # ── Reconnect status ──────────────────────────────────────────────────
        _sect_vi("RECONNECT")
        self._vi_rc_var = tk.StringVar(value="\U0001f6e1 Auto-Reconnect Ready")
        tk.Label(sf, textvariable=self._vi_rc_var,
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["panel"]).pack(anchor="w", padx=12)
        tk.Label(sf,
                 text="Auto-reconnect fires when OCR detects a\ndisconnect message in the selected region.",
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["panel"],
                 justify="left", wraplength=300).pack(anchor="w", padx=12, pady=(2, 10))

    # ── Left panel (controls) ────────────────────────────────────────────────

    def _build_controls(self, parent):
        C = self._C

        _sect(parent, "MACRO", C, panel=True)
        br = tk.Frame(parent, bg=C["panel"])
        br.pack(fill="x", padx=12, pady=(4, 0))
        ttk.Button(br, text="\u25cf  Record", style="Rec.TButton",
                    command=self._on_record).pack(side="left", fill="x", expand=True, padx=(0,2), ipady=6)
        ttk.Button(br, text="\u25a0  Stop",   style="Stop.TButton",
                    command=self._on_stop  ).pack(side="left", fill="x", expand=True, padx=2,    ipady=6)
        ttk.Button(br, text="\u25b6  Play",   style="Play.TButton",
                    command=self._on_play  ).pack(side="left", fill="x", expand=True, padx=(2,0), ipady=6)

        _hsep(parent, C)

        _sect(parent, "STATUS", C, panel=True)
        self._sv_status     = tk.StringVar(value=self._status_text)
        self._sv_mouse_mode = tk.StringVar(value=self._mouse_mode_text)
        self._sv_connection = tk.StringVar(value=self._connection_text)
        sg = tk.Frame(parent, bg=C["panel"])
        sg.pack(fill="x", padx=12, pady=(4, 0))
        for i, (lbl, sv, col) in enumerate([
            ("State",      self._sv_status,     C["success"]),
            ("Mouse Mode", self._sv_mouse_mode, C["fg"]),
            ("IPC",        self._sv_connection, C["fg"]),
        ]):
            tk.Label(sg, text=f"{lbl}:", font=("Segoe UI", 9),
                     fg=C["muted"], bg=C["panel"]).grid(row=i, column=0, sticky="w", pady=2)
            tk.Label(sg, textvariable=sv, font=("Segoe UI", 10, "bold"),
                     fg=col, bg=C["panel"]).grid(row=i, column=1, sticky="w", padx=8)

        _hsep(parent, C)

        # Scrollable settings
        cv = tk.Canvas(parent, bg=C["panel"], highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=cv.yview)
        sf = tk.Frame(cv, bg=C["panel"])
        sf.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv_window_id = cv.create_window((0, 0), window=sf, anchor="nw")
        cv.bind("<Configure>", lambda e: cv.itemconfig(cv_window_id, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        cv.bind_all("<MouseWheel>", lambda e: cv.yview_scroll(-1*(e.delta//120), "units"))

        self._build_keybinds(sf, C)
        self._build_cookie(sf, C)
        self._build_mode(sf, C)
        ttk.Button(sf, text="\U0001f4be  Save Settings", style="Accent.TButton",
                    command=self._save_all).pack(fill="x", padx=10, pady=(10, 4), ipady=4)

    def _build_keybinds(self, parent, C):
        _hsep(parent, C, panel=True)
        _sect(parent, "KEYBINDS", C, panel=True, top=6)
        g = tk.Frame(parent, bg=C["panel"])
        g.pack(fill="x", padx=12, pady=(4, 6))
        self._keybind_entries = {}
        for i, (action, key) in enumerate(self.config.keybinds.items()):
            tk.Label(g, text=f"{action.replace('_',' ').title()}:",
                     font=("Segoe UI", 9), fg=C["muted"], bg=C["panel"]).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(g, width=9)
            e.insert(0, key)
            e.grid(row=i, column=1, sticky="w", padx=8)
            self._keybind_entries[action] = e

    def _build_cookie(self, parent, C):
        _hsep(parent, C, panel=True)
        _sect(parent, "ROBLOX COOKIE", C, panel=True, top=6)
        inner = tk.Frame(parent, bg=C["panel"])
        inner.pack(fill="x", padx=12, pady=(4, 6))
        has = self.cookie_store.has_cookie()
        badge = "\u2705 Cookie stored" if has else "\u274c No cookie stored"
        col   = C["success"] if has else C["danger"]
        self._sv_cookie_status = tk.StringVar(value=badge)
        self._lbl_cookie = tk.Label(inner, textvariable=self._sv_cookie_status,
                                     font=("Segoe UI", 9, "bold"), fg=col, bg=C["panel"])
        self._lbl_cookie.pack(anchor="w")
        br = tk.Frame(inner, bg=C["panel"])
        br.pack(fill="x", pady=(8, 0))
        ttk.Button(br, text="\U0001f512 Manage Auth", style="Accent.TButton",
                    command=self._show_cookie_manager).pack(side="left", padx=(0, 4))

    def _build_mode(self, parent, C):
        _hsep(parent, C, panel=True)
        _sect(parent, "MACRO MODE", C, panel=True, top=6)
        inner = tk.Frame(parent, bg=C["panel"])
        inner.pack(fill="x", padx=12, pady=(4, 6))
        mode = self.config.active_mode
        fields = [("Mode Name","name",20),("Place ID","place_id",20),("Job ID","job_id",20)]
        self._mode_entries = {}
        for i, (lbl, key, w) in enumerate(fields):
            tk.Label(inner, text=f"{lbl}:", font=("Segoe UI", 9),
                     fg=C["muted"], bg=C["panel"]).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(inner, width=w)
            e.insert(0, mode.get(key, ""))
            e.grid(row=i, column=1, sticky="w", padx=8)
            self._mode_entries[key] = e
        tk.Label(inner, text="Reconnect:", font=("Segoe UI", 9),
                 fg=C["muted"], bg=C["panel"]).grid(row=3, column=0, sticky="w", pady=2)
        pf = tk.Frame(inner, bg=C["panel"])
        pf.grid(row=3, column=1, sticky="w", padx=8)
        self._entry_preset = ttk.Entry(pf, width=17)
        self._entry_preset.insert(0, mode.get("reconnect_preset", ""))
        self._entry_preset.pack(side="left")
        ttk.Button(pf, text="\U0001f4c2", width=2,
                    command=self._browse_preset).pack(side="left", padx=2)

        # ── Reconnect Editor ───────────────────────────────────────────────────
        
        # Add some padding before the editor button
        tk.Frame(inner, bg=C["panel"], height=10).grid(row=4, column=0, columnspan=2)
        
        self._recon_editor = None

        def _open_global_recon_editor():
            path = self._entry_preset.get().strip()
            if not path:
                messagebox.showerror("Error", "Please select a reconnect preset file first.")
                return
                
            if self._recon_editor and self._recon_editor.winfo_exists():
                self._recon_editor.lift()
                return
                
            from villain_invasion.sequence_editor import SequenceEditor
            self._recon_editor = SequenceEditor(
                parent=self._root,
                container=self.container,
                container_canvas=self._container_canvas,
                sequence_path=path,
                on_save=lambda _: None,
            )

        dev_mode = self.config.get("dev_mode", False)
        btn_text = "\u270f Edit Reconnect Sequence" if dev_mode else "\U0001f512 Edit Reconnect Sequence (Dev Only)"
        btn_state = "normal" if dev_mode else "disabled"
        btn_cursor = "hand2" if dev_mode else "arrow"

        tk.Button(inner, text=btn_text, state=btn_state,
                  bg=C["btn_bg"], fg=C["fg"], relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), cursor=btn_cursor, padx=10, pady=5,
                  activebackground=C["accent"], activeforeground="#1e1e2e",
                  command=_open_global_recon_editor).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 8))

    # ── Right panel — container ──────────────────────────────────────────────

    def _build_container_panel(self, parent):
        C = self._C

        # Header row (NOT covered by Roblox — it's above the canvas)
        hdr = tk.Frame(parent, bg=C["bg"])
        hdr.pack(fill="x", pady=(0, 4))
        tk.Label(hdr, text="CONTAINER", font=("Segoe UI", 10, "bold"),
                 fg=C["accent"], bg=C["bg"]).pack(side="left")
        self._sv_container = tk.StringVar(value="\U0001f50d Searching for Roblox...")
        tk.Label(hdr, textvariable=self._sv_container,
                 font=("Segoe UI", 9), fg=C["muted"], bg=C["bg"]).pack(side="left", padx=10)
        ttk.Button(hdr, text="\U0001f504 Recalibrate",
                    command=self._recalibrate).pack(side="right")

        # Accent border frame (visible behind Roblox's edges)
        border = tk.Frame(parent, bg=C["accent"])
        border.pack(fill="both", expand=True)

        # Dark canvas — just a placeholder. Roblox will sit ON TOP of this,
        # not behind it. When Roblox is running, this canvas is invisible.
        self._container_canvas = tk.Canvas(
            border, bg="#0d0d14", highlightthickness=0,
        )
        self._container_canvas.pack(fill="both", expand=True, padx=2, pady=2)
        self._container_canvas.bind("<Configure>", self._draw_placeholder)

    def _draw_placeholder(self, event=None):
        """Draw a placeholder when Roblox is not positioned."""
        cv = self._container_canvas
        w  = cv.winfo_width()  or 600
        h  = cv.winfo_height() or 400
        cv.delete("all")

        if self._roblox_positioned:
            # Roblox covers this canvas — draw nothing
            return

        # Dot grid
        C = self._C
        for x in range(14, w, 28):
            for y in range(14, h, 28):
                cv.create_oval(x-1, y-1, x+1, y+1, fill=C["muted"], outline="")

        cv.create_text(w//2, h//2 - 12, text="\U0001f3ae",
                       font=("Segoe UI Emoji", 28), fill=C["muted"])
        cv.create_text(w//2, h//2 + 24, text="Waiting for Roblox...",
                       font=("Segoe UI", 12, "bold"), fill=C["muted"])
        cv.create_text(w//2, h//2 + 48, text="Roblox will appear here automatically",
                       font=("Segoe UI", 9), fill="#3d3f58")

    # ── Roblox detection + positioning ───────────────────────────────────────

    def _poll_for_roblox(self):
        """Background poll: find Roblox window."""
        if not self._root:
            return

        # Fast path: if we already have a valid Roblox window, don't scan all processes again
        if self._roblox_hwnd and win32gui.IsWindow(self._roblox_hwnd):
            self._root.after(CONTAINER_POLL_MS, self._poll_for_roblox)
            return

        hwnd = self._find_roblox_hwnd()

        if hwnd:
            self._roblox_hwnd = hwnd
            if not self._roblox_positioned:
                self._position_roblox()
        else:
            if self._roblox_positioned:
                self._on_roblox_closed()
            self._roblox_hwnd = 0
            self._roblox_positioned = False
            self._sv_container.set("\U0001f50d Searching for Roblox...")
            self._draw_placeholder()

        self._root.after(CONTAINER_POLL_MS, self._poll_for_roblox)

    def _position_roblox(self):
        """
        Strip Roblox decorations and place it ON TOP of the container canvas.
        Roblox stays a normal top-level window — no SetParent.
        It is the topmost window in the canvas area, so clicks go
        directly to Roblox with no invisible wall.
        """
        if not self._roblox_hwnd or not win32gui.IsWindow(self._roblox_hwnd):
            return

        self._root.update_idletasks()
        cv = self._container_canvas
        x = cv.winfo_rootx()
        y = cv.winfo_rooty()
        w = cv.winfo_width()
        h = cv.winfo_height()

        if w <= 10 or h <= 10:
            return

        # Save original rect + style for restore on close
        try:
            rx, ry, rr, rb = win32gui.GetWindowRect(self._roblox_hwnd)
            self._saved_roblox_rect = (rx, ry, rr - rx, rb - ry)
        except Exception:
            pass

        if self._saved_roblox_style is None:
            self._saved_roblox_style = win32gui.GetWindowLong(
                self._roblox_hwnd, win32con.GWL_STYLE
            )
        if self._saved_roblox_exstyle is None:
            self._saved_roblox_exstyle = win32gui.GetWindowLong(
                self._roblox_hwnd, win32con.GWL_EXSTYLE
            )

        # Un-maximise if needed
        win32gui.ShowWindow(self._roblox_hwnd, win32con.SW_RESTORE)

        # Strip decorations: both normal style (title bar, resize border)
        # and extended style (client edge, window edge, dialog frame).
        # Roblox stays a top-level window — only the frame is removed.
        new_style = self._saved_roblox_style & ~STYLE_MASK
        new_exstyle = self._saved_roblox_exstyle & ~EXSTYLE_MASK
        win32gui.SetWindowLong(self._roblox_hwnd, win32con.GWL_STYLE, new_style)
        win32gui.SetWindowLong(self._roblox_hwnd, win32con.GWL_EXSTYLE, new_exstyle)

        # Step 1: Apply the frame change so Windows recalculates the
        #         non-client area BEFORE we set the final size.
        win32gui.SetWindowPos(
            self._roblox_hwnd, 0,
            0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
            | win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE,
        )

        # Step 2: Position and resize Roblox to fill the container canvas.
        win32gui.SetWindowPos(
            self._roblox_hwnd,
            win32con.HWND_TOPMOST,
            x, y, w, h,
            win32con.SWP_SHOWWINDOW,
        )

        # Verify: read back the actual rect (Roblox may enforce a minimum size)
        try:
            ax, ay, ar, ab = win32gui.GetWindowRect(self._roblox_hwnd)
            aw, ah = ar - ax, ab - ay
            if aw != w or ah != h:
                logger.warning(
                    "Roblox resized to %dx%d instead of requested %dx%d "
                    "(minimum size constraint?)", aw, ah, w, h
                )
                w, h = aw, ah
        except Exception:
            pass

        # Remember the minimum size Roblox actually accepted, so the sync
        # loop never requests smaller and causes a resize oscillation.
        self._roblox_min_w = w
        self._roblox_min_h = h

        # Update container rect
        self.container.set_manual(x, y, w, h)
        self._roblox_positioned = True

        self._sv_container.set(f"\u2705 Active \u2014 {w}\u00d7{h}")
        self._draw_placeholder()
        logger.info("Roblox positioned ON TOP at (%d,%d) %dx%d", x, y, w, h)

        # Fire on_container_found once
        if not self._container_found_fired:
            self._container_found_fired = True
            if self.on_container_found:
                threading.Thread(target=self.on_container_found, daemon=True).start()

        # Start sync loop
        self._root.after(CONTAINER_SYNC_MS, self._sync_loop)

    def _sync_loop(self):
        """Keep Roblox aligned with the canvas and above the macro tool."""
        if not self._root or not self._roblox_positioned:
            return
        if not self._roblox_hwnd or not win32gui.IsWindow(self._roblox_hwnd):
            self._on_roblox_closed()
            return

        self._reposition_roblox()
        self._root.after(CONTAINER_SYNC_MS, self._sync_loop)

    def _reposition_roblox(self):
        """Move Roblox to match the current canvas position and keep it on top."""
        if not self._roblox_hwnd or not self._roblox_positioned:
            return
        cv = self._container_canvas
        try:
            # Enforce style in case Roblox tries to restore its title bar
            current_style = win32gui.GetWindowLong(self._roblox_hwnd, win32con.GWL_STYLE)
            if current_style & win32con.WS_CAPTION:
                new_style = current_style & ~STYLE_MASK
                new_exstyle = win32gui.GetWindowLong(self._roblox_hwnd, win32con.GWL_EXSTYLE) & ~EXSTYLE_MASK
                win32gui.SetWindowLong(self._roblox_hwnd, win32con.GWL_STYLE, new_style)
                win32gui.SetWindowLong(self._roblox_hwnd, win32con.GWL_EXSTYLE, new_exstyle)
                win32gui.SetWindowPos(
                    self._roblox_hwnd, 0, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                    | win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE
                )

            x = cv.winfo_rootx()
            y = cv.winfo_rooty()
            w = max(cv.winfo_width(),  self._roblox_min_w)
            h = max(cv.winfo_height(), self._roblox_min_h)
            if w > 10 and h > 10:
                win32gui.SetWindowPos(
                    self._roblox_hwnd,
                    win32con.HWND_TOPMOST,
                    x, y, w, h,
                    win32con.SWP_NOACTIVATE,
                )
                if self._ocr_selector_open and self._ocr_selector_hwnd:
                    win32gui.SetWindowPos(
                        self._ocr_selector_hwnd,
                        win32con.HWND_TOPMOST,
                        0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                    )
                self.container.set_manual(x, y, w, h)
        except Exception as e:
            logger.debug("reposition error: %s", e)

    def _on_window_configure(self, event):
        """Debounced reposition: fire 150 ms after the last <Configure> event."""
        if event.widget != self._root or not self._roblox_positioned:
            return
        if self._configure_after_id:
            self._root.after_cancel(self._configure_after_id)
        self._configure_after_id = self._root.after(150, self._reposition_roblox)

    def _raise_roblox(self, event=None):
        """
        Re-raise Roblox above the macro tool.
        Bound to <FocusIn> on root — when you click a control button,
        the macro tool briefly comes to front; this pushes Roblox
        back on top after a tiny delay so it stays clickable.
        """
        if not self._roblox_positioned or not self._roblox_hwnd:
            return
        self._root.after(50, self._do_raise_roblox)

    def _do_raise_roblox(self):
        if not self._roblox_positioned or not self._roblox_hwnd or self._ocr_selector_open:
            return
        try:
            win32gui.SetWindowPos(
                self._roblox_hwnd,
                win32con.HWND_TOPMOST,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
            )
        except Exception:
            pass

    def _recalibrate(self):
        """Force re-detect and re-position."""
        self._roblox_positioned = False
        self._saved_roblox_style = None
        self._saved_roblox_exstyle = None
        hwnd = self._find_roblox_hwnd()
        if hwnd:
            self._roblox_hwnd = hwnd
            self._position_roblox()
        else:
            self._sv_container.set("\u26a0 Roblox not found")

    def _on_roblox_closed(self):
        """Handle Roblox window disappearing."""
        self._roblox_positioned = False
        self._container_found_fired = False
        self._saved_roblox_style = None
        self._saved_roblox_exstyle = None
        self._sv_container.set("\U0001f50d Roblox closed \u2014 re-searching...")
        self._draw_placeholder()

    def _restore_roblox(self):
        """Restore Roblox to its original window position and style."""
        if not self._roblox_hwnd:
            return
        try:
            if not win32gui.IsWindow(self._roblox_hwnd):
                return
            # Restore original window style (title bar + borders)
            if self._saved_roblox_style is not None:
                win32gui.SetWindowLong(
                    self._roblox_hwnd, win32con.GWL_STYLE, self._saved_roblox_style
                )
            if self._saved_roblox_exstyle is not None:
                win32gui.SetWindowLong(
                    self._roblox_hwnd, win32con.GWL_EXSTYLE, self._saved_roblox_exstyle
                )
            # Restore original position
            if self._saved_roblox_rect:
                rx, ry, rw, rh = self._saved_roblox_rect
                win32gui.SetWindowPos(
                    self._roblox_hwnd,
                    win32con.HWND_NOTOPMOST,
                    rx, ry, rw, rh,
                    win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW,
                )
            self._roblox_positioned = False
        except Exception as e:
            logger.warning("restore_roblox failed: %s", e)

    # ── Window finder ────────────────────────────────────────────────────────

    @staticmethod
    def _find_roblox_hwnd() -> int:
        result = 0
        def _cb(hwnd, _):
            nonlocal result
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetParent(hwnd):
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                name = psutil.Process(pid).name().lower()
                if name in ("robloxplayerbeta.exe", "robloxplayer.exe"):
                    result = hwnd
                    return False
            except Exception:
                pass
            return True
        try:
            win32gui.EnumWindows(_cb, None)
        except Exception:
            pass
        return result

    # ── Settings actions ─────────────────────────────────────────────────────

    def _on_record(self):
        messagebox.showinfo("Record", f"Press {self.config.keybinds.get('record','F6')} to record.")

    def _on_stop(self):
        messagebox.showinfo("Stop", f"Press {self.config.keybinds.get('stop','F7')} to stop.")

    def _on_play(self):
        messagebox.showinfo("Play", f"Press {self.config.keybinds.get('play','F8')} to play.")

    def _browse_preset(self):
        path = filedialog.askopenfilename(title="Reconnect Preset",
                                          filetypes=[("JSON","*.json"),("All","*.*")])
        if path:
            self._entry_preset.delete(0, tk.END)
            self._entry_preset.insert(0, path)

    def _save_all(self):
        for action, entry in self._keybind_entries.items():
            self.config.set_keybind(action, entry.get().strip())
        mode_name = self.config.active_mode_name
        self.config.set_mode(mode_name, {
            "name":             self._mode_entries["name"].get().strip(),
            "place_id":         self._mode_entries["place_id"].get().strip(),
            "job_id":           self._mode_entries["job_id"].get().strip(),
            "reconnect_preset": self._entry_preset.get().strip(),
        })
        if self.config.save():
            messagebox.showinfo("Settings", "All settings saved.")
            if self.on_config_changed:
                self.on_config_changed()
        else:
            messagebox.showerror("Settings", "Failed to save settings.")

    def _on_close(self):
        """Restore Roblox and close."""
        if self._roblox_positioned:
            self._restore_roblox()
        if self._root:
            self._root.withdraw()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _hsep(parent, C, panel=False):
    tk.Frame(parent, bg=C["muted"], height=1).pack(fill="x", padx=10, pady=4)


def _sect(parent, text, C, panel=False, top=0):
    bg = C["panel"] if panel else C["bg"]
    tk.Label(parent, text=text, font=("Segoe UI", 8, "bold"),
             fg=C["muted"], bg=bg).pack(anchor="w", padx=12, pady=(top, 0))
