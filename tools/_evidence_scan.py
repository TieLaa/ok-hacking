"""扫 logs 里历史存证的 ocr 文本，挑出「顶栏星期几」那一行的坐标。

`check_topbar_day.py` 与 `check_topbar_day_e2e.py` 各抄过一份
「evidence_dirs → os.walk → 读 txt → BOX_RE 匹配 → 找配对帧图」
（2026-11-10 重复扫描抓到，连同 `BOX_RE` 也是两处各定义一次）。
"""

import os
import re

from tools._logdirs import evidence_dirs

BOX_RE = re.compile(r"^(.*?)\s*\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)\s*$")

# 星期几在顶栏左侧这块：y < 60、x 在 120~300
DAY_Y_MAX = 60
DAY_X_MIN, DAY_X_MAX = 120, 300


def is_day_box(x, y):
    return y < DAY_Y_MAX and DAY_X_MIN <= x <= DAY_X_MAX


def iter_day_lines(logs, subdirs=("unknown", "debug")):
    """产出 `(ocr 文本路径, 配对帧图路径, [(文本, x, y), …])`。

    只保留落在顶栏星期位置的框；同一张图有多行命中（新老结构都认）。
    配对帧图找不到就跳过这组（没有图没法复核）。
    """
    for base in evidence_dirs(logs, subdirs):
        if not os.path.isdir(base):
            continue
        for dp, _dn, fn in os.walk(base):
            for f in fn:
                if not f.endswith(".txt"):
                    continue
                p = os.path.join(dp, f)
                try:
                    content = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                hits = []
                for line in content.splitlines():
                    m = BOX_RE.match(line)
                    if not m:
                        continue
                    s = m.group(1).strip()
                    x, y = int(m.group(2)), int(m.group(3))
                    if is_day_box(x, y):
                        hits.append((s, x, y))
                if not hits:
                    continue
                jpg = os.path.splitext(p)[0] + ".jpg"
                if not os.path.exists(jpg):
                    jpg = os.path.join(dp, "frame.jpg")
                if not os.path.exists(jpg):
                    continue
                yield p, jpg, hits
