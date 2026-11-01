"""金框判据的离线回归测试（不依赖游戏 / runtime）。

背景：2026-11-02 用户报「刚才最后一次的训练，最后一个意识有金框，没识别到」。
根因是 has_gold_border 的「2 倍其它行中位数」相对判据在**度假页**误杀真金框
（那一局「意识」占比 0.274 已过绝对门限 0.20，却被基线 0.229 挡掉）。

本测试用 logs/debug 的存证帧重算，断言：
  1. 已知真金框样本必须全部判为「有金框」；
  2. 度假页（按钮本身橙黄）的行必须全部判为「无金框」；
  3. 基线（中位数）不再被橙黄按钮抬到 0.2 以上。

跑法：
    .venv\\Scripts\\python.exe tools\\check_gold_border.py
"""
import os
import re
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "logs", "debug")
SUBJ = ("投射", "弹跳", "防守", "体能", "运控", "意识")

# 与 src/tasks/training/parsers.py 保持一致（这里不 import，避免拉起整个 ok 包）
GOLD_RING_MIN = 0.20
GOLD_RING_FACTOR = 2.0
GOLD_PIX_HUE_MIN = 18
GOLD_PIX_G_MIN = 200
GOLD_PAGE_WIDE_MIN = 2

# 已知真金框样本：离线核对过图 + 日志 ground truth（2026-11-02）
GOLD_SAMPLES = {
    ("1031-122608_subject09.txt", "意识"),
    ("1031-122609_subject10.txt", "意识"),
    ("1031-122610_subject11.txt", "意识"),
    ("1101-122130_subject11.txt", "投射"),
}
# 度假页（橙黄 MAX 按钮）的行：一律不许判成金框
VACATION_FRAMES = {
    "1101-122111_subject07.txt", "1101-122113_subject09.txt",
    "1101-122114_subject10.txt", "1101-122041_subject03.txt",
    "1101-232911_subject00.txt", "1031-195115_subject00.txt",
    "1031-202920_subject00.txt",
}


def load(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def parse(txt):
    img = load(txt[:-4] + ".jpg")
    h, w = img.shape[:2]
    rows = {}
    for line in open(txt, encoding="utf-8"):
        line = line.strip()
        if line.startswith("# subject="):
            continue
        m = re.match(r"(.+)\((\d+),(\d+),(\d+),(\d+)\)", line)
        if not m:
            continue
        name = m.group(1).strip()
        x, y, ww, hh = map(int, m.groups()[1:])
        if name in SUBJ and (x + ww / 2) / w > 0.7:
            rows.setdefault(name, (x, y, ww, hh))
    return img, rows, h, w


def ring_ratio(img, x, y, ww, hh, h, w):
    """和 parsers.has_gold_border 的 ring_gold_ratio 一致。"""
    cy = y + ww * 0 + hh / 2
    x1, x2 = int(w * 0.752), int(w * 0.968)
    y1, y2 = max(0, int(cy - h * 0.055)), min(h, int(cy + h * 0.055))
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    blue, green, red = cv2.split(roi)
    gold = ((sat > 110) & (val > 150)
            & (hue >= GOLD_PIX_HUE_MIN) & (hue <= 42)
            & (green >= GOLD_PIX_G_MIN))
    t = max(6, int(h * 0.012))
    ring = np.zeros(roi.shape[:2], dtype=bool)
    ring[:t, :] = True
    ring[-t:, :] = True
    ring[:, :t] = True
    ring[:, -t:] = True
    return float((gold & ring).sum()) / max(int(ring.sum()), 1)


def is_border(ratio, over_count, n_subjects):
    """和 parsers.has_gold_border 的判定一致。"""
    if ratio <= GOLD_RING_MIN:
        return False
    page_wide = over_count >= max(GOLD_PAGE_WIDE_MIN, n_subjects // 2)
    if page_wide:
        return False
    return True


def main():
    if not os.path.isdir(ROOT):
        print(f"跳过：找不到存证目录 {ROOT}")
        return 0
    files = sorted(f for f in os.listdir(ROOT)
                   if f.endswith(".txt") and "subject" in f)
    fails = []
    checked = 0
    gold_hits = 0
    vac_false_pos = 0
    max_baseline = 0.0
    for f in files:
        txt = os.path.join(ROOT, f)
        try:
            img, rows, h, w = parse(txt)
        except FileNotFoundError:
            continue
        if img is None or len(rows) < 2:
            continue
        vals = {n: ring_ratio(img, *rows[n], h, w) for n in rows}
        med = sorted(vals.values())[len(vals) // 2]
        max_baseline = max(max_baseline, med)
        over_count = sum(1 for v in vals.values() if v > GOLD_RING_MIN)
        for n, v in vals.items():
            hit = is_border(v, over_count, len(rows))
            checked += 1
            key = (f, n)
            if key in GOLD_SAMPLES:
                if hit:
                    gold_hits += 1
                else:
                    fails.append(f"真金框漏检 {f} {n} ratio={v:.3f} "
                                 f"过门限行数={over_count}/{len(rows)}")
            elif f in VACATION_FRAMES and hit:
                vac_false_pos += 1
                fails.append(f"度假页误报 {f} {n} ratio={v:.3f} "
                             f"过门限行数={over_count}/{len(rows)}")

    print(f"检查 {checked} 个科目行（{len(files)} 个存证帧）")
    print(f"真金框召回: {gold_hits}/{len(GOLD_SAMPLES)}")
    print(f"度假页误报: {vac_false_pos}")
    print(f"基线中位数最大值: {max_baseline:.3f}（要求 < 0.10）")
    if max_baseline >= 0.10:
        fails.append(f"基线仍被抬高到 {max_baseline:.3f}（>=0.10）")

    if fails:
        print("\n失败：")
        for x in fails:
            print("  " + x)
        return 1
    print("\n全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
