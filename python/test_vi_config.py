"""
test_vi_config.py — Unit tests for VillainInvasionConfig and URL parser
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from villain_invasion.vi_config import (
    VillainInvasionConfig,
    OcrRegion,
    parse_private_server_url,
)

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"


def run_tests():
    passed = failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            print(f"{PASS}  {name}")
            passed += 1
        else:
            print(f"{FAIL}  {name}")
            failed += 1

    print("\n--- VillainInvasionConfig & URL parser ---")

    # URL parser — full private server link
    r = parse_private_server_url(
        "https://www.roblox.com/games/12345678/Test-Game"
        "?privateServerLinkCode=AbCdEfGhIjKlMnOpQrStUv"
    )
    check("place_id parsed", r["place_id"] == "12345678")
    check("link_code parsed", r["link_code"] == "AbCdEfGhIjKlMnOpQrStUv")
    check("no error on valid URL", r["error"] == "")

    # URL parser — public server link (no linkCode)
    r2 = parse_private_server_url("https://www.roblox.com/games/99999/Name")
    check("place_id from public link", r2["place_id"] == "99999")
    check("warning on missing linkCode", r2["error"] != "")

    # URL parser — share link (roblox.com/share?code=...)
    r_share = parse_private_server_url(
        "https://www.roblox.com/share?code=sample_share_code_1234567890&type=Server",
        resolve_redirects=False,
    )
    check("share link_code parsed", r_share["link_code"] == "sample_share_code_1234567890")
    check("no error on valid share URL", r_share["error"] == "")

    # URL parser — empty
    r3 = parse_private_server_url("")
    check("error on empty URL", r3["error"] != "")

    # OcrRegion round-trip
    region = OcrRegion(nx=0.1, ny=0.35, nw=0.8, nh=0.3)
    d = region.to_dict()
    region2 = OcrRegion.from_dict(d)
    check("OcrRegion.to_dict nx", d["nx"] == 0.1)
    check("OcrRegion.from_dict round-trip nw", region2.nw == 0.8)

    # Save / load round-trip
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "config.json")
        cfg = VillainInvasionConfig(config_path=cfg_path)
        cfg.set_private_server_url(
            "https://www.roblox.com/games/11111111/X?privateServerLinkCode=CodeABC"
        )
        cfg.ocr_region = OcrRegion(nx=0.2, ny=0.4, nw=0.6, nh=0.2)
        cfg.reconnect_preset = "presets/reconnect.json"
        ok = cfg.save()
        check("save returns True", ok)
        check("config.json exists", os.path.exists(cfg_path))

        # Load and verify
        cfg2 = VillainInvasionConfig.load(cfg_path)
        check("place_id persists", cfg2.place_id == "11111111")
        check("link_code persists", cfg2.link_code == "CodeABC")
        check("ocr_region.nx persists", cfg2.ocr_region.nx == 0.2)
        check("ocr_region.nh persists", cfg2.ocr_region.nh == 0.2)
        check("reconnect_preset persists", cfg2.reconnect_preset == "presets/reconnect.json")

    # has_server / has_ocr_region
    cfg3 = VillainInvasionConfig()
    check("has_server False when no URL", not cfg3.has_server())
    cfg3.set_private_server_url(
        "https://www.roblox.com/games/55555/Y?privateServerLinkCode=ZZZ"
    )
    check("has_server True after URL set", cfg3.has_server())

    # set_ocr_region saves immediately (no file here — just attribute check)
    # We use a tmp dir config for this
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "cfg.json")
        cfg4 = VillainInvasionConfig(config_path=p)
        cfg4.set_ocr_region(0.1, 0.2, 0.5, 0.4)
        check("ocr_region set", cfg4.ocr_region.nx == 0.1)
        check("ocr_region file written immediately", os.path.exists(p))

    print("\n" + "=" * 50)
    total = passed + failed
    print(f"Results: {passed} passed, {failed} failed, {total} total")
    if failed == 0:
        print("ALL TESTS PASSED")
    return failed == 0


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
