# AI IDE Build Prompt — Container-Scoped OCR Macro for Roblox (AHK + Python)

## Context for the AI IDE
You are building a Windows desktop macro/automation tool specifically for Roblox. It is a **new, standalone project** (not related to any prior C#/.NET macro attempt). It combines **AutoHotkey v2** for native input handling/hotkeys/playback and **Python** for OCR/computer-vision, communicating over a local named pipe. Read this entire document before writing any code — it defines required behavior, not just features to bolt on separately. A companion research file (`research-macro-ocr.md`) is provided alongside this prompt with competitive analysis and rationale; treat it as background, not additional requirements.

## Non-negotiable design principle: the "Container"
Every other feature below depends on this. Do not implement absolute-screen-coordinate recording (that is what TinyTask/basic AHK scripts already do, and it's exactly what we're improving on).

- The **Container** = the Roblox client's window client-area, or a user/auto-calibrated sub-rectangle within it.
- On startup (or first run per device), the app must **auto-detect the Roblox window** (by process name/window class/title) and derive the Container's rectangle from its client area — not the full screen, not including window chrome.
- ALL recorded actions (clicks, mouse paths) and ALL vision/OCR scans must store and operate on **normalized coordinates** (0.0–1.0 fractions of Container width/height), never absolute screen pixels.
- At playback time, re-detect the Container's current on-screen rectangle (resolution, window position, and monitor may differ from recording time) and re-project normalized coordinates into current absolute pixels.
- Vision/OCR must never scan outside the Container rectangle.
- Provide a manual override/recalibration option in the UI in case auto-detection of the window fails (e.g., borderless fullscreen edge cases), but auto-detection is the default, expected path.

## Automatic 2D ↔ 3D mouse movement switching
This must be fully automatic — no manual toggle exposed to the end user during playback.

- **Default state:** precise, absolute (2D) mouse movement for all clicks/menu navigation.
- **Trigger into 3D/camera mode:** right mouse button held down **and** a native OS-level confirmation that the cursor is locked/hidden by the game (Roblox confines and/or hides the OS cursor during free-look and first-person camera control). Query this via the Windows API directly from AHK — `GetCursorInfo` (cursor visibility/shown state) and/or `GetClipCursor` (whether the cursor is confined to a region) — rather than any visual/OCR check. This is a system-level signal, not a rendering-dependent one, so it doesn't fail due to lighting, UI scaling, font rendering, or occlusion the way OCR/visual detection can. Both signals should be combined (RMB-held is necessary but not sufficient by itself; treat the cursor-lock state as confirmation).
- **While in 3D mode:** mouse movement must be injected as relative deltas (mirroring how Roblox reads camera-drag input), not absolute coordinates.
- **Trigger back to 2D mode:** right mouse button released and/or the OS reports the cursor is no longer locked/hidden — switch immediately back to absolute movement.
- Implement this as an explicit state machine: `IDLE_2D → RMB_DOWN+CURSOR_LOCK_CONFIRMED → MODE_3D → RMB_UP → IDLE_2D`. Make states/transitions inspectable/loggable for debugging, since this is the most novel and highest-risk part of the system.
- **Fallback for zoom-triggered first-person without RMB held:** if Roblox can enter a locked-camera first-person state without RMB being held (e.g. scroll-to-zoom into FPS), the cursor-lock signal above should still catch it on its own, since it's independent of RMB. Reserve OCR/visual template matching as a secondary fallback only, for cases where the cursor-lock API signal is inconclusive — it should not be the primary or sole signal for this detection.

## OCR / vision architecture
- Python process owns all screen capture and recognition, scoped strictly to the Container rectangle (smaller capture region = faster processing).
- Use a fast capture method (e.g. `mss` or Windows Graphics Capture) — avoid slow, full-screen capture calls.
- Use OpenCV template matching for known static UI elements (fast, no text parsing needed).
- Use OCR (Tesseract or EasyOCR — pick one and justify the choice in your implementation notes) only when actual text must be read (e.g. disconnect dialogs, dynamic labels).
- Expose vision results to AHK over the named pipe/local socket as simple query/response: AHK asks "is X visible in the Container right now?" (or "read text in region Y") and gets back a fast structured response (found/not-found + normalized coordinates, or text string).
- Keep the AHK↔Python round trip well under one game frame (~16ms @ 60fps) as a target budget for anything in the 3D-mode detection path; slower, low-frequency polling (every 1–2s) is acceptable for things like the disconnect watchdog.

## Auto-reconnect
Full flow: **detect → relaunch (browserless, cookie-based) → resume via a reconnect macro preset tied to the active macro mode.**

### Detection
- A low-frequency background poll (not every frame, e.g. every 1–2s) watches the **center-screen region of the Container only** (where Roblox's disconnect/error dialogs appear) for the presence of a disconnect dialog.
- **Presence-only detection for now:** do not read/parse the dialog's text content or branch behavior by disconnect reason. Any detected center-screen disconnect dialog triggers the same reconnect flow. (Text-based branching by reason — kicked, server full, connection lost, etc. — is a possible future enhancement, not part of this build.)

### Relaunch (always cookie-based, no native "Rejoin" click)
- Do **not** attempt to click Roblox's own in-game "Rejoin" button. Always use the cookie-based browserless relaunch path directly, every time a disconnect is detected.
- Store the user's `.ROBLOSECURITY` session cookie locally, encrypted at rest using **Windows DPAPI** (`CryptProtectData`/`CryptUnprotectData`), scoped to the current Windows user account. Never transmit the cookie anywhere off-device, never log it in plaintext, never display it in the UI after initial entry.
- On trigger: use the stored cookie to call Roblox's authenticated join/ticket API for the last known place/server, then launch the Roblox client directly via the `roblox-player://` protocol handler with that ticket — no browser window involved at any point.
- This mirrors the standard technique used by existing Roblox multi-account manager tools (see `research-macro-ocr.md`); the only sensitive part is cookie handling, covered above.

### Resume via reconnect macro presets
- Once the game has visibly loaded back in (confirmed via a vision check on the Container — not a fixed timer), play a **reconnect macro preset**: a separate saved recording whose job is to navigate the character back to its farming spot/activity after a fresh join (e.g. walking from spawn back to a grind location).
- Reconnect presets are mapped **1:1 to "macro modes"** (e.g. a specific Act, gamemode, or challenge) — the user selects which macro mode they're running, and the matching reconnect preset plays automatically after any relaunch during that session. The mode list itself is user-defined/fixed by the user later; the app just needs a mode → reconnect-preset mapping structure.
- **Build only one macro mode and one reconnect preset for this initial version.** Design the mapping/storage structure (mode identifier → reconnect preset file) so additional modes/presets can be added later without rearchitecting — but do not build UI or logic for managing multiple modes yet.

## Baseline must-have features
- **Custom keybinds** for record/stop/play, independent of which window has focus (global hotkeys via AHK).
- **Save/load of recordings** as a structured format (e.g. JSON) containing normalized coordinates, key events, timing, and any vision anchor references needed for playback — must remain valid across devices once the Container is (re-)calibrated. Reconnect macro presets use this same recording format.
- **Auto-reconnect (detect → cookie-based relaunch → reconnect preset playback)** as described above.

## Explicit non-goals / things to avoid
- Do not implement anti-detection or anti-cheat evasion features. Focus purely on the automation/vision/input architecture described here; how the user chooses to use it is their responsibility.
- Do not fall back to absolute-screen-pixel coordinates anywhere in the recording/playback pipeline — this defeats the entire point of the Container system.
- Do not require a manual 2D/3D toggle during playback; the switching must be automatic per the state machine above.
- Do not store, log, or transmit the `.ROBLOSECURITY` cookie in plaintext anywhere, and never send it to any destination other than Roblox's own official API endpoints.

## Suggested build order
1. Container detection + calibration (window handle → client-area rect → normalized coordinate math), with manual override UI.
2. AHK input layer: global hotkeys, raw recording (mouse/keyboard events + timestamps) in normalized coordinates, JSON save/load.
3. Python vision service: Container-scoped capture, template matching, OCR, exposed over named pipe.
4. 2D/3D mouse-mode state machine wired into the playback engine, using RMB state + OS-level cursor-lock detection (Windows API calls from AHK), with vision as a secondary fallback only.
5. Auto-reconnect: center-screen disconnect detection (vision), DPAPI-encrypted cookie storage, cookie-based browserless relaunch via `roblox-player://`, and a single reconnect macro preset played back once the game reloads (extensible mode→preset mapping, but only one mode/preset built now).
6. Integration pass: end-to-end test across at least two different resolutions/window positions to confirm Container portability actually works.

## Reference material
See `research-macro-ocr.md` (provided alongside this prompt) for the competitive landscape (TinyTask, AutoHotkey, Pulover's Macro Creator, SikuliX/Lackey) and the reasoning behind each architectural decision above.
