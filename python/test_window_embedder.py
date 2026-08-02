"""
test_window_embedder.py
Tests for WindowEmbedder state-machine logic (no real Win32 calls).
"""
import sys
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

print("\n--- WindowEmbedder state machine ---")

from window_embedder import WindowEmbedder

# 1. Initial state
e = WindowEmbedder()
test("not embedded on init",    not e.is_embedded)
test("target hwnd 0 on init",   e._target_hwnd == 0)
test("is_target_alive False when not set", not e.is_target_alive())

# 2. release() on un-embedded instance is a no-op
result = e.release()
test("release() on clean instance returns True", result is True)
test("still not embedded after no-op release",   not e.is_embedded)

# 3. embed() with invalid HWNDs returns False without crashing
result = e.embed(0, 0, 800, 600)
test("embed(0,0,...) returns False",  result is False)
test("not embedded after failed embed", not e.is_embedded)

result2 = e.embed(0, 12345, 800, 600)
test("embed(0, valid_host,...) returns False", result2 is False)

# 4. resize() with no embedded window is a no-op (no exception)
try:
    e.resize(1920, 1080)
    test("resize() with nothing embedded does not raise", True)
except Exception as ex:
    test(f"resize() with nothing embedded does not raise ({ex})", False)

# 5. Simulate embedded state manually and verify release resets it
e2 = WindowEmbedder()
e2.is_embedded = True
e2._target_hwnd = 99999       # fake hwnd that doesn't exist
e2._original_style   = 0x00CF0000
e2._original_exstyle = 0
e2._original_parent  = 0

# release() will call Win32 on the fake hwnd — it'll fail silently
# We just care that is_embedded is reset and it doesn't crash the process
try:
    e2.release()
    # May or may not succeed on a fake HWND, but should not raise
    test("release() on fake hwnd does not raise", True)
except Exception as ex:
    test(f"release() on fake hwnd does not raise ({ex})", False)

# 6. is_target_alive() returns False for non-existent HWND
e3 = WindowEmbedder()
e3._target_hwnd = 99999
test("is_target_alive() False for fake hwnd", not e3.is_target_alive())

# 7. Constants from the module are correct types
import window_embedder as we
test("GWL_STYLE is int",      isinstance(we.GWL_STYLE, int))
test("WS_CHILD is int",       isinstance(we.WS_CHILD, int))
test("WS_CHILD bit correct",  we.WS_CHILD == 0x40000000)
test("WS_VISIBLE bit correct",we.WS_VISIBLE == 0x10000000)

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
print("ALL TESTS PASSED" if not failed else "SOME TESTS FAILED")
sys.exit(failed)
