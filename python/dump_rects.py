import win32gui
import json

def get_rects():
    data = {}
    
    hwnd_macro = win32gui.FindWindow(None, "RenzeiMacro")
    if hwnd_macro:
        rect = win32gui.GetWindowRect(hwnd_macro)
        data["RenzeiMacro"] = rect
        
    hwnd_cookie = win32gui.FindWindow(None, "Roblox Cookie Manager")
    if hwnd_cookie:
        rect = win32gui.GetWindowRect(hwnd_cookie)
        data["CookieManager"] = rect
        
    hwnd_roblox = win32gui.FindWindow(None, "Roblox")
    if hwnd_roblox:
        rect = win32gui.GetWindowRect(hwnd_roblox)
        data["Roblox"] = rect
        
    print(json.dumps(data, indent=2))

get_rects()
