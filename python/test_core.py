"""
Unit tests for core modules: container coordinate math, config, cookie store.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# Force UTF-8 output
sys.stdout.reconfigure(encoding="utf-8")

# Track results
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


# ===== Container Coordinate Math ==========================================
print("\n--- Container Coordinate Math ---")

from container import Container, ContainerRect

c = Container()

# Test manual set
c.set_manual(100, 200, 1920, 1080)
test("set_manual valid", c.rect.valid)
test("set_manual x", c.rect.x == 100)
test("set_manual w", c.rect.w == 1920)

# Test normalize
nx, ny = c.normalize_coords(100, 200)
test("normalize top-left → (0, 0)", abs(nx) < 0.001 and abs(ny) < 0.001)

nx, ny = c.normalize_coords(100 + 1920, 200 + 1080)
test("normalize bottom-right → (1, 1)", abs(nx - 1.0) < 0.001 and abs(ny - 1.0) < 0.001)

nx, ny = c.normalize_coords(100 + 960, 200 + 540)
test("normalize center → (0.5, 0.5)", abs(nx - 0.5) < 0.001 and abs(ny - 0.5) < 0.001)

# Test denormalize
ax, ay = c.denormalize_coords(0.5, 0.5)
test("denormalize center → (1060, 740)", ax == 1060 and ay == 740)

ax, ay = c.denormalize_coords(0.0, 0.0)
test("denormalize top-left → (100, 200)", ax == 100 and ay == 200)

# Test round-trip
nx, ny = c.normalize_coords(500, 600)
ax, ay = c.denormalize_coords(nx, ny)
test("round-trip (500,600)", ax == 500 and ay == 600)

# Test is_point_in_container
test("point inside", c.is_point_in_container(500, 600))
test("point outside left", not c.is_point_in_container(50, 600))
test("point outside below", not c.is_point_in_container(500, 2000))

# Test get_capture_region
region = c.get_capture_region(0.3, 0.3, 0.7, 0.7)
test("capture_region left", region["left"] == 100 + round(0.3 * 1920))
test("capture_region width", region["width"] == round(0.7 * 1920) - round(0.3 * 1920))

# Test to_dict / from_dict
d = c.rect.to_dict()
r2 = ContainerRect.from_dict(d)
test("to_dict/from_dict round-trip", r2.x == c.rect.x and r2.w == c.rect.w and r2.valid == c.rect.valid)


# ===== Config ==============================================================
print("\n--- Config ---")

from config import AppConfig

# Use a temp file for config
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir=".") as f:
    temp_config_path = Path(f.name)

try:
    cfg = AppConfig(settings_path=temp_config_path)
    
    test("default keybinds exist", "record" in cfg.keybinds)
    test("default record key is F6", cfg.keybinds["record"] == "F6")
    test("default active_mode is 'default'", cfg.active_mode_name == "default")
    test("default mode has name", cfg.active_mode.get("name") == "Default Mode")
    
    # Test set and save
    cfg.set_keybind("record", "F5")
    cfg.save()
    
    # Reload and verify
    cfg2 = AppConfig(settings_path=temp_config_path)
    test("saved keybind persists", cfg2.keybinds["record"] == "F5")
    
    # Test mode update
    cfg2.set_mode("farming_act6", {"name": "Act 6 Farm", "place_id": "12345", "job_id": "", "reconnect_preset": ""})
    cfg2.active_mode_name = "farming_act6"
    cfg2.save()
    
    cfg3 = AppConfig(settings_path=temp_config_path)
    test("new mode persists", cfg3.active_mode.get("name") == "Act 6 Farm")
    test("place_id persists", cfg3.place_id == "12345")
    
finally:
    temp_config_path.unlink(missing_ok=True)


# ===== Cookie Store (DPAPI) ================================================
print("\n--- Cookie Store (DPAPI round-trip) ---")

from cookie_store import CookieStore

with tempfile.NamedTemporaryFile(suffix=".dat", delete=False, dir=".") as f:
    temp_cookie_path = Path(f.name)

try:
    cs = CookieStore(cookie_path=temp_cookie_path)
    
    test("no cookie initially", not cs.has_cookie())
    
    # Store a test cookie
    test_cookie = "_|WARNING:-DO-NOT-SHARE-THIS.--Sharing-this-will-allow-someone-to-log-in-as-you|" + "A" * 200
    stored = cs.store_cookie(test_cookie)
    test("store_cookie succeeds", stored)
    test("has_cookie after store", cs.has_cookie())
    
    # Retrieve and verify
    retrieved = cs.get_cookie()
    test("retrieved cookie matches original", retrieved == test_cookie)
    
    # Verify encrypted file is NOT plaintext
    with open(temp_cookie_path, "rb") as f:
        raw = f.read()
    test("file is encrypted (not plaintext)", test_cookie.encode() not in raw)
    
    # Test format validation
    test("valid format (long)", cs.validate_cookie_format(test_cookie))
    test("invalid format (short)", not cs.validate_cookie_format("tooshort"))
    
    # Test delete
    cs.delete_cookie()
    test("cookie deleted", not cs.has_cookie())
    
finally:
    temp_cookie_path.unlink(missing_ok=True)


# ===== IPC Message Format ==================================================
print("\n--- IPC Message Serialization ---")

# Test that the IPC server can serialize/deserialize messages
from ipc_server import IPCServer

server = IPCServer()

# Register a test handler
server.register_handler("test", lambda q: {"echo": q.get("data", "")})

# Test dispatch
result = server._dispatch({"cmd": "test", "data": "hello"})
test("dispatch returns echo", result.get("echo") == "hello")

result = server._dispatch({"cmd": "unknown_cmd"})
test("unknown cmd returns error", "error" in result)


# ===== Summary =============================================================
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
if failed == 0:
    print("ALL TESTS PASSED ✓")
else:
    print(f"SOME TESTS FAILED ✗")
    sys.exit(1)
