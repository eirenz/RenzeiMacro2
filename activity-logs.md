# RenzeiMacro Activity Logs

This file contains a detailed log of all development activities, bug fixes, and feature implementations for the RenzeiMacro project.

## [2026-07-30 to 2026-07-31] - Villain Invasion Automations & Macro Recorder

### 1. Villain Invasion UI & Configuration
- **New Panel Added:** Implemented the "Villain Invasion (Villain Coin Farm)" mode panel in the main GUI (`gui.py`).
- **Config Management:** Created `villain_invasion/vi_config.py` to handle persistent state using JSON (`presets/villain_invasion/config.json`).
- **URL Parser:** Added automatic parsing for Roblox private server URLs to extract the `place_id` and `link_code` for the relaunch service.

### 2. OCR Region Selector (Disconnect Detection)
- **Overlay Selector:** Created `villain_invasion/ocr_region_selector.py`.
- **Functionality:** Spawns a semi-transparent, borderless window exactly over the Roblox container. Allows the user to click and drag to select a normalized region for the OCR to monitor for disconnect messages.
- **Safety:** The overlay safely sits on top of Roblox without altering Roblox's Win32 properties or the container dimensions.

### 3. Container & GUI Bug Fixes
- **Container Jitter Fixed:** Addressed a 1px height jitter caused by Roblox's minimum window size constraints conflicting with the container.
- **Attribute Fixes:** Fixed crashes related to incorrect attribute names in `ContainerRect` (changed `.width` / `.height` to `.w` / `.h` and `.contains()` to `is_point_in_container()`).

### 4. Sequence Editor & Win32 Polling Engine
- **Sequence Editor UI:** Created `villain_invasion/sequence_editor.py` as a popup window to manage sequences of clicks, moves, delays, and keypresses.
- **Z-Order Issue Resolved:** Discovered that the `HWND_TOPMOST` Roblox window was intercepting all mouse clicks, breaking Tkinter `"<Button-1>"` bindings. An initial overlay approach failed because the `_sync_loop` continually pushed Roblox back to the top.
- **Win32 Polling Implementation:** Replaced the broken point-capture system with a highly reliable OS-level polling engine. It uses `ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON)` and `GetCursorPos()` running on a 50ms Tkinter `after()` loop to detect clicks regardless of window focus.

### 5. Macro Recording System
- **Real-Time Recording:** Implemented a full TinyTask-style macro recorder inside the Sequence Editor.
- **Event Capture:** 
  - Captures Left Clicks and Right Clicks.
  - Captures Mouse Movements (intelligently throttled to >6px distance to prevent log spam).
  - Captures Keyboard Input (whitelisted to common keys, letters, numbers, and modifiers).
- **Container Isolation:** The recorder strictly logs events *only* if the mouse cursor is physically inside the defined Container bounds. Events outside are ignored.
- **F9 Hotkey:** Added a global F9 hotkey (using a 100ms background poll) to instantly start and stop recording, bypassing the standard 3-2-1 countdown.

### 6. Playback Engine
- **Background Threading:** Implemented a non-blocking playback engine using Python's `threading.Thread`.
- **Coordinate Re-projection:** The engine reads the normalized coordinates (0.0 - 1.0) and dynamically converts them to absolute screen coordinates using `container.denormalize_coords()`. This ensures the macro works even if the container is moved.
- **Input Injection:** Uses native Windows API (`SetCursorPos`, `mouse_event`, `keybd_event`) to simulate physical hardware inputs.

### 7. Documentation & Architecture
- Discussed limitations regarding 3D camera movement: The macro currently tracks 2D screen coordinates (`GetCursorPos`) and does not hook into Raw Input deltas, meaning holding right-click to rotate the camera in Roblox is not yet supported.

---

## [2026-08-01] — 3D Camera Move, Key Hold, Arrow Drawer

### 1. 3D Camera Move — Arrow Drawer Dialog
- **New file**: `villain_invasion/camera_move_dialog.py` — `CameraMoveDialog(tk.Toplevel)`.
- **Architecture Decision**: Implemented as a **modal canvas dialog** inside the Sequence Editor instead of a screen overlay. This avoids the HWND_TOPMOST z-order conflict — `_sync_loop` in `gui.py` reasserts Roblox on top every 250ms, which would have destroyed any overlay approach.
- **Canvas**: 420×307 px preview canvas (matches the 874:638 container aspect ratio). Shows a scaled representation of the container with a grid overlay for spatial orientation.
- **Phase Machine**: `idle → drawing → done → [curve]`. Users click-and-drag to draw the arrow; release finalises it.
- **Snap-to-Angle**: While dragging, if the arrow direction is within 10° of any of the 8 cardinal/diagonal angles (0°, 45°, 90°, ..., 315°), the end point snaps to that angle. The arrow turns gold and a "snapped" label appears. Faint guide lines radiate from the start point at all 8 angles while drawing.
- **Bezier Curve Option**: "Add Curve" button enters curve mode; user clicks once more to place a quadratic bezier control point. Shows control handles (dashed lines) and a filled CTRL dot. "Remove Curve" reverts to straight.
- **Duration Field**: `tk.Spinbox` for movement duration in ms (50–30000).
- **Event Format Stored**:
  ```json
  {"type": "camera_move", "start_nx": 0.5, "start_ny": 0.5,
   "end_nx": 0.7, "end_ny": 0.4, "ctrl_nx": null, "ctrl_ny": null,
   "duration_ms": 500, "delay_after_ms": 200}
  ```

### 2. 3D Camera Move — Playback Engine
- **Method**: `camera_move()` inside `_playback_worker` (background thread).
- **Mechanism**: Moves cursor to start, holds RMB (`MOUSEEVENTF_RIGHTDOWN`), step-by-step `SetCursorPos` at ~60 steps/s along either a linear or quadratic bezier path, then releases RMB (`MOUSEEVENTF_RIGHTUP`).
- **Container Safety**: Uses `container.denormalize_coords()` read-only. The container rect and HWND are never touched.

### 3. Key Hold Mode
- **New class**: `_KeyModeDialog(tk.Toplevel)` — small modal with two radio buttons: "Press (instant tap)" and "Hold..." with a configurable ms spinbox.
- **Integrated into `_add_keypress()`**: Now opens `_KeyModeDialog` after the user enters the key name. Returns `("press", 0)` or `("hold", N)`.
- **Event Format**: `hold_ms` field added to keypress events. `hold_ms: 0` = instant press (backwards-compatible; old events without the field default to 0).
- **Labels**: `⌨ Key [w]` for instant press; `⌨ Hold [w] 500ms` for hold events.
- **Playback**: `press_key(key_name, hold_ms)` — `keybd_event(DOWN)` + `sleep(max(0.05, hold_ms/1000))` + `keybd_event(UP)`.
- **Double-click edit**: `_edit_selected()` now shows a hold-duration dialog for keypress events.

### 4. Sequence Editor Toolbar Update
- **`+ 3D` button** added to toolbar row 1 (purple/mauve colour to distinguish from other buttons).
- **`camera_move`** added to `EVENT_TYPES`.
- **`_event_label()`** updated: `🎥 3D Move (0.50,0.50)→(0.70,0.40) 500ms [curved]`.

### 5. Validation
- 58/58 tests pass. Zero regressions across `test_vi_config.py`, `test_core.py`, `test_gui_container.py`.

## [2026-08-01 to 2026-08-02] — Playback Engine Refactor & Container Stability

### 1. The Walk Forward Bug (Zero-Second Holds)
- **Fix**: Re-wrote the recording loop in `sequence_editor.py` to dynamically track key release events. It now calculates exact millisecond hold duration instead of treating all holds as 0ms instant taps with massive delays. AFK walking recordings now correctly maintain key states.

### 2. Thread Race Condition ("Ghost Playback")
- **Fix**: Replaced blocking `time.sleep()` calls in `playback_engine.py` with a custom `interruptible_sleep()` function that loops in 10ms increments. It actively checks a unique Thread ID signature to detect if it has been orphaned by a new play command or stopped, allowing it to terminate itself instantly and preventing multiple macros from running simultaneously.

### 3. Key Hold Interruption Safety
- **Fix**: Upgraded `interruptible_sleep` to intercept stop signals during `hold_ms` actions. If a macro is stopped mid-walk, it now fires a `KEYUP` event to safely release the key before the thread terminates, preventing characters from walking forward forever.

### 4. 3D Camera Timing Drift
- **Fix**: Transitioned the `camera_move` step loop from a fixed `step_delay` to an absolute timeline calculation (`start_time + (i * step_delay)`). This prevents floating-point inaccuracies in Windows timers from accumulating, ensuring camera panning finishes exactly on time down to the millisecond.

### 5. Container Misalignment (Roblox pushed below the UI)
- **Fix**: Added active window style enforcement in `gui.py`'s `_reposition_roblox`. Every 250ms, it checks if Roblox has forcefully restored its `WS_CAPTION` (title bar) due to injected inputs or focus events. If detected, it instantly strips the style and forces a frame recalculation (`SWP_FRAMECHANGED`), permanently locking the client area perfectly into the container and preventing 30-pixel Y-axis click shifts.

### 6. Container Random Detachment (The Empty Log Gap)
- **Fix**: Rewrote `_poll_for_roblox` in `gui.py`. Replaced an intensive, 3-second full-system `psutil` process scan with a lightning-fast pointer check (`win32gui.IsWindow(self._roblox_hwnd)`). The system only falls back to a full scan if the window is genuinely closed, eliminating random container detachments caused by CPU load spikes throwing `psutil` exceptions.

### 7. 3D Camera First-Person Flick
- **Fix**: Fixed a bug where `camera_move` would "teleport" the mouse to the UI-drawn start coordinates before rotating, causing a massive camera flick in First-Person mode where the cursor is locked to the center. `camera_move` now safely teleports the cursor to exactly `(0.5, 0.5)` (the center of the container) before injecting relative `MOUSEEVENTF_MOVE` deltas.

### 8. 3D Camera Drag Sensitivity
- **Fix**: Implemented a `SENSITIVITY_MULTIPLIER = 3.5` inside `camera_move`. Since Roblox scales raw mouse input down via in-game sensitivity, physical mouse movements matching UI pixels were resulting in partial rotation. The multiplier scales up the `dx_float` and `dy_float` deltas to provide a 1:1 feel between the UI drawing distance and the in-game rotation distance.

### 9. OCR Region Persistent Storage Location
- **Important Note for Scalability**: The user's OCR region coordinates (which define exactly where on the screen the script should look for disconnect messages) are permanently serialized and saved in JSON format.
- **File Location**: `d:\RenzeiMacro\presets\villain_invasion\config.json`
- **Why this matters**: Storing the OCR region persistently in this JSON configuration file ensures the system is completely stateless across reboots. This is critical for the scalability of the program, as it allows the macro to automatically recover and resume automation loops after system crashes or updates without requiring any manual recalibration by the user.

---

## Pending for Next Session
- **Reconnect Logic**: Wire `DisconnectWatchdog` OCR detection to `RelaunchService` to auto-rejoin on disconnect.
- **Full Automation Loop**: Combine the sequence playback + reconnect + loop into a cohesive "Villain Coin Farm" automation state machine.

