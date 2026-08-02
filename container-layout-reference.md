# RenzeiMacro — Container & Layout Reference

## Overview

RenzeiMacro uses a two-panel GUI where Roblox is placed **on top** of a container canvas (right panel), while macro controls live in a fixed-width left panel. Roblox stays a normal top-level window — no `SetParent` — so all input (mouse, keyboard, DirectX, `SetCapture`) works natively.

---

## Window Dimensions

| Property | Value | Notes |
|----------|-------|-------|
| **Total window** | `1240 × 740` | Default geometry; resizable, min = same |
| **Controls panel** | `340px` wide | Fixed width, full height, scrollable |
| **Body padding** | `8px` each side | `padx=8, pady=8` on the body frame |
| **Panel gap** | `6px` | Gap between controls and container |
| **Container border** | `2px` | Accent-coloured border frame around canvas |
| **Title bar** | `46px` tall | Custom title bar with app name + version |
| **Container header** | `~26px` tall | "CONTAINER" label + status + Recalibrate btn |

### Container Canvas (computed)

```
container_width  = window_width  - CONTROLS_W - body_padx*2 - gap - border*2
                 = 1240 - 340 - 16 - 6 - 4
                 = 874 px

container_height = window_height - title_bar - body_pady*2 - header - border*2
                 = 740 - 46 - 16 - 26 - 4
                 = 648 px

Actual container ≈ 874 × 648  (varies ±2px with OS DPI/theme)
```

### Roblox Minimum Borderless Size

Roblox Player enforces a minimum window size of approximately **816 × 638** when stripped of decorations. The container must be at least this large for Roblox to fit cleanly.

---

## How the Container Works

### Roblox Positioning (no SetParent)

1. **Detect**: `_poll_for_roblox()` runs every `3000ms`, finds `robloxplayerbeta.exe` via `EnumWindows`.
2. **Strip decorations**: Remove `WS_CAPTION`, `WS_THICKFRAME`, `WS_SYSMENU`, `WS_MINIMIZEBOX`, `WS_MAXIMIZEBOX` (normal style) and `WS_EX_CLIENTEDGE`, `WS_EX_WINDOWEDGE`, `WS_EX_DLGMODALFRAME` (extended style).
3. **Apply frame change**: `SetWindowPos(SWP_FRAMECHANGED)` in a separate call so Windows recalculates the non-client area before the resize.
4. **Position on top**: `SetWindowPos(HWND_TOPMOST, canvas_x, canvas_y, canvas_w, canvas_h)` places Roblox exactly over the container canvas, above the macro tool.
5. **Sync loop**: Every `250ms` + on `<Configure>` events, Roblox follows the container if the macro window is moved/resized.
6. **Re-raise on focus**: `<FocusIn>` binding pushes Roblox back on top after any control-panel interaction.
7. **Restore on close**: Original window style + position are restored when the macro tool closes.

### Why This Approach

| Approach | Problem |
|----------|---------|
| `SetParent` (child window) | Breaks DirectX rendering, RawInput, `SetCapture` — Roblox can't receive input |
| Colour-key transparency (behind) | `LWA_COLORKEY` is unreliable with DPI scaling; creates invisible walls |
| **On-top positioning** ✅ | Roblox stays a normal top-level window; clicks go directly to it; no overlay |

### Critical: pywin32 vs ctypes

All Win32 API calls **must** use `win32gui` (pywin32), **not** `ctypes.windll.user32`.

On 64-bit Python, ctypes defaults to `c_int` (32-bit) for parameters. HWND is a pointer (64-bit), so values like `HWND_TOPMOST` (`-1`) and Roblox's HWND get **truncated**, causing `SetWindowPos`/`SetWindowLong` to silently fail.

```python
# ✅ CORRECT — pywin32 handles 64-bit types
win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, w, h, flags)
win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, new_style)

# ❌ BROKEN on 64-bit — ctypes truncates HWND to 32 bits
ctypes.windll.user32.SetWindowPos(hwnd, -1, x, y, w, h, flags)
```

---

## Coordinate System

All macro recordings use **normalised coordinates** (0.0–1.0) relative to the container rect:

```
normalised_x = (screen_x - container.x) / container.width
normalised_y = (screen_y - container.y) / container.height
```

This makes recordings **resolution-independent** — they work at any window size or screen resolution. The `Container` class (`container.py`) handles normalisation/denormalisation.

---

## Timing Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `CONTAINER_POLL_MS` | `3000` | How often to search for a Roblox window |
| `CONTAINER_SYNC_MS` | `250` | How often to re-sync Roblox position with the canvas |
| `<FocusIn>` re-raise | `50ms` delay | Re-raise Roblox after clicking a control button |

---

## Colour Palette

| Token | Hex | Usage |
|-------|-----|-------|
| `bg` | `#1e1e2e` | Main background |
| `panel` | `#181825` | Controls panel background |
| `fg` | `#cdd6f4` | Default text |
| `accent` | `#89b4fa` | Highlights, borders, section headers |
| `success` | `#a6e3a1` | Status "idle", Play button |
| `warning` | `#f9e2af` | Warning states |
| `danger` | `#f38ba8` | Record button, errors |
| `muted` | `#585b70` | Secondary text, separators |
| `entry_bg` | `#313244` | Input field backgrounds |
| `btn_bg` | `#45475a` | Default button background |

---

## Key Files

| File | Role |
|------|------|
| `gui.py` | Two-panel UI, Roblox detection + positioning, all Win32 window management |
| `container.py` | Container rect storage, coordinate normalisation/denormalisation |
| `main.py` | Service orchestrator — wires GUI, IPC, watchdog, vision |
| `window_embedder.py` | `set_dpi_awareness()` helper (called at GUI startup) |
| `config.py` | App configuration (keybinds, modes, place IDs) |
| `cookie_store.py` | DPAPI-encrypted Roblox cookie storage |
| `ahk/main.ahk` | AHK entry point — launches Python, syncs container rect via IPC |
