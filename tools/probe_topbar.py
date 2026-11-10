"""探测顶栏右上角图标条：倍数档位 / 历史记录 / 菜单 / 返回。

为什么单独一个工具：倍速是 **OFF → 1x → 2x → 3x 循环**（用户 2026-11-01 口径），
而 `handlers/match.py: speed_is_2x()` 只判「右三角实心蓝」= 「至少 1x」，
分不出 2x / 3x。这个工具把四个状态逐帧抓下来 + OCR 小字 + 数三角个数，
用来校准判据（也用来确认 `TOP_ICON_POINTS` 的坐标是否还准）。

用法：
    python tools/probe_topbar.py                 # 抓一帧，报告当前档位
    python tools/probe_topbar.py --click 1       # 点一下倍速，再报告
    python tools/probe_topbar.py --cycle 4       # 连点 4 次，每次存图（看完整循环）
    python tools/probe_topbar.py --hover         # 只把光标移到倍速图标上，不点

产物：`logs/topbar/<时间戳>/state<N>.png`（图标条放大图）+ `report.txt`
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.abs_mouse import ensure_foreground, find_game_hwnd, move_to_screen  # noqa: E402
from tools import _capture, _ocr  # noqa: E402

import win32gui  # noqa: E402

# 图标条：四个图标的横向跨度（相对坐标，实测见 docs/UI_MAP.md）
STRIP = (0.735, 0.000, 0.965, 0.085)
# 倍速图标的三角区（不含下面的 Nx 小字）
TRI_BAND = (0.740, 0.010, 0.785, 0.048)
# 现有代码用的判据 ROI（右三角）
LEGACY_ROI = (0.759, 0.018, 0.773, 0.039)
# 倍速图标下面那行「OFF / 1x / 2x / 3x」小字
LABEL_CROP = (0.736, 0.042, 0.792, 0.086)
SPEED_POINT = (0.760, 0.044)


def grab(hwnd):
    return _capture.grab_hwnd(hwnd, tries=15)


def ocr_engine():
    return _ocr.engine()


def ocr_items(ocr, img):
    """`[(text, x, y, w, h, score)]`（实现见 tools/_ocr.py）。"""
    return _ocr.items(img, eng=ocr, min_score=0.4)


def crop_rel(frame, box):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    return frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]


def icon_mask(img):
    """蓝灰实心块：不是纯白底，且偏蓝。"""
    b = img[:, :, 0].astype(int)
    r = img[:, :, 2].astype(int)
    return (b > 120) & (b < 240) & ((b - r) > 8)


def count_triangles(frame):
    """数倍速图标左侧三角串里有几个三角（1x / 2x / 3x 的主要区别之一）。"""
    band = crop_rel(frame, TRI_BAND)
    m = icon_mask(band)
    colsum = m.sum(axis=0)
    thr = max(3, int(band.shape[0] * 0.15))
    groups, start = [], None
    for i, v in enumerate(colsum):
        if v >= thr and start is None:
            start = i
        elif v < thr and start is not None:
            if i - start >= 4:
                groups.append((start, i))
            start = None
    if start is not None:
        groups.append((start, len(colsum)))
    return len(groups), groups, band


def legacy_blue(frame):
    roi = crop_rel(frame, LEGACY_ROI)
    if roi.size == 0:
        return 0.0
    b = roi[:, :, 0].astype(int)
    r = roi[:, :, 2].astype(int)
    return float((((b > 150) & (b > r + 20))).mean())


def right_tri_fill(frame):
    """三角带里最右边那个三角的填充率：空心（描边）填充率低，实心高。"""
    n, groups, band = count_triangles(frame)
    if not groups:
        return 0.0, n
    a, b = groups[-1]
    sub = icon_mask(band)[:, a:b]
    if sub.size == 0:
        return 0.0, n
    # 三角是等腰三角，外接矩形里理论填充 0.5；用「非背景像素占外接矩形」比较
    rows = np.where(sub.sum(axis=1) > 0)[0]
    if len(rows) == 0:
        return 0.0, n
    box = sub[rows.min():rows.max() + 1, :]
    return float(box.mean()), n


def report(ocr, frame, tag, out_dir, save=True):
    strip = crop_rel(frame, STRIP)
    # 图标条里的字很小（「3x」只有 ~30px 宽），先放大再 OCR，否则一律读空
    big_strip = cv2.resize(strip, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    items = ocr_items(ocr, big_strip)
    texts = [f"{t[0]}@({t[1]//3 + int(frame.shape[1] * STRIP[0])},{t[2]//3})" for t in items]
    # 倍速图标下面那行 Nx 小字单独放大 8 倍再读一遍（这是档位的权威判据）
    label = crop_rel(frame, LABEL_CROP)
    big_label = cv2.resize(label, None, fx=8.0, fy=8.0, interpolation=cv2.INTER_CUBIC)
    label_txt = [t[0] for t in ocr_items(ocr, big_label)]
    n_tri, groups, _ = count_triangles(frame)
    fill, n_tri2 = right_tri_fill(frame)
    blue = legacy_blue(frame)
    lines = [
        f"--- {tag} ---",
        f"倍速小字(8x): {label_txt}",
        f"图标条 OCR(3x): {texts}",
        f"三角个数: {n_tri}  最右三角填充率: {fill:.2f}",
        f"旧判据 ROI 蓝占比: {blue:.2f}  -> 判定{'已加速(>=1x)' if blue > 0.45 else '未加速'}",
    ]
    if save:
        big = cv2.resize(strip, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(os.path.join(out_dir, f"state_{tag}.png"), big)
        lines.append(f"图: state_{tag}.png")
    return lines


def main():
    args = sys.argv[1:]
    clicks = 0
    if "--click" in args:
        clicks = int(args[args.index("--click") + 1])
    if "--cycle" in args:
        clicks = int(args[args.index("--cycle") + 1])
    hover = "--hover" in args

    hwnd = find_game_hwnd()
    if not hwnd:
        raise SystemExit("找不到游戏窗口（UnityWndClass + 标题）")
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    cw, ch = right - left, bottom - top
    print(f"hwnd={hwnd} client={cw}x{ch}")

    if not ensure_foreground(hwnd):
        raise SystemExit("游戏窗口未能置前，放弃（否则抓帧是黑的/点击会打空）")
    time.sleep(0.3)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join("logs", "topbar", stamp)
    os.makedirs(out_dir, exist_ok=True)

    ocr = ocr_engine()
    lines = []

    def snap(tag):
        fr = grab(hwnd)
        if fr is None:
            lines.append(f"--- {tag} --- 抓帧失败")
            return
        lines.extend(report(ocr, fr, tag, out_dir))
        lines.append("")

    snap("S0")

    sx, sy = win32gui.ClientToScreen(hwnd, (int(cw * SPEED_POINT[0]), int(ch * SPEED_POINT[1])))
    for i in range(clicks):
        from tools.abs_mouse import click_screen

        # 每一下都要重新抢前台 + 先 hover 再点：只抢一次前台时实测点击会被吃掉
        # （2026-11-01：一次 ensure_foreground + 连点 4 下，四帧全是同一个状态）
        ensure_foreground(hwnd)
        move_to_screen(sx, sy)
        time.sleep(0.25)
        ensure_foreground(hwnd)
        click_screen(sx, sy)
        time.sleep(0.7)
        snap(f"S{i + 1}")
    if hover:
        move_to_screen(sx, sy)
        lines.append(f"光标已移到倍速图标 {sx},{sy}")

    txt = "\n".join(lines)
    with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"产物目录: {out_dir}")


if __name__ == "__main__":
    main()
