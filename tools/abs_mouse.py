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
import win32con
import win32gui
import win32process

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
    """把窗口真正置前；抢不到返回 False，调用方不要硬点。"""
    if not hwnd:
        return False
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)

    for _ in range(tries):
        fg = win32gui.GetForegroundWindow()
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        time.sleep(0.02)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        finally:
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        if win32gui.GetForegroundWindow() == hwnd:
            return True

        try:
            cur_tid = win32api.GetCurrentThreadId()
            fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
            if fg_tid and fg_tid != cur_tid:
                win32process.AttachThreadInput(cur_tid, fg_tid, True)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                finally:
                    win32process.AttachThreadInput(cur_tid, fg_tid, False)
        except Exception:
            pass
        if win32gui.GetForegroundWindow() == hwnd:
            return True

        try:
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        except Exception:
            pass
        time.sleep(0.15)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    return False
