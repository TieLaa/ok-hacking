"""判定实验：游戏不在前台时，真实 mouse_event 点击是否生效。

安全前提：只点训练科目的**第一下**（预览），并在科目间来回切换，
绝不连点同一科目（第二下=确认训练，会扣体力）。

输出：每次点击后的前台窗口 + 截图，由人工比对界面是否切换。
"""

import os
import sys
import time

import win32api
import win32con
import win32gui
from PIL import ImageGrab

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.abs_mouse import click_client, find_game_hwnd, find_window  # noqa: E402

OUT = r"C:\Users\tiela\AppData\Local\Temp\opencode"
SUBJECT = {"投射": (1440, 215), "弹跳": (1440, 345), "防守": (1440, 475)}
GAME = find_game_hwnd()


def shoot(tag):
    r = win32gui.GetWindowRect(GAME)
    img = ImageGrab.grab(bbox=r, all_screens=True)
    img.save(f"{OUT}\\bg_{tag}.png")
    print(f"  [{tag}] 前台={win32gui.GetWindowText(win32gui.GetForegroundWindow())!r} -> {img.size}")


def main():
    other = find_window("OpenCode")
    print("把别的窗口切到前台：", other)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(0.02)
    try:
        win32gui.SetForegroundWindow(other)
    except Exception as e:
        print("  SetForegroundWindow:", e)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.6)
    print("  当前前台:", win32gui.GetWindowText(win32gui.GetForegroundWindow()))
    if win32gui.GetForegroundWindow() == GAME:
        print("!! 游戏仍在前台，实验无效")
        return

    shoot("0_before")

    print("点击「弹跳」（第一下=预览）")
    click_client(GAME, *SUBJECT["弹跳"])
    time.sleep(1.0)
    shoot("1_dantiao")

    print("点击「投射」（第一下=预览）")
    click_client(GAME, *SUBJECT["投射"])
    time.sleep(1.0)
    shoot("2_toushe")

    print("点击「防守」（第一下=预览）")
    click_client(GAME, *SUBJECT["防守"])
    time.sleep(1.0)
    shoot("3_fangshou")


if __name__ == "__main__":
    main()
