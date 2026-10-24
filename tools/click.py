"""真实鼠标：用相对位移逐步移动后点击（模拟人的移动轨迹）。

为什么不用 SetCursorPos 瞬移：
实测本游戏对 SetCursorPos 的瞬移不响应（点出来的位置和画面对不上），
而真实鼠标的移动是相对位移（raw input）。所以这里用 mouse_event(MOVE, dx, dy)
分小步移动，再点。

用法：
    python tools/click.py 1370 640          # 客户区坐标
    python tools/click.py 1370 640 --hover  # 只移动不点击
"""
import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.LONG, wintypes.LONG, wintypes.DWORD, ctypes.c_void_p]


def game_hwnd():
    import win32gui

    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "篮球少女" in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return max(found, key=lambda h: (lambda l, t, r, b: (r - l) * (b - t))(*win32gui.GetWindowRect(h))) if found else 0


def cursor():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move_relative(dx, dy):
    user32.mouse_event(MOUSEEVENTF_MOVE, dx, dy, 0, None)


def move_to(sx, sy, step=10, delay=0.012):
    cx, cy = cursor()
    dx, dy = sx - cx, sy - cy
    steps = max(1, int((abs(dx) + abs(dy)) / step))
    for i in range(1, steps + 1):
        wx, wy = round(dx * i / steps), round(dy * i / steps)
        mx, my = wx - round(dx * (i - 1) / steps), wy - round(dy * (i - 1) / steps)
        if mx or my:
            move_relative(mx, my)
        time.sleep(delay)
    time.sleep(0.12)
    return cursor()


def click(down=0.07):
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(down)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    time.sleep(0.25)


def ensure_foreground(hwnd):
    """把目标窗口真正置前。

    Windows 默认禁止后台进程抢前台（SetForegroundWindow 静默失败），
    标准绕过办法：先模拟按一下 Alt，让系统认为"用户有输入意图"，再置顶。
    参考 ok-end-field src/interaction/Mouse.py。
    """
    import win32api
    import win32con
    import win32gui

    if win32gui.GetForegroundWindow() == hwnd:
        return True
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)

    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(0.01)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception as e:
        print(f"SetForegroundWindow failed: {e}")
    finally:
        # Alt 必须抬起，否则后续所有按键都会变成 Alt 组合键
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.35)
    return win32gui.GetForegroundWindow() == hwnd


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: click.py X Y [--hover]")
    x, y = int(sys.argv[1]), int(sys.argv[2])
    hover = "--hover" in sys.argv

    hwnd = game_hwnd()
    if not hwnd:
        raise SystemExit("找不到游戏窗口")
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    sx, sy = origin.x + x, origin.y + y

    before = cursor()
    fg = ensure_foreground(hwnd)
    after = move_to(sx, sy)
    if not hover:
        click()
    print(f"hwnd={hwnd} origin=({origin.x},{origin.y}) target_client=({x},{y}) target_screen=({sx},{sy})")
    print(f"foreground={fg} cursor {before} -> {after} (want {sx},{sy}) hover={hover}")


if __name__ == "__main__":
    main()
