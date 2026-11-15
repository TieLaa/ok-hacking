"""量顶栏精力条的「扣减预览」段：连续抓帧，把药丸内部按颜色分段打出来。

为什么单独一个工具：`parsers._stamina_bar_measure` 只认最左边那段黄色填充，
后面的像素全丢；而游戏在科目预览时会在这里画「这次训练要扣多少」。
先把那个段的编码（颜色 / 长度 / 相对位置）量清楚，才能写判据 ——
判据不能靠猜颜色。

用法：
    python tools/probe_stamina_preview.py --minutes 40

产物：`logs/stamina_preview/<时间戳>/`
    runs.txt         颜色分段变了才写一行（附时间）
    <时间>_s<N>.jpg   分段变化时存一帧，用来人工核对是哪个科目
"""

import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.wgc_capture import find_hwnd, imwrite_auto, make_capture  # noqa: E402

# 药丸版式（与 `training/parsers.py: STAMINA_BAR_*` 同一组常量）
PILL_LEFT_F = 0.2010
PILL_WIDTH_F = 0.2885
# 竖向取样带：药丸内部，避开上下描边和光晕（1920x1080 下约 y=39~62）
BAND = (0.036, 0.058)

CATS = [
    (0, 10, "red"),
    (11, 25, "orange"),
    (26, 40, "yellow"),
    (41, 70, "green"),
    (71, 100, "cyan"),
    (101, 130, "blue"),
    (131, 160, "purple"),
    (161, 180, "magenta"),
]

MIN_PX = 3       # 一列里至少这么多像素同色才算数（容忍压在条上的数字）
MIN_SEG = 8      # 短于这个像素宽的段并进前一段（抗锯齿噪点）


def column_categories(band):
    """逐列给出主色分类名。"""
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    h = hue.astype(int)
    colored = (sat >= 110) & (val >= 120)
    masks = {}
    for lo, hi, name in CATS:
        masks[name] = colored & (h >= lo) & (h <= hi)
    masks["grey"] = (sat < 70) & (val >= 120)
    masks["dark"] = val < 90
    names = list(masks)
    counts = np.stack([masks[n].sum(axis=0) for n in names])
    idx = counts.argmax(axis=0)
    top = counts.max(axis=0)
    return [names[i] if c >= MIN_PX else "?" for i, c in zip(idx, top)]


def segments(frame):
    """药丸内部的颜色分段：[(分类, 起点 px, 宽度 px)]。"""
    h, w = frame.shape[:2]
    left = int(w * PILL_LEFT_F)
    total = int(w * PILL_WIDTH_F)
    y1, y2 = int(h * BAND[0]), int(h * BAND[1])
    band = frame[y1:y2, left:left + total]
    if band.size == 0:
        return [], []
    cats = column_categories(band)
    segs = []
    for i, name in enumerate(cats):
        if segs and segs[-1][0] == name:
            segs[-1][2] += 1
        else:
            segs.append([name, i, 1])
    merged = []
    for name, start, width in segs:
        if merged and width < MIN_SEG:
            merged[-1][2] += width
        else:
            merged.append([name, start, width])
    # 亮度剖面：整条按 20 等分，每份的 V 均值（辨识「同色但更暗」的预览段）
    val = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)[:, :, 2]
    step = max(1, val.shape[1] // 20)
    v_bins = [int(round(val[:, i:i + step].mean())) for i in range(0, val.shape[1], step)]
    return [(n, s, wd) for n, s, wd in merged], v_bins


def fmt(segs):
    return " ".join(f"{n}@{s}+{w}" for n, s, w in segs)


def main():
    minutes = 40.0
    args = sys.argv[1:]
    if "--minutes" in args:
        minutes = float(args[args.index("--minutes") + 1])

    hwnd = find_hwnd("篮球少女：飞跃巅峰")
    if not hwnd:
        raise SystemExit("找不到游戏窗口（UnityWndClass + 标题）")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join("logs", "stamina_preview", stamp)
    os.makedirs(out_dir, exist_ok=True)
    runs_path = os.path.join(out_dir, "runs.txt")
    print(f"窗口 hwnd={hwnd}｜产物 {out_dir}｜跑 {minutes} 分钟")

    cap = make_capture(hwnd)
    last = None
    last_write = 0.0
    saved = 0
    frames = 0
    heartbeat = time.monotonic()
    starving_since = None
    deadline = time.monotonic() + minutes * 60
    with open(runs_path, "w", encoding="utf-8") as out:
        try:
            while time.monotonic() < deadline:
                if cap is None:
                    hwnd = find_hwnd("篮球少女：飞跃巅峰")
                    if not hwnd:
                        time.sleep(0.5)
                        continue
                    cap = make_capture(hwnd)
                    print(f"重新接上窗口 hwnd={hwnd}", flush=True)
                frame = cap.get_frame()
                if frame is None:
                    # 游戏重启会换 hwnd：连 5 秒没帧就当断了，重新找窗口
                    if starving_since is None:
                        starving_since = time.monotonic()
                    elif time.monotonic() - starving_since > 5:
                        cap.close()
                        cap = None
                        starving_since = None
                        print("5 秒没抓到帧 → 重新找窗口", flush=True)
                    time.sleep(0.02)
                    continue
                starving_since = None
                frames += 1
                if time.monotonic() - heartbeat >= 10:
                    heartbeat = time.monotonic()
                    print(f"… {frames} 帧", flush=True)
                segs, v_bins = segments(frame)
                key = tuple((n, w // 4) for n, _, w in segs)
                now_m = time.monotonic()
                if key != last or now_m - last_write >= 1.0:
                    last = key
                    last_write = now_m
                    now = time.strftime("%H:%M:%S")
                    line = (f"{now} s={frames} {fmt(segs)}"
                            f" | v={'/'.join(str(v) for v in v_bins)}")
                    out.write(line + "\n")
                    out.flush()
                    print(line, flush=True)
                    parts = [n for n, _, _ in segs if n not in ("grey", "?")]
                    if saved < 400 and (len(parts) > 1 or "yellow" not in parts):
                        name = f"{time.strftime('%H%M%S')}_s{frames}.jpg"
                        imwrite_auto(os.path.join(out_dir, name), frame)
                        saved += 1
        except KeyboardInterrupt:
            print("手动停止")
        finally:
            if cap is not None:
                cap.close()
    print(f"结束：{frames} 帧，存图 {saved} 张 → {out_dir}")


if __name__ == "__main__":
    main()
