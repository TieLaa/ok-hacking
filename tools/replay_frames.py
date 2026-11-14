"""存证帧回放的公共设施：找帧、读图、OCR 缓存。

`tools/check_behavior.py`（比 handler 赢家+动作）和
`tools/check_route.py`（比顺序 vs 路由）都用这套，别各写一份。
"""

import glob
import hashlib
import json
import os

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "logs", "dev", ".replay_cache.json")


def imread_unicode(path):
    """读图（Windows 下路径含中文时 `cv2.imread` 会失败，走 imdecode）。"""
    with open(path, "rb") as f:
        buf = np.frombuffer(f.read(), dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def frame_key(path):
    """缓存键：路径 + 大小 + mtime（改了图自动失效）。"""
    st = os.stat(path)
    raw = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
    return hashlib.md5(raw.encode()).hexdigest()


def iter_shots():
    """所有实跑存证帧（同名 tag 取 mtime 最新的那份）。"""
    seen = {}
    for pattern in (os.path.join(ROOT, "logs", "*", "debug", "*.jpg"),
                    os.path.join(ROOT, "logs", "mcp", "**", "*.jpg")):
        for p in glob.glob(pattern, recursive=True):
            key = os.path.basename(p)
            if key not in seen or os.path.getmtime(p) > os.path.getmtime(seen[key]):
                seen[key] = p
    return sorted(seen.items())


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def frames_with_cache(cache=None):
    """产出 (tag, path, frame, boxes_data)。

    OCR 结果按 (路径|大小|mtime) 缓存在 `.replay_cache.json`，
    第一次跑要几分钟，之后秒出。
    """
    from tools import _ocr

    if cache is None:
        cache = load_cache()
    dirty = False
    for tag, path in iter_shots():
        key = frame_key(path)
        if key not in cache:
            frame = imread_unicode(path)
            if frame is None:
                continue
            cache[key] = [[b.x, b.y, b.width, b.height, b.name]
                          for b in _ocr.boxes(frame)]
            dirty = True
        frame = imread_unicode(path)
        if frame is None:
            continue
        yield tag, path, frame, cache[key]
    if dirty:
        save_cache(cache)
