"""按窗口标题点一个客户区坐标（真实鼠标），可点完把游戏切回前台。

用法：
    python tools/click_window.py "OK dev" 1088 89            # 点 ok-script 的「开始」
    python tools/click_window.py "OK dev" 1088 89 --restore  # 点完把游戏切回前台

为什么单独写：ok-script 的 Qt 窗口对 PostMessage 时灵时不灵，真实点击才稳；
点完必须把游戏切回前台，否则 ok-script 会认为窗口不可见而暂停执行器。

移动统一走 `tools/abs_mouse.py`（绝对坐标一步到位）。找游戏窗口时必须校验
`UnityWndClass`，否则浏览器攻略帖标题里的「篮球少女」会被误认（实训 2026-10-26）。
"""
import os
import sys

import win32gui

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.abs_mouse import (  # noqa: E402
    click_screen, ensure_foreground, find_game_hwnd, find_window)


def main():
    title = sys.argv[1]
    x, y = int(sys.argv[2]), int(sys.argv[3])
    restore = "--restore" in sys.argv

    # 游戏标题只认 UnityWndClass；其它窗口（ok-script GUI）按标题找即可
    hwnd = find_game_hwnd() if "篮球少女" in title else find_window(title)
    if not hwnd:
        raise SystemExit(f"找不到窗口: {title}")
    if not ensure_foreground(hwnd):
        raise SystemExit("窗口未能置前，放弃点击")
    sx, sy = win32gui.ClientToScreen(hwnd, (x, y))
    click_screen(sx, sy)
    print(f"clicked {title} at ({x},{y}) screen=({sx},{sy})")

    if restore:
        g = find_game_hwnd()
        if g:
            print("game foreground:", ensure_foreground(g))


if __name__ == "__main__":
    main()
