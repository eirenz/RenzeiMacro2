import tkinter as tk
import win32gui
import win32con
import subprocess
import time

root = tk.Tk()
root.geometry('800x600+100+100')
cv = tk.Canvas(root, bg='red')
cv.pack(fill='both', expand=True)
root.update()

p = subprocess.Popen(['notepad.exe'])
time.sleep(1)

hwnd = win32gui.FindWindow("Notepad", None)
if hwnd:
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height(), win32con.SWP_SHOWWINDOW)
    time.sleep(0.5)
    root.update()
    print("Viewable:", cv.winfo_viewable())
    p.terminate()
else:
    print("Notepad not found")

root.destroy()
