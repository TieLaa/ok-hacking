"""页面分类器回放门禁：用**实跑存证帧**验证 `pages.py` 的锚点。

为什么要有这个（2026-11-14）
    `docs/shots/` 里的 50 张回归图是人工整理的，覆盖面窄；
    真实对局留下的存证帧（`logs/<局ID>/debug/` + `logs/mcp/<run_id>/`）
    有 **4000+ 张**，文件名自带 handler 名 = 现成的 ground truth。
    改锚点时靠这批帧回放，才能知道「认对率有没有掉」。

    2026-11-14 首次跑：认对率 38.6% → 补锚点后 95.7%。

用法
    python tools/check_pages.py             # 跑回放，打印认对率
    python tools/check_pages.py --sample    # 额外打印错配样本
    python tools/check_pages.py --min 90    # 认对率低于这个值就退出码 1

退出码：认对率低于 `--min`（默认 90）时返回 1，可以进 CI。

OCR 结果缓存在 `logs/dev/.replay_cache.json`（按 路径|大小|mtime 做键），
第一次跑要几分钟（4000 张 × ~0.3 秒），之后秒出。
"""

import collections
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
from src.tasks.training.pages import classify_page  # noqa: E402
from tools.check_handlers import DryRunTask  # noqa: E402
from tools import _ocr  # noqa: E402

CACHE = os.path.join(ROOT, "logs", "dev", ".replay_cache.json")

# 赢家 handler → 它应该在的页面（None = 兜底型/页面不唯一，不计入准确率）
EXPECT = {
    "handle_subject_select": "training_subject",
    "handle_shop": "shop",
    "handle_street": "street",
    "handle_restaurant": "restaurant",
    "handle_park": "park",
    "handle_weekend": "weekend_map",
    "handle_go_out": "go_out",
    "handle_story_arrow": "story_arrow",
    "handle_event_choice": "event_choice",
    "handle_popup": "popup",
    "handle_result": "result_page",
    "handle_training_result": "training_result",
    "handle_skill_select": "skill_select",
    "handle_training_plan": "training_plan",
    "handle_training_hub": "training_hub",
    "handle_training_day": "training_day",
    "handle_confirm_team": "confirm_team",
    "handle_note_study": "note_study",
    "handle_note_select": "note_select",
    "handle_equipment": "equipment",
    "handle_stamina_recover": "stamina_recover",
    "handle_stamina_short": "stamina_short",
    "handle_dialog_continue": "dialog_continue",
    "handle_ingredient_select": "ingredient_select",
    "handle_match_result": "match_result",
    "handle_match_intro": "match_intro",
    "handle_training_score": "training_score",
    "handle_draw": "draw",
    "handle_boost_training": "boost_training",
    "handle_prepare": "prepare",
    "handle_captain": "captain",
    "handle_captain_filter": "captain_filter",
    "handle_equipment_bonus": "equipment_bonus",
    # 兜底型 / 页面不唯一
    "handle_unmarked_cards": None,
    "handle_activity_go": None,
    "handle_tap_to_continue": None,
    "handle_vacation": None,
    "handle_rest_confirm": None,
    "handle_register_player": None,      # 注册球员/编队/战术板/奖励总览混在一起
}

# 文件名 tag 前缀 → 页面
TAG_PAGE = {
    "subject": "training_subject",
    "skill": "skill_select",
}


def imread_unicode(path):
    """读图（Windows 下路径含中文时 `cv2.imread` 会失败，走 imdecode）。"""
    with open(path, "rb") as f:
        buf = np.frombuffer(f.read(), dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def gt_of(name):
    """从文件名推 ground truth（页面 ID）。返回 (page, tag)。"""
    stem = os.path.splitext(os.path.basename(name))[0]
    parts = stem.split("_", 1)
    tag = parts[1] if len(parts) > 1 else stem
    for prefix, page in TAG_PAGE.items():
        if tag.startswith(prefix):
            return page, tag
    if tag.startswith("handle_"):
        return EXPECT.get(tag, "（未登记）"), tag
    return None, tag


def iter_shots():
    """所有实跑存证帧（同名 tag 取 mtime 最新的那份）。"""
    seen = {}
    for pattern in (os.path.join(ROOT, "logs", "*", "debug", "*.jpg"),
                    os.path.join(ROOT, "logs", "mcp", "**", "*.jpg"),
                    os.path.join(ROOT, "logs", "pre", "**", "*.jpg")):
        for p in glob.glob(pattern, recursive=True):
            key = os.path.basename(p)
            if key not in seen or os.path.getmtime(p) > os.path.getmtime(seen[key]):
                seen[key] = p
    return sorted(seen.items())


def frame_key(path):
    st = os.stat(path)
    raw = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
    return hashlib.md5(raw.encode()).hexdigest()


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def run(sample=False, min_rate=90.0):
    """跑回放。返回 (认对率, 是否达标)。"""
    cache = load_cache()
    shots = iter_shots()

    by_gt = collections.defaultdict(collections.Counter)
    wrong = collections.Counter()
    samples = {}
    new = 0

    for tag, path in shots:
        gt, htag = gt_of(tag)
        if gt is None or gt == "（未登记）":
            continue
        key = frame_key(path)
        if key not in cache:
            frame = imread_unicode(path)
            if frame is None:
                continue
            cache[key] = [[b.x, b.y, b.width, b.height, b.name]
                          for b in _ocr.boxes(frame)]
            new += 1
        data = cache[key]
        frame = imread_unicode(path)
        if frame is None:
            continue
        texts = [Box(x=d[0], y=d[1], width=d[2], height=d[3], name=d[4],
                     confidence=1.0) for d in data]
        task = DryRunTask(frame, texts)
        page, _cands = classify_page(task, texts)
        by_gt[gt][page or "unknown"] += 1
        if page != gt:
            wrong[(gt, page or "unknown")] += 1
            samples.setdefault((gt, page or "unknown"),
                               (tag, [d[4] for d in data]))

    if new:
        save_cache(cache)

    print("存证帧 %d 张（新增 OCR 缓存 %d）" % (len(shots), new))
    print()
    print("=== 每个 ground truth 的分类结果 ===")
    total = 0
    hit = 0
    for gt in sorted(by_gt, key=lambda g: -sum(by_gt[g].values())):
        tot = sum(by_gt[gt].values())
        total += tot
        hit += by_gt[gt].get(gt, 0)
        detail = "  ".join("%s=%d" % (p, n) for p, n in by_gt[gt].most_common())
        print("  %-18s %4d  (对 %d, %3.0f%%)  %s"
              % (gt, tot, by_gt[gt].get(gt, 0),
                 100 * by_gt[gt].get(gt, 0) / tot, detail))

    rate = 100 * hit / max(total, 1)
    print()
    print("总帧数 %d  认对 %d  认对率 %.1f%%（门槛 %.0f%%）"
          % (total, hit, rate, min_rate))

    if sample:
        print()
        print("=== 错配样本 ===")
        for (gt, page), n in wrong.most_common(20):
            tag, names = samples[(gt, page)]
            print("%4d  %-18s → %-18s (%s)" % (n, gt, page, tag))
            print("      %s" % (" | ".join(names))[:300])

    return rate, rate >= min_rate


def main():
    sample = "--sample" in sys.argv
    min_rate = 90.0
    if "--min" in sys.argv:
        min_rate = float(sys.argv[sys.argv.index("--min") + 1])
    ok = run(sample=sample, min_rate=min_rate)[1]
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
