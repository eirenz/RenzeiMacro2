# Research: OCR-Driven Roblox Macro Tool — Competitive Landscape & Technical Approach

## 1. Competitor landscape

### Tier 1 — Simple record/playback (no vision, no logic)
- **TinyTask** — tiny (~30KB), records raw mouse/keyboard events and coordinates, plays them back. No conditional logic, no image/OCR awareness, coordinates are absolute pixels (breaks on any resolution change or window move). This is the baseline gamers already know.
- **OP Auto Clicker / Mini Mouse Macro / GS Auto Clicker** — single-purpose clickers, no recording of complex sequences, no vision.

### Tier 2 — Scriptable, coordinate-based
- **AutoHotkey (AHK v2)** — the most common tool mentioned alongside TinyTask. Lets you click specific coordinates, read pixel colors (`PixelGetColor`, `PixelSearch`, `ImageSearch`), check window titles, and branch with conditionals. No OCR built-in, no true "vision" — image search is template matching only, and its scaling/DPI handling is manual and brittle.
- **Pulover's Macro Creator** — a GUI layer over AHK; records actions like TinyTask but exposes an editable list of steps, loops, and conditions without writing code. Effectively "TinyTask with an edit button." Still coordinate/pixel-based, no OCR.
- **Jitbit Macro Recorder / Macro Toolworks** — commercial, similar niche to Pulover's; some paid tiers mention OCR as an upsell feature but it's shallow (template-based, not text-reading).

### Tier 3 — Vision/OCR-based automation
- **SikuliX** — the closest existing analog to what's being built. Uses OpenCV for image-recognition-based clicking (find an image on screen, click it) and Tesseract for OCR (read text off screen, act on it). Runs on a JVM (Jython), which is why it never got fast/native gameplay adoption — it's built for GUI test automation, not real-time gaming loops. Known weaknesses: image-recognition scripts are slow relative to native calls, brittle to UI/resolution changes (screenshots must be recaptured per resolution/theme), and debugging is hard because failures are visual, not exception-based.
- **Lackey** — a Python port of Sikuli's core ideas (screen search, no OCR at last check), alpha-quality, Windows-only despite "cross-platform" goals.

### What's missing across ALL of the above (the gap this project fills)
1. **No existing tool defines a "container" concept** — a bounded sub-region of the screen that all detection is scoped to, decoupled from absolute screen coordinates, so the same macro works at 1080p, 1440p, ultrawide, or a windowed Roblox client at any position.
2. **No existing tool auto-detects 2D vs 3D camera mode** and switches mouse-movement math accordingly. AHK/TinyTask/SikuliX all just replay raw deltas or absolute coordinates — none reason about "is the game currently in a first-person/camera-rotation state."
3. **OCR is either absent (TinyTask/AHK) or slow and JVM-bound (SikuliX)** — nothing combines a fast native input layer (AHK) with a modern Python OCR/vision stack (Tesseract, EasyOCR, or template matching via OpenCV) through a lightweight IPC bridge.
4. **Auto-reconnect for a *game* (Roblox specifically) isn't a feature of any generic macro tool** — it requires knowing what a "disconnected" state looks like inside the container, and knowing how to get back into the game unattended, neither of which any generic macro tool addresses.

This is the actual differentiation: **container-scoped OCR/vision + a native AHK input layer + automatic 2D/3D mouse-mode switching + a full unattended reconnect pipeline**, purpose-built for Roblox, rather than a generic desktop automation tool retrofitted for gaming.

## 2. Core technical problems and proposed solutions

### Problem A — Resolution/device independence
Absolute pixel coordinates (what TinyTask/AHK use by default) break the instant resolution, DPI scaling, or window position changes.
**Solution:** Define a "container" = the Roblox client window (or a calibrated sub-rectangle inside it). All recorded actions and all vision/OCR scans store *normalized* coordinates (0.0–1.0 fractions of container width/height), not absolute pixels. At playback time, the container's current on-screen rectangle is auto-detected via window handle + client-area bounds, and normalized coordinates are re-projected to the current resolution/position. Scanning is clipped to the container rect only.

### Problem B — Automatic 2D vs 3D mouse-movement switching
The user wants precise, direct (2D/absolute) mouse movement most of the time, but Roblox camera-rotation (holding right-click and dragging, or first-person/zoomed-in mode) needs relative/delta-based "3D" movement to match how the game reads mouse input during camera control.
**Solution:** Detect camera-rotation mode using a **native OS-level signal rather than vision** — Roblox confines and/or hides the OS cursor during free-look and first-person camera control, which AHK can query directly via the Windows API (`GetCursorInfo` for visibility, `GetClipCursor` for confinement). This is far more reliable than OCR/visual detection since it doesn't depend on rendering, lighting, UI scaling, or font rendering. Combine with right-mouse-button-held state (RMB down + cursor-lock confirmed → 3D mode). While active, mouse movement switches from absolute `MouseMove x,y` to relative delta injection. The moment RMB is released and/or the cursor unlocks, it switches back to absolute movement — fully automatic, state-machine driven: `IDLE_2D → RMB_DOWN+CURSOR_LOCK_CONFIRMED → MODE_3D → RMB_UP → IDLE_2D`. Vision/OCR is reserved only as a secondary fallback if the cursor-lock signal is ever inconclusive.

### Problem C — OCR/vision performance for real-time gaming loops
Sikuli's JVM/Tesseract pipeline is too slow for tight gameplay loops.
**Solution:** Split responsibilities: AHK handles all timing-critical input (clicks, key presses, mouse deltas) since it's a lightweight native Windows automation layer with no runtime startup cost per action. A separate Python process handles vision — screen capture (via `mss` or Windows Graphics Capture for speed) restricted to the container rect only, using OpenCV template matching for known UI elements (fast, no text needed) and Tesseract/EasyOCR only when actual text needs to be read. The two processes communicate over a named pipe/local socket so AHK can ask "is X visible in the container right now?" and get a fast structured response back, without needing a full OCR pass every tick.

### Problem D — Auto-reconnect (detection + unattended relaunch + resume)
Requires detecting Roblox's own disconnect dialog, getting back into the game **without user interaction and without a browser**, and then resuming gameplay at the correct in-game location — three separate problems, none of which any generic macro tool solves.
**Solution:** covered in full in section 4 below (detection) and section 5 (browserless relaunch) and section 6 (resume via reconnect presets).

## 3. Proposed architecture (high level)
- **AHK layer (input + orchestration):** hotkey handling, recording of raw input events, macro playback engine, mouse-mode state machine (2D/3D via cursor-lock API calls), talks to the Python vision service over a named pipe/local socket.
- **Python layer (vision/OCR):** screen capture scoped to the container, OpenCV template matching, Tesseract/EasyOCR text reads (used sparingly), container calibration.
- **Container calibration:** auto-detected from the Roblox window's client area; stored relative to that window, not absolute screen pixels — this is what makes it portable across devices/resolutions.
- **Storage:** recordings saved as JSON (normalized coordinates + key events + timing), loadable/re-playable on any device once the container is auto-recalibrated.
- **Must-have baseline features:** custom keybinds (start/stop/play, independent of focus), save/load of recordings, full auto-reconnect pipeline.

## 4. Disconnect detection
A low-frequency background poll (every 1–2s, not every frame) watches the **center-screen region of the container** — where Roblox's disconnect/error dialogs appear — for the presence of a disconnect dialog. This build uses **presence-only detection**: any detected center-screen disconnect dialog triggers the reconnect flow the same way, with no parsing of the dialog's text or branching by disconnect reason (that's a possible future enhancement, not in scope now).

## 5. Auto-reconnect: browserless (no-browser) relaunch research
Community "Roblox account manager" tools (e.g. Roblox Account Manager / RAM, and browser-launch bootstrapper Bloxstrap) confirm the mechanism the user described is well-documented and technically established:

- The normal browser join flow works by navigating to a `roblox-player:1+launchmode:play+...` protocol URI, which the OS hands off to `RobloxPlayerLauncher.exe`, which in turn launches `RobloxPlayerBeta.exe` with the parsed launch arguments (place/job IDs, locale, etc.).
- Account-manager tools skip the browser entirely by obtaining an **authentication ticket** from Roblox's own API (using the account's stored `.ROBLOSECURITY` session cookie + a CSRF token) and launching `RobloxPlayerBeta.exe` directly with that ticket and the target place/job ID — this is exactly the "join without opening a browser" behavior wanted here.
- This requires storing the account's session cookie locally to allow unattended relaunch. Every source on this (Roblox's own wiki, third-party guides, and RAM's own README) is consistent on the risk: the `.ROBLOSECURITY` cookie is equivalent to a password — anyone who obtains it can fully access the account. RAM's own docs describe storing it "encrypted and stored locally" and never transmitting it anywhere — that's the standard of care to match.
- **Decision for this build:** always use the cookie-based relaunch directly on disconnect — do not attempt to click Roblox's own in-game "Rejoin" button first. Store the cookie encrypted via **Windows DPAPI** (tied to the Windows user account, no separate master password needed) — this is the lowest-friction option that still meets the "treat it like a password manager secret" standard of care: local encryption at rest, no network transmission except to Roblox's own auth endpoints, no plaintext logging.
- Practical implication: this feature is a **security- and account-risk-bearing feature**, not just an engineering one — unattended auto-login/relaunch carries inherent account risk per Roblox's own terms, independent of the macro/automation aspect.

## 6. Reconnect macro presets tied to macro "modes"
No existing competitor (TinyTask/AHK/Pulover's/SikuliX) has any concept of a "mode" — they are single-recording tools. This is a genuinely new structure for this project:

- A **Macro Mode** = a named profile representing what the user is currently farming (a specific act, gamemode, or challenge). The mode list is fixed/user-defined later.
- Each Mode maps 1:1 to its own **Reconnect Preset recording** — a separate, shorter recording whose only job is to navigate back to the correct in-game spot after a fresh rejoin.
- On reconnect, once the game is confirmed loaded back in (via a vision check, not a fixed timer), the app looks up the **currently active Mode** and plays *that Mode's* Reconnect Preset — not a single global preset.
- **Starting scope: build exactly one Mode with one Reconnect Preset.** The data structure (Mode → Reconnect Preset) should still be built as a list/map from day one so adding more Modes later doesn't require restructuring — but no multi-mode UI/management logic in this initial version.

## 7. Open technical risks to flag for the IDE / implementation
- Roblox's anti-cheat/anti-automation stance — this needs to be acknowledged as something the user is responsible for; the focus here is the technical automation architecture, not evading detection.
- The `.ROBLOSECURITY` cookie handling is a genuine account-security risk, independent of automation — treat storage/handling with the same care as a password manager secret.
- OCR accuracy on Roblox's often-stylized UI fonts may be lower than on plain desktop text; template matching should carry more weight than text OCR for in-game elements.
- Windows Graphics Capture vs simple screenshot APIs for capture speed/latency tradeoffs.
- AHK↔Python IPC latency budget needs to stay well under a single game frame (~16ms at 60fps) for the 3D mouse-mode switch to feel responsive.
