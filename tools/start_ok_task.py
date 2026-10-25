"""一键启动 ok-script 的「养成-训练循环」任务。

为什么要这个脚本：
- ok-script GUI 会自己乱飘（每次 ShowWindow/geometry 事件都可能换位置），
  所以每次点击都必须**当场读窗口矩形**，不能用记下来的屏幕坐标；
- 任务卡位置会变（跑完之后顶部会展开「已完成」面板，卡片整体下移），
  所以按候选 y 逐个试，用日志里的 `queued onetime_task` 确认是否真的点上了；
- 点完必须把 ok-script 窗口**最小化**，否则它会突然抢到前台，
  真实鼠标事件就全打到 GUI 上（实训：点击全落空，看起来像"点在没反应的地方"）。

用法：
    python tools/start_ok_task.py
"""

import io
import os
import sys
import time

import win32api
import win32con
import win32gui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.abs_mouse import click_screen, ensure_foreground  # noqa: E402

LOG = os.path.join(ROOT, "logs", "ok-script.log")

# 相对于 ok-script 主窗口左上角的「开始」按钮候选位置（窗口固定 1200x800）
CANDIDATE_Y = (89, 470, 350, 210)
BUTTON_X = 1089


def find_ok_window():
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "篮球少女 OK" in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def find_game_window():
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "篮球少女：飞跃巅峰" == win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def real_click(x, y):
    # 绝对坐标一步到位（与 GirlsInteraction 相同），不再滑行 / SetCursorPos
    click_screen(x, y)


def log_tail(n=200):
    try:
        with io.open(LOG, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def queued_since(mark):
    for line in log_tail(400):
        if "queued onetime_task" in line and line[:19] >= mark:
            return True
    return False


def main():
    ok = find_ok_window()
    if ok is None:
        print("找不到 ok-script 窗口")
        return 1

    # 先把 GUI 摆到一个不遮游戏的位置，并让它当前台（Qt 按钮需要激活）
    win32gui.ShowWindow(ok, win32con.SW_RESTORE)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(0.02)
    try:
        win32gui.SetForegroundWindow(ok)
    except Exception:
        pass
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.6)

    mark = time.strftime("%Y-%m-%d %H:%M:%S")
    clicked = None
    for dy in CANDIDATE_Y:
        rect = win32gui.GetWindowRect(ok)
        x, y = rect[0] + BUTTON_X, rect[1] + dy
        real_click(x, y)
        time.sleep(1.6)
        if queued_since(mark):
            clicked = (x, y)
            break

    if clicked is None:
        print("点了所有候选位置都没排队成功")
        return 1
    print(f"已排队，点击位置 {clicked}")

    # 关键：立刻最小化 GUI，别让它抢前台
    win32gui.ShowWindow(ok, win32con.SW_MINIMIZE)
    time.sleep(0.4)

    game = find_game_window()
    if game:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        time.sleep(0.02)
        try:
            win32gui.SetForegroundWindow(game)
        except Exception:
            pass
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.4)
        print("游戏前台:", win32gui.GetForegroundWindow() == game)
    return 0


if __name__ == "__main__":
    sys.exit(main())
