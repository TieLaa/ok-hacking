"""绝对坐标鼠标：一次 mouse_event 到位，不滑行、不用 SetCursorPos。

这是 `src/interaction/GirlsInteraction._move_to()` 的工具版，行为完全一致：

1. 只发 `mouse_event(MOVE|ABSOLUTE|VIRTUALDESK)`，坐标按**整个虚拟桌面**归一化到 0~65535；
2. 不用 `SetCursorPos`：它不产生 raw input，游戏感知不到，还会让系统光标和
   游戏内部光标错位；
3. 不做相对位移滑行：跨屏几十上百步又慢又容易累积误差（实训：目标 4450 只到 4308）。

所有 tools/*.py 都必须走这里，不要再自己写 glide / SetCursorPos。
"""

import ctypes
import time

import win32api
import win32gui

try:
    from src.util.win_focus import force_foreground, restore_if_minimized
except ImportError:  # 直接 `python tools/xxx.py` 跑时 sys.path[0] 是 tools/
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))
    from src.util.win_focus import force_foreground, restore_if_minimized

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

GAME_TITLE = "篮球少女：飞跃巅峰"
GAME_CLASS = "UnityWndClass"

CURSOR_SETTLE = 0.04      # 绝对定位后等引擎采样
CLICK_HOLD = 0.12         # 按住时间；低于 0.10 会被游戏丢弃


def set_dpi_aware():
    """按物理像素工作，避免 150% 缩放下坐标被系统偷偷换算。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


set_dpi_aware()


def virtual_desktop():
    m = win32api.GetSystemMetrics
    return (m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN),
            m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN))


def move_to_screen(sx, sy, settle=CURSOR_SETTLE):
    """把光标一步移动到**屏幕**坐标 (sx, sy)。"""
    vx, vy, vw, vh = virtual_desktop()
    nx = int(round((sx - vx) * 65535 / max(vw - 1, 1)))
    ny = int(round((sy - vy) * 65535 / max(vh - 1, 1)))
    win32api.mouse_event(
        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        nx, ny, 0, 0)
    if settle:
        time.sleep(settle)
    return sx, sy


def click_screen(sx, sy, hold=CLICK_HOLD, settle=CURSOR_SETTLE):
    move_to_screen(sx, sy, settle=settle)
    try:
        win32api.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(max(float(hold), 0.10))
    finally:
        win32api.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.08)


def click_client(hwnd, x, y, hold=CLICK_HOLD):
    sx, sy = win32gui.ClientToScreen(hwnd, (int(x), int(y)))
    click_screen(sx, sy, hold=hold)
    return sx, sy


def find_window(title_part=None, class_name=None, largest=True):
    """按标题片段 / 窗口类名找可见窗口；largest=True 取面积最大的。"""
    found = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if class_name and win32gui.GetClassName(hwnd) != class_name:
            return
        if title_part and title_part not in win32gui.GetWindowText(hwnd):
            return
        found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    if not found:
        return 0
    if not largest:
        return found[0]
    return max(found, key=lambda h: (
        lambda l, t, r, b: (r - l) * (b - t))(*win32gui.GetWindowRect(h)))


def find_game_hwnd():
    """只认 **UnityWndClass + 游戏标题**。

    坑（2026-10-26）：浏览器攻略帖标题里也有「篮球少女」，只按标题找会把点击
    打到 Edge 上；而且旧逻辑取面积最大的窗口，浏览器比游戏大，必中招。
    """
    return find_window(GAME_TITLE, GAME_CLASS)


def ensure_foreground(hwnd, tries=3):
    """把窗口真正置前；抢不到返回 False，调用方不要硬点。

    实现只有一份：`src/util/win_focus.force_foreground`（src 里 GirlsInteraction
    与这里共用同一段 win32 逻辑）。保留这个函数名是因为十来个 tools 脚本都在 import 它。
    """
    if not hwnd:
        return False
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    restore_if_minimized(hwnd)
    return force_foreground(hwnd, tries=tries)
