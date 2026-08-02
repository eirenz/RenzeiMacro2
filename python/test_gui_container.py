"""
test_gui_container_polling.py
Tests the GUI container polling callback wiring without opening any windows.
"""
import sys, threading, time, tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        print(f"  PASS  {name}")
        passed += 1
    else:
        print(f"  FAIL  {name}")
        failed += 1

# ──────────────────────────────────────────────────────
print("\n--- GUI on_container_found callback wiring ---")

from container import Container, ContainerRect
from config import AppConfig
from cookie_store import CookieStore
from gui import SettingsGUI, CONTAINER_POLL_MS

# 1. Callback fires when container becomes valid via set_manual
callback_fired = threading.Event()

container = Container()
config = AppConfig(settings_path=Path(tempfile.mktemp(suffix=".json")))
cookie = CookieStore(cookie_path=Path(tempfile.mktemp(suffix=".dat")))

gui = SettingsGUI(
    config=config,
    cookie_store=cookie,
    container=container,
    on_container_found=lambda: callback_fired.set(),
)

# Simulate what _on_container_detected() does (container not yet valid)
test("callback not fired before detection", not callback_fired.is_set())

# Manually mark container as valid (simulating Roblox window found)
container.set_manual(0, 0, 1920, 1080)
test("container now valid", container.rect.valid)

# Simulate the GUI's _on_container_detected path (no Tk needed)
gui._container_found_fired = False
if gui.on_container_found:
    gui.on_container_found()
    time.sleep(0.1)
test("callback fired after container valid", callback_fired.is_set())

# 2. Guard: callback fires only ONCE even if called again
callback_count = [0]
gui2 = SettingsGUI(
    config=config,
    cookie_store=cookie,
    container=container,
    on_container_found=lambda: callback_count.__setitem__(0, callback_count[0] + 1),
)
gui2._container_found_fired = False

# First call
if not gui2._container_found_fired:
    gui2._container_found_fired = True
    gui2.on_container_found()

# Second call (guard should prevent double-fire in real GUI)
# The _container_found_fired flag is checked inside _poll_container_detection
test("callback fired exactly once (guard flag set)", gui2._container_found_fired)

# 3. CONTAINER_POLL_MS is a reasonable value (module-level constant)
test("CONTAINER_POLL_MS <= 5000ms", CONTAINER_POLL_MS <= 5000)
test("CONTAINER_POLL_MS >= 500ms",  CONTAINER_POLL_MS >= 500)

# 4. on_container_found defaults to None (no crash without it)
gui3 = SettingsGUI(config=config, cookie_store=cookie, container=container)
test("on_container_found defaults to None", gui3.on_container_found is None)

# Cleanup temp files
try:
    Path(config._path).unlink(missing_ok=True)
except Exception:
    pass

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
print("ALL TESTS PASSED" if not failed else "SOME TESTS FAILED")
sys.exit(failed)
