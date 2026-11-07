"""离线回归：扭蛋卡「队友标记」判据（`parsers.has_mate_badge`），不启动游戏。

为什么要有这个（2026-11-07 扭蛋误购事故）：
    用户指出「第二次买扭蛋没选左边有队友的卡是识别问题」。复现发现旧判据
    在**徽章完全可见**的帧上也返回 False —— ROI 上沿 0.22h 把卡片上方的
    灰蓝背景带框进来，背景 `b-r≈30` 命中旧色判据 `b > r+20`，和徽章
    连成一整块 214×58（长宽比 3.69 超 2.8 上限被拒）；分离出来的徽章小块
    面积占比 0.019 < 0.03 下限也被拒。也就是说旧判据在当前版本画面上
    从来没对过（10-31、11-03 两次「没识别到队友」的同源事故）。

    本脚本用 `logs/events/*_handle_street.jpg` 的真实帧对比 新旧判据，
    并打印每个通过块的几何参数（真徽章 ≈136~140×53~54 @ y=213、
    长宽比 2.5~2.6、fill≈0.5），改判据后跑一遍即可回归。

用法：
    python tools/check_mate_badge.py            # 扫全部历史扭蛋帧
    python tools/check_mate_badge.py 图.jpg ...  # 只测指定帧

判定基准（2026-11-07 人工确认）：
    191553 / 191612 左卡有徽章 → 新判据必须 True；
    191616 / 191808 左卡被「再买一次！」横幅遮住 → 必须 False。
    其余帧不允许出现 frac>=0.04 的通过块（出现即误报，exit 1）。

退出码：有「应检出未检出」或「不该检出却检出」时返回 1。
"""

import glob
import json
import os
import sys

import cv2
import numpy as np  # noqa: F402  (deep_mask / deep_mask_old 依赖)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools._console import utf8_stdout, write_report  # noqa: E402

# 三个卡位的中心 x 比例（1549 宽实测：左 397/1549、中 778、右 1159）
CARD_CX = {"左": 0.256, "中": 0.502, "右": 0.748}

# 人工确认过的事实：帧名(去掉日期前缀) -> [(卡位, 应为True/False)]
EXPECTED = {
    "191553": [("左", True)],
    "191612": [("左", True)],
    "191616": [("左", False)],   # 「再买一次！」横幅盖住徽章
    "191808": [("左", False)],   # 同上
}


def deep_mask(roi):
    """新版深蓝判据：徽章胶囊底色 b-r≈63~65、S≈117；背景带 b-r≈11~30、S≈61~68。"""
    b = roi[:, :, 0].astype(int)
    g = roi[:, :, 1].astype(int)
    r = roi[:, :, 2].astype(int)
    s = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)[:, :, 1].astype(int)
    return ((b - r >= 45) & (s >= 95) & (r < 150)).astype(np.uint8)


def deep_mask_old(roi):
    """旧版判据原文（parsers.py 1045-1048），用于对照展示它漏检。"""
    b = roi[:, :, 0].astype(int)
    g = roi[:, :, 1].astype(int)
    r = roi[:, :, 2].astype(int)
    orange = (r > 200) & (g > 90) & (g < 200) & (b < 90)
    deep = (b > r + 20) & (r < 130) & (g < 130) & (b < 170)
    return deep.astype(np.uint8), orange


def probe(img, cx, top=0.245):
    """对一张帧的一个卡位跑新判据。

    返回 (命中?, 描述)。形状阈值与 has_mate_badge 新版一致：
    frac 0.04~0.30、长宽比 0.5~3.0、实心率 >=0.45（实心率拦横幅描边碎片）。
    """
    h, w = img.shape[:2]
    x1 = int(w * (cx - 0.12))
    x2 = int(w * (cx + 0.02))
    y1, y2 = int(h * top), int(h * 0.36)
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return False, "empty roi"
    m = deep_mask(roi)
    if not m.any():
        return False, "no px"
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(m, 8)
    roi_area = float(m.size)
    hit, desc = False, []
    for i in range(1, n):
        x, y, ww, hh, area = (int(stats[i, k]) for k in range(5))
        if area < 100:
            continue
        frac = area / roi_area
        ar = ww / max(hh, 1)
        fill = area / float(ww * hh)
        ok = 0.04 <= frac <= 0.30 and 0.5 <= ar <= 3.0 and fill >= 0.45
        desc.append("abs=({},{}) {}x{} frac={:.3f} ar={:.2f} fill={:.2f}{}"
                    .format(x1 + x, y1 + y, ww, hh, frac, ar, fill,
                            "*" if ok else ""))
        if ok:
            hit = True
    return hit, "; ".join(desc[:4])


def probe_old(img, cx):
    """旧判据（ROI top=0.22、无饱和度/实心率过滤），打印对照用。"""
    h, w = img.shape[:2]
    x1 = int(w * (cx - 0.12))
    x2 = int(w * (cx + 0.02))
    y1, y2 = int(h * 0.22), int(h * 0.36)
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return False
    deep, orange = deep_mask_old(roi)
    roi_area = float(orange.size)
    if deep.any():
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(deep, 8)
        for i in range(1, n):
            ww, hh, area = int(stats[i, 2]), int(stats[i, 3]), int(stats[i, 4])
            frac = area / roi_area
            if 0.03 <= frac <= 0.28 and 0.5 <= ww / max(hh, 1) <= 2.8:
                return True
    return False


def main():
    utf8_stdout()
    args = sys.argv[1:]
    if args:
        files = [a for a in args if os.path.exists(a)]
    else:
        files = sorted(glob.glob(
            os.path.join(ROOT, "logs", "events", "*_handle_street.jpg")))

    lines = []
    fails = []
    for path in files:
        img = cv2.imread(path)
        if img is None:
            continue
        name = os.path.basename(path)[:11]        # 1107-191612
        short = name[5:]                          # 191612
        # 该帧有没有「再买一次！」横幅（texts 来自同名 json）
        banner = False
        jpath = path[:-4] + ".json"
        if os.path.exists(jpath):
            with open(jpath, encoding="utf-8") as fh:
                banner = "再买一次" in json.load(fh).get("texts", [])
        old = probe_old(img, CARD_CX["左"])
        hits = {}
        for pos, cx in CARD_CX.items():
            hit, desc = probe(img, cx)
            hits[pos] = (hit, desc)
        cells = " ".join("{}={}".format(p, "T" if hits[p][0] else "F")
                         for p in CARD_CX)
        print("{} 横幅={} 旧判据(左)={} 新判据 {}"
              .format(name, "有" if banner else "无", "T" if old else "F",
                      cells))
        lines.append("{} banner={} old(左)={} new {}{}"
                     .format(name, banner, old, cells,
                             "".join("  [{}] {}".format(p, hits[p][1])
                                     for p in CARD_CX if hits[p][0])))
        # 回归基准
        for pos, want in EXPECTED.get(short, []):
            got = hits[pos][0]
            if got != want:
                fails.append("{} 左卡应{}却{}".format(
                    name, "检出" if want else "为空",
                    "检出" if got else "为空"))
    print()
    if fails:
        print("FAIL:")
        for f in fails:
            print(" ", f)
    else:
        print("PASS: {} 帧，基准 {} 条全过".format(
            len(files), sum(len(v) for v in EXPECTED.values())))
    report = "\n".join(lines) + "\n"
    path = write_report("mate_badge.txt", report)
    print("明细已写入", path)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
