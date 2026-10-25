"""按窗口标题点一个客户区坐标（真实鼠标），可点完把游戏切回前台。

用法：
    python tools/click_window.py "OK dev" 1088 89            # 点 ok-script 的「开始」
    python tools/click_window.py "OK dev" 1088 89 --restore  # 点完把游戏切回前台

为什么单独写：ok-script 的 Qt 窗口对 PostMessage 时灵时不灵，真实点击才稳；
点完必须把游戏切回前台，否则 ok-script 会认为窗口不可见而暂停执行器。
"""
import ctypes
import sys
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui

user32 = ctypes.windll.user32
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def find_window(title_part):
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and title_part in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    if not found:
        return 0
    return max(found, key=lambda h: (lambda l, t, r, b: (r - l) * (b - t))(*win32gui.GetWindowRect(h)))


def force_foreground(hwnd):
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    win32api.keybd_event(VK_MENU, 0, 0, 0)
    time.sleep(0.01)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    finally:
        win32api.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.35)
    return win32gui.GetForegroundWindow() == hwnd


def game_hwnd():
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "篮球少女" in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return max(found, key=lambda h: (lambda l, t, r, b: (r - l) * (b - t))(*win32gui.GetWindowRect(h))) if found else 0


def main():
    title = sys.argv[1]
    x, y = int(sys.argv[2]), int(sys.argv[3])
    restore = "--restore" in sys.argv

    hwnd = find_window(title)
    if not hwnd:
        raise SystemExit(f"找不到窗口: {title}")
    force_foreground(hwnd)
    sx, sy = win32gui.ClientToScreen(hwnd, (x, y))
    win32api.SetCursorPos((sx, sy))
    time.sleep(0.2)
    win32api.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.12)
    win32api.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    print(f"clicked {title} at ({x},{y}) screen=({sx},{sy})")

    if restore:
        time.sleep(0.4)
        g = game_hwnd()
        if g:
            print("game foreground:", force_foreground(g))


if __name__ == "__main__":
    main()
