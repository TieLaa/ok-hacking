"""OCR 找文字并点它 —— 命令行版的「按文字点击」，不依赖 ok-script。

用法：
    python tools/click_text.py 游戏厅
    python tools/click_text.py 游戏厅 --hover     # 只移动不点击
    python tools/click_text.py 确定 --list        # 只列出所有识别到的文字

为什么要这个工具：截图在当前环境里会被缩放显示，肉眼估的坐标经常差上百像素
（实测「返回」图标目测 1650 实际 1819）。文字框坐标是 OCR 直接给的，最可靠。
"""
import sys
import time

import cv2
import win32gui

sys.path.insert(0, __file__.rsplit("tools", 1)[0].rstrip("\\/"))

from tools.abs_mouse import (  # noqa: E402
    click_screen, ensure_foreground, find_game_hwnd, move_to_screen)
from tools.wgc_capture import MockWindow  # noqa: E402


def game_hwnd():
    # 必须校验 UnityWndClass：浏览器攻略帖标题里也有「篮球少女」（实训 2026-10-26）
    return find_game_hwnd()


def grab():
    from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

    cap = WindowsGraphicsCaptureMethod(MockWindow(game_hwnd()))
    frame = None
    for _ in range(10):
        frame = cap.get_frame()
        if frame is not None:
            break
    cap.close()
    return frame


def ocr(frame, texts_only=False):
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr

    engine = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, show_log=False)
    raw = engine.ocr(frame)
    page = raw[0] if isinstance(raw, list) and len(raw) == 1 else raw
    items = []
    for entry in page or []:
        try:
            box = entry[0]
            text, score = entry[1]
        except Exception:
            continue
        if not text or score is None or score < 0.5:
            continue
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        items.append((text.strip(), min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
    return items


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: click_text.py <文字> [--hover] [--list]")
    target = sys.argv[1]
    hover = "--hover" in sys.argv
    list_only = "--list" in sys.argv

    hwnd = game_hwnd()
    if not hwnd:
        raise SystemExit("找不到游戏窗口")
    frame = grab()
    if frame is None:
        raise SystemExit("抓帧失败")

    items = ocr(frame)
    if list_only:
        for t, x, y, w, h in sorted(items, key=lambda i: (i[2], i[1])):
            print(f"  「{t}」 @({x},{y},{w},{h})")
        return

    match = next((i for i in items if target in i[0]), None)
    if match is None:
        print(f"没找到「{target}」，本帧识别到：")
        for t, x, y, w, h in sorted(items, key=lambda i: (i[2], i[1])):
            print(f"  「{t}」 @({x},{y},{w},{h})")
        raise SystemExit(1)

    text, x, y, w, h = match
    cx, cy = x + w // 2, y + h // 2
    if not ensure_foreground(hwnd):
        raise SystemExit("游戏窗口未能置前，放弃点击")

    # 真实点击：绝对坐标一步到位，点完不还原光标
    if hover:
        move_to_screen(*win32gui.ClientToScreen(hwnd, (cx, cy)))
    else:
        click_screen(*win32gui.ClientToScreen(hwnd, (cx, cy)))

    print(f"「{text}」 box=({x},{y},{w},{h}) -> client=({cx},{cy}) hover={hover}")


if __name__ == "__main__":
    main()
