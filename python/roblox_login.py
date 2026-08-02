"""
roblox_login.py - Automated Cookie Extractor
============================================
Opens a WebView2 window to the Roblox login page.
Polls the browser's cookies until the .ROBLOSECURITY cookie is found,
then automatically saves it to the CookieStore and closes the window.
"""

import sys
import time
import threading
import logging
from pathlib import Path

# Ensure we can import cookie_store
sys.path.insert(0, str(Path(__file__).parent))

import webview
from cookie_store import CookieStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RobloxLogin")

def check_cookies(window, cookie_store):
    """Polls the webview cookies in the background."""
    # Give the window a moment to initialize
    time.sleep(2)
    
    while True:
        try:
            # get_cookies() returns a list of http.cookiejar.Cookie objects
            cookies = window.get_cookies()
            for c in cookies:
                # `cookies` could be a list of `http.cookies.SimpleCookie` objects or raw dicts depending on backend.
                if isinstance(c, dict) and 'name' in c and 'value' in c:
                    # Raw dict (some pywebview backends)
                    name = c.get('name')
                    value = c.get('value')
                else:
                    # SimpleCookie object (WebView2 backend on Windows)
                    # Acts like a dictionary mapping cookie name -> Morsel object
                    for key, morsel in getattr(c, "items", lambda: [])():
                        name = key
                        value = getattr(morsel, "value", str(morsel))
                        
                        if name == ".ROBLOSECURITY":
                            break
                
                if name == ".ROBLOSECURITY":
                    logger.info("Found .ROBLOSECURITY cookie!")
                    if cookie_store.store_cookie(value):
                        print("SUCCESS", flush=True)
                        window.destroy()
                        return
                    else:
                        print("ERROR_ENCRYPT", flush=True)
                        window.destroy()
                        return
        except Exception as e:
            # Exceptions might happen if the window is closed or not ready
            pass
            
        time.sleep(1)

def main():
    cookie_store = CookieStore()
    
    # Default to 50, 50 if no args provided, otherwise parse from command line
    x_pos = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    y_pos = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    
    # Create the window
    window = webview.create_window(
        title="RenzeiMacro | Roblox Login (Cookie will be automatically grabbed)",
        url="https://www.roblox.com/login",
        width=330,
        height=650,
        resizable=False,
        x=x_pos,
        y=y_pos,
        on_top=True
    )
    
    # Start the polling thread
    t = threading.Thread(target=check_cookies, args=(window, cookie_store), daemon=True)
    t.start()
    
    # Blocks until the window is destroyed
    webview.start(private_mode=False)

if __name__ == "__main__":
    main()
