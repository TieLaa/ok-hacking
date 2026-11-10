"""打开顶栏「历史记录」面板，往上翻页抓全量，用于和脚本日志对账。

背景（用户 2026-11-01 口径）：第二个图标是历史记录，显示刚刚发生了什么事件、获得了
什么（零用钱 / 属性 / ...）；面板里往上滚可以看更早的记录。

用法：
    python tools/probe_history.py                 # 打开面板 + 往上翻到底，存每页截图和 OCR
    python tools/probe_history.py --no-open        # 面板已经开着，只翻页
    python tools/probe_history.py --pages 10       # 最多翻 10 屏（默认 20）

产物：`logs/history/<时间戳>/pageNN.png` + `text.txt`（每屏 OCR，按屏分隔）
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32api  # noqa: E402  # noqa: E402
import win32gui  # noqa: E402

from tools.abs_mouse import click_screen, ensure_foreground, find_game_hwnd, move_to_screen  # noqa: E402
from tools import _capture, _ocr  # noqa: E402

HISTORY_POINT = (0.819, 0.045)      # 顶栏第 2 个图标（历史记录）
LIST_CENTER = (0.50, 0.50)          # 面板中间，滚动落点
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120


def grab(hwnd):
    return _capture.grab_hwnd(hwnd, tries=15)


def ocr_items(ocr, img):
    """按 y 排序的 `[(y, x, text, score)]`（历史记录面板是自上而下的流水）。"""
    return sorted((y, x, text, score)
                  for text, x, y, _w, _h, score in _ocr.items(img, eng=ocr, min_score=0.4))


def wheel_up(times=3, gap=0.12):
    for _ in range(times):
        win32api.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, WHEEL_DELTA, 0)
        time.sleep(gap)

LIST_BOX = (0.14, 0.18, 0.87, 0.78)   # 面板里可滚动的白卡区域（判「画面有没有动」用）


def body(frame):
    h, w = frame.shape[:2]
    return frame[int(h * LIST_BOX[1]):int(h * LIST_BOX[3]), int(w * LIST_BOX[0]):int(w * LIST_BOX[2])]


def main():
    args = sys.argv[1:]
    pages = int(args[args.index("--pages") + 1]) if "--pages" in args else 20
    step = int(args[args.index("--step") + 1]) if "--step" in args else 10
    do_open = "--no-open" not in args

    hwnd = find_game_hwnd()
    if not hwnd:
        raise SystemExit("找不到游戏窗口")
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    cw, ch = right - left, bottom - top

    def pt(rel):
        return win32gui.ClientToScreen(hwnd, (int(cw * rel[0]), int(ch * rel[1])))

    if not ensure_foreground(hwnd):
        raise SystemExit("窗口未能置前")
    time.sleep(0.3)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join("logs", "history", stamp)
    os.makedirs(out_dir, exist_ok=True)

    if do_open:
        sx, sy = pt(HISTORY_POINT)
        move_to_screen(sx, sy)
        time.sleep(0.25)
        ensure_foreground(hwnd)
        click_screen(sx, sy)
        time.sleep(1.0)

    from onnxocr.onnx_paddleocr import ONNXPaddleOcr

    ocr = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, show_log=False)

    texts = []
    prev_body = None
    page = 0
    while page < pages:
        fr = grab(hwnd)
        if fr is None:
            texts.append(f"=== page {page}: 抓帧失败 ===")
            break
        cv2.imwrite(os.path.join(out_dir, "page%02d.png" % page), fr)
        items = ocr_items(ocr, fr)
        texts.append("=== page %02d ===" % page)
        for y, x, t, s in items:
            texts.append("  (%4d,%4d) %s  [%.2f]" % (x, y, t, s))
        texts.append("")
        page += 1

        cur = body(fr)
        # 滚轮往上（一次 10 格才有明显位移），光标必须先放到列表里
        cx, cy = pt((0.50, 0.45))
        move_to_screen(cx, cy)
        time.sleep(0.2)
        wheel_up(step, gap=0.02)
        time.sleep(0.8)
        nxt = grab(hwnd)
        if nxt is None:
            break
        diff = float(np.abs(body(nxt).astype(int) - cur.astype(int)).mean())
        texts.append("  (滚动后 diff=%.3f)" % diff)
        if diff < 0.05:
            texts.append("=== 到顶了（画面不再变化） ===")
            cv2.imwrite(os.path.join(out_dir, "page_top.png"), nxt)
            break
        prev_body = cur

    with open(os.path.join(out_dir, "text.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(texts))
    print("out", out_dir, "pages", page)


if __name__ == "__main__":
    main()
