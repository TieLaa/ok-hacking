"""测量「卡池详情」网格的几何参数（一次性工具，测出来的数写进采集脚本）。

输出：
- 一行几张卡 / 列间距 dx（按位置标签的 x 聚类）
- 行距 dy（同列相邻两行标签的 y 差；需要一屏能看到两行）
- 卡片高度 card_h（从标签下方沿竖线找卡片底边）
- 整个卡池一共几行（按行距逐行下滚，累计行号）

用法：
    python tools/measure_pool_grid.py            # 只测当前屏几何
    python tools/measure_pool_grid.py --scan     # 再逐行下滚统计总行数
"""

import sys

import cv2
import numpy as np

ROOT = __file__.rsplit("\\tools\\", 1)[0]
sys.path.insert(0, ROOT)

from tools.abs_mouse import find_game_hwnd  # noqa: E402
from tools.collect_roster import cards_in_view, read, scroll, scroll_to_top, section_of, section_headers  # noqa: E402
from tools.collect_ssr import Screen  # noqa: E402

POSITIONS = ("控卫", "分卫", "中锋", "大前", "小前")


def measure_frame(frame, boxes, w, h):
    """返回 (列x列表, 行y列表, dx, 行距列表, 卡片高度列表)。"""
    labels = [b for b in boxes if b["text"] in POSITIONS]
    cols = sorted({round((b["x"] + b["w"] / 2) / w, 3) for b in labels})
    rows = sorted({round((b["y"] + b["h"] / 2) / h, 3) for b in labels})
    dxs = [round(cols[i + 1] - cols[i], 3) for i in range(len(cols) - 1)]
    dys = [round(rows[i + 1] - rows[i], 3) for i in range(len(rows) - 1)]

    # 卡片高度：从第一列标签中心往下扫，白框结束（亮度骤降）处就是卡底
    heights = []
    for b in labels[:3]:
        cx = int((b["x"] + b["w"] / 2))
        top = int(b["y"] + b["h"])
        col = frame[:, cx]
        # 卡片区域整体偏亮（白框 + 立绘），背景是浅蓝渐变；用「向下连续非背景」估
        ys = []
        for y in range(top, min(h - 1, top + int(h * 0.5))):
            bgr = col[y]
            # 背景判定：蓝通道明显高于红（浅蓝），卡片白框/立绘不满足
            if int(bgr[0]) - int(bgr[2]) > 25 and int(bgr[0]) > 180:
                break
            ys.append(y)
        if ys:
            heights.append(round((ys[-1] - b["y"]) / h, 3))
    return cols, rows, dxs, dys, heights


def measure_cards_by_contour(frame, w, h):
    """用轮廓检测卡片框：卡是白边圆角矩形，背景是浅蓝渐变。

    返回 [(x1,y1,x2,y2)], 按 y 再 x 排序。
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    # 背景：浅蓝、低饱和、高亮度；卡片：白框(高亮低饱和)或立绘(高饱和)
    bg = (val > 170) & (sat < 60)
    mask = (~bg).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw < w * 0.08 or ch < h * 0.08:
            continue
        if not (0.9 <= (ch / cw) <= 1.9):        # 卡片是竖长方形
            continue
        boxes.append((x, y, x + cw, y + ch))
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def main():
    scan = "--scan" in sys.argv
    hwnd = find_game_hwnd()
    if not hwnd:
        print("找不到游戏窗口")
        return 1
    screen = Screen(hwnd)
    try:
        boxes, w, h = read(screen)
        scroll_to_top(screen, w, h, limit=14)
        # 顶部是「雷霆一闪」那排小卡（没有位置标签），继续下滚到出现角色一览网格
        for _ in range(10):
            boxes, w, h = read(screen)
            labels = [b for b in boxes if b["text"] in POSITIONS]
            if len(labels) >= 5:
                break
            if not scroll(screen, w, h, +1):
                break
        frame = screen.frame()
        boxes, w, h = read(screen)
        cols, rows, dxs, dys, heights = measure_frame(frame, boxes, w, h)
        print(f"画面 {w}x{h}")
        print("列 x:", cols)
        print("列间距 dx:", dxs)
        print("本屏行 y:", rows, " 行距 dy:", dys)
        print("卡片高度(占屏高):", heights)
        cards = measure_cards_by_contour(frame, w, h)
        print(f"\n轮廓检测到 {len(cards)} 个候选卡框：")
        for (x1, y1, x2, y2) in cards[:12]:
            print(f"  ({x1},{y1})-({x2},{y2}) 宽 {(x2-x1)/w:.3f}w 高 {(y2-y1)/h:.3f}h")
        if cols and dxs:
            pitch = sum(dxs) / len(dxs)
            print(f"→ 一行 {round(1 / pitch)} 张（dx={pitch:.3f}，屏宽/{pitch:.3f}={1/pitch:.2f}）")

        if not scan:
            return 0

        print("\n逐行扫描（每次滚一个行距）…")
        row_pitch = (sum(dys) / len(dys)) if dys else 0.12
        seen = {}
        section = None
        total = 0
        for step in range(30):
            boxes, w, h = read(screen)
            head = section_of(section_headers(boxes))
            if head:
                section = head
            cards = cards_in_view(boxes, w, h)
            for c in cards:
                key = (section, c["pos"], round(c["cx"], 2))
                if key not in seen:
                    seen[key] = True
                    total += 1
                    print(f"  #{total:02d} {section} {c['pos']} @({c['cx']:.2f},{c['cy']:.2f})")
            # 按测得的行距滚一格（不足一行时会重叠，正好用来去重）
            if not scroll(screen, w, h, +1):
                print("  到底")
                break
        print(f"\n累计卡片 {total} 个；行距 {row_pitch:.3f}")
    finally:
        screen.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
