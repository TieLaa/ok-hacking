"""离线标定：周末地图上「地点名字上方的队友气泡」用什么判据分得开。

用法：
    python tools/calibrate_mate_bubble.py logs/debug/1026-184525_nochange3/frame.jpg

输出每个地点的深色像素占比，用来给 BaseGirlsTask.venue_has_mate 定阈值。
"""

import sys

import cv2
import numpy as np

# 相对坐标（按 1680x950 实测帧估的），只看标定用
VENUES = {
    "游戏厅": (0.482, 0.460),   # 有气泡
    "餐厅": (0.165, 0.726),     # 有气泡
    "步行街": (0.563, 0.874),   # 有气泡
    "公园": (0.833, 0.595),     # 没气泡
}

DX = 0.07          # 左右各 7% 宽
UP_TOP = 0.35      # 从标签往上 35% 高开始
UP_BOTTOM = 0.10   # 到往上 10% 高结束


def dark_fraction(frame, cx, cy):
    h, w = frame.shape[:2]
    x1 = int(max(0.0, cx - DX) * w)
    x2 = int(min(1.0, cx + DX) * w)
    y1 = int(max(0.0, cy - UP_TOP) * h)
    y2 = int(max(0.0, cy - UP_BOTTOM) * h)
    if x2 <= x1 or y2 <= y1:
        return 0.0, (x1, y1, x2, y2)
    crop = frame[y1:y2, x1:x2]
    b, g, r = crop[:, :, 0].astype(int), crop[:, :, 1].astype(int), crop[:, :, 2].astype(int)
    # 深藏青：三通道都暗，且蓝 >= 红（气泡是 #2e3350 那种）
    mask = (r < 95) & (g < 95) & (b < 140) & (b >= r)
    return float(mask.mean()), (x1, y1, x2, y2)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "logs/debug/1026-184525_nochange3/frame.jpg"
    frame = cv2.imread(path)
    if frame is None:
        print(f"读不到图: {path}")
        return 1
    h, w = frame.shape[:2]
    print(f"帧 {w}x{h}: {path}")
    for name, (cx, cy) in VENUES.items():
        frac, box = dark_fraction(frame, cx, cy)
        print(f"  {name:<4} 深色占比 {frac:.4f}  区域 {box}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
