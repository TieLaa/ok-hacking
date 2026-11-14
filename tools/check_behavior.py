"""实跑存证帧的 handler 行为基线：改代码前后对比「赢家 + 动作」。

比 `logs/dev/probe_handlers.py`（50 张静态回归图）覆盖面广得多 ——
这里是 4000+ 张真实对局帧。

用法：
    python tools/check_behavior.py baseline   # 存基线
    python tools/check_behavior.py compare   # 对比（不一致时退出码 1）

OCR 结果复用 `logs/dev/.replay_cache.json`（和 tools/check_pages.py 同一份），
第一次跑要几分钟，之后秒出。
"""

import glob
import hashlib
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402
from src.tasks.training.handlers import HANDLERS  # noqa: E402
from tools.check_handlers import DryRunTask  # noqa: E402
from tools import _ocr  # noqa: E402

CACHE = os.path.join(ROOT, "logs", "dev", ".replay_cache.json")
BASELINE = os.path.join(ROOT, "logs", "dev", ".behavior_baseline.json")


def imread_unicode(path):
    with open(path, "rb") as f:
        buf = np.frombuffer(f.read(), dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def frame_key(path):
    st = os.stat(path)
    raw = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
    return hashlib.md5(raw.encode()).hexdigest()


def iter_shots():
    seen = {}
    for pattern in (os.path.join(ROOT, "logs", "*", "debug", "*.jpg"),
                    os.path.join(ROOT, "logs", "mcp", "**", "*.jpg")):
        for p in glob.glob(pattern, recursive=True):
            key = os.path.basename(p)
            if key not in seen or os.path.getmtime(p) > os.path.getmtime(seen[key]):
                seen[key] = p
    return sorted(seen.items())


def norm(actions):
    out = []
    for a in actions or []:
        if isinstance(a, str):
            out.append(a)
        elif isinstance(a, (list, tuple)):
            out.append(list(a))
    return out


def probe_all():
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    out = {}
    shots = iter_shots()
    for i, (tag, path) in enumerate(shots):
        key = frame_key(path)
        if key not in cache:
            frame = imread_unicode(path)
            if frame is None:
                continue
            cache[key] = [[b.x, b.y, b.width, b.height, b.name]
                          for b in _ocr.boxes(frame)]
            with open(CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        data = cache[key]
        frame = imread_unicode(path)
        if frame is None:
            continue
        hits = []
        for handler in HANDLERS:
            texts = [Box(x=d[0], y=d[1], width=d[2], height=d[3], name=d[4],
                         confidence=1.0) for d in data]
            task = DryRunTask(frame, texts)
            try:
                if handler(task, texts):
                    hits.append([handler.__name__, norm(task.actions)])
            except Exception as e:                       # noqa: BLE001
                hits.append([handler.__name__, "ERR:%s" % type(e).__name__])
        out[tag] = hits
        if (i + 1) % 500 == 0:
            print("  ... %d/%d" % (i + 1, len(shots)))
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "compare"
    data = probe_all()
    print("帧数:", len(data))
    if mode == "baseline":
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print("baseline saved")
        return
    if not os.path.exists(BASELINE):
        print("no baseline; run 'baseline' first")
        return
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    diffs = 0
    for name, hits in data.items():
        b = base.get(name)
        if b is None:
            print("NEW", name)
            continue
        bw = b[0][0] if b else None
        dw = hits[0][0] if hits else None
        ba = b[0][1] if b else None
        da = hits[0][1] if hits else None
        if bw != dw or ba != da:
            diffs += 1
            if diffs <= 20:
                print("DIFF", name)
                print("   before:", bw, ba)
                print("   after :", dw, da)
    print("total", len(data), "diffs", diffs)
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
