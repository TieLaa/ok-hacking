"""按 PID 截一个窗口，保存 png（不需要游戏，也不依赖 PowerShell 的 DPI 行为）。

用法：
    .venv\\Scripts\\python.exe tools\\win_shot.py <pid> <out.png>

实现：
- 先把窗口 SW_RESTORE 并置前（Qt 的 PrintWindow 会返回全黑，只能抓屏幕）；
- 用桌面 DC 的 BitBlt 抓窗口矩形（物理像素，本进程已 DPI-aware）。
"""

import ctypes
import sys
import time

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process
import win32ui

ctypes.windll.shcore.SetProcessDpiAwareness(2)


def find_hwnd(pid):
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid == pid:
                found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    if not found:
        return 0
    return max(found, key=lambda h: (lambda r: (r[2] - r[0]) * (r[3] - r[1]))(win32gui.GetWindowRect(h)))


def grab(rect, out):
    left, top, right, bottom = rect
    w, h = right - left, bottom - top
    hwnd_dc = win32gui.GetDC(0)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    save_dc.BitBlt((0, 0), (w, h), mfc_dc, (left, top), win32con.SRCCOPY)
    arr = np.frombuffer(bmp.GetBitmapBits(True), dtype=np.uint8).reshape(h, w, 4)
    cv2.imwrite(out, arr[:, :, :3])
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(0, hwnd_dc)
    return w, h


if __name__ == "__main__":
    pid, out = int(sys.argv[1]), sys.argv[2]
    hwnd = find_hwnd(pid)
    if not hwnd:
        raise SystemExit("找不到窗口")
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(0.03)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.5)
    rect = win32gui.GetWindowRect(hwnd)
    print("hwnd", hwnd, "rect", rect, "size", grab(rect, out), "->", out,
          "fg:", win32gui.GetForegroundWindow() == hwnd)
