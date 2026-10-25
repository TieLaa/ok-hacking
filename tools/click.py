"""真实鼠标：把光标一步移到客户区坐标并点击。

用法：
    python tools/click.py 1370 640          # 客户区坐标
    python tools/click.py 1370 640 --hover  # 只移动不点击

注意（2026-10-26）：
- 移动统一走 `tools/abs_mouse.py` 的**绝对坐标**（MOUSEEVENTF_ABSOLUTE|VIRTUALDESK），
  一步到位，不再用相对位移滑行；`SetCursorPos` 已被证明游戏感知不到。
- 找窗口必须校验窗口类 `UnityWndClass`。浏览器攻略帖标题里也含「篮球少女」，
  只按标题找会点到 Edge 上。
"""
import os
import sys

import win32gui

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.abs_mouse import click_screen, ensure_foreground, find_game_hwnd, move_to_screen  # noqa: E402


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: click.py X Y [--hover]")
    x, y = int(sys.argv[1]), int(sys.argv[2])
    hover = "--hover" in sys.argv

    hwnd = find_game_hwnd()
    if not hwnd:
        raise SystemExit("找不到游戏窗口（UnityWndClass）")

    origin = win32gui.ClientToScreen(hwnd, (0, 0))
    sx, sy = origin[0] + x, origin[1] + y

    fg = ensure_foreground(hwnd)
    if not fg:
        raise SystemExit("游戏窗口未能置前，放弃点击")
    if hover:
        move_to_screen(sx, sy)
    else:
        click_screen(sx, sy)
    print(f"hwnd={hwnd} origin={origin} target_client=({x},{y}) target_screen=({sx},{sy}) hover={hover}")


if __name__ == "__main__":
    main()
