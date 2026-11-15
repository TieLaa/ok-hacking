"""科目页：把六个科目的「训练后精力」一次读全。

为什么要这个工具：科目页顶栏的药丸显示的是**当前选中科目练完之后**的精力，
换一个科目才刷新一次。要比较六个科目就得点六次、读六次 —— 手点容易点错科目，
也看不出「哪个科目最省精力」。

判据来源（2026-11-15 三个实拍帧，见 docs/RULES.md §6）：
药丸的数字 = 训练后精力；黄段画到「当前/训练后」里较小的那个。

用法：
    python tools/probe_subject_stamina.py

要求游戏停在**训练科目页**（有「失败率」和六个科目按钮）。工具只点科目按钮，
**不会点「决定」**，所以不会真的开练；当前已选中的那一格直接读、不重复点。

产物：屏幕一份表格 + `logs/subject_stamina/<时间戳>.txt`
"""

import os
import sys
import time

import win32gui

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tasks.ui import labels, zoom_reread  # noqa: E402
from tools import _capture, _ocr  # noqa: E402
from tools.abs_mouse import click_screen, ensure_foreground, move_to_screen  # noqa: E402
from tools.wgc_capture import find_hwnd  # noqa: E402
from tools._console import utf8_stdout  # noqa: E402

# 顶栏药丸那一块的归一化区域（和 `probe_topbar` / `scan` 用的同一段）
BAR_REGION = (0.175, 0.0, 0.520, 0.095)
# 右侧科目按钮列
LIST_REGION = (0.72, 0.0, 1.0, 0.95)
# 点完到药丸刷新之间的等待（实测 0.6 秒够，留余量）
SETTLE = 0.9


def grab(hwnd):
    return _capture.grab_hwnd(hwnd)


def ocr_items(engine, img, region=None):
    """`[(文字, cx, cy)]`；给了 region 就只认那一块（归一化坐标）。"""
    h, w = img.shape[:2]
    x0, y0 = 0, 0
    if region:
        rx1, ry1, rx2, ry2 = region
        x0, y0 = int(w * rx1), int(h * ry1)
        img = img[y0:int(h * ry2), x0:int(w * rx2)]
    out = []
    for text, x, y, bw, bh, _score in _ocr.items(img, eng=engine, min_score=0.5):
        out.append((text.strip(), x0 + x + bw // 2, y0 + y + bh // 2))
    return out


def read_stamina(img, engine):
    """药丸上的 `训练后/上限` → 训练后精力；读不到返回 None。"""
    texts = zoom_reread.reread_region(img, engine, BAR_REGION)
    for text, _score in texts:
        m = labels.STAMINA_RE.search(text.replace(" ", ""))
        if m:
            return int(m.group(1))
    return None


def subject_boxes(img, engine):
    return {name: (cx, cy) for name, cx, cy in ocr_items(engine, img, LIST_REGION)
            if name in labels.TRAINING_SUBJECTS}


def selected_subject(img, engine, boxes):
    """当前已选中的科目 = 和「决定」同一行、在它左边的那个按钮。"""
    decide = next(((cx, cy) for text, cx, cy in ocr_items(engine, img)
                   if text == "决定"), None)
    if decide is None or not boxes:
        return None
    dx, dy = decide
    near = [(abs(cy - dy), cx, name) for name, (cx, cy) in boxes.items() if cx < dx]
    return min(near)[2] if near else None


def click_at(hwnd, pos):
    ensure_foreground(hwnd)
    sx, sy = win32gui.ClientToScreen(hwnd, pos)
    move_to_screen(sx, sy)
    time.sleep(0.25)
    ensure_foreground(hwnd)
    click_screen(sx, sy)


def main():
    utf8_stdout()
    hwnd = find_hwnd("篮球少女：飞跃巅峰")
    if not hwnd:
        raise SystemExit("找不到游戏窗口（UnityWndClass + 标题）")
    engine = _ocr.engine()

    img = grab(hwnd)
    if img is None:
        raise SystemExit("抓帧失败")
    page = "".join(t for t, _x, _y in ocr_items(engine, img))
    if "失败率" not in page:
        raise SystemExit("当前不是训练科目页（没读到「失败率」）")

    boxes = subject_boxes(img, engine)
    if len(boxes) < 6:
        print("只认出 %d 个科目：%s" % (len(boxes), "、".join(boxes)))

    rows = []
    current = selected_subject(img, engine, boxes)
    if current:
        rows.append((current, read_stamina(img, engine), "当前已选，直接读"))
    for name in labels.TRAINING_SUBJECTS:
        if name == current:
            continue
        pos = subject_boxes(grab(hwnd), engine).get(name)
        if pos is None:
            rows.append((name, None, "找不到按钮"))
            continue
        click_at(hwnd, pos)
        time.sleep(SETTLE)
        rows.append((name, read_stamina(grab(hwnd), engine), "点选后读"))

    known = [v for _n, v, _w in rows if v is not None]
    best = max(known) if known else None
    lines = ["科目\t训练后精力\t相对最高\t怎么读的"]
    for name, value, how in rows:
        delta = "" if value is None or best is None else "%+d" % (value - best)
        lines.append("%s\t%s\t%s\t%s" % (name, "?" if value is None else value, delta, how))
    report = "\n".join(lines)
    print(report)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join("logs", "subject_stamina")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, stamp + ".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    # stdout 被接进 cmdlet 时中文会被 CP936 解坏，以文件为准（见 tools/_console.py）
    print("report=" + os.path.abspath(path))


if __name__ == "__main__":
    main()
