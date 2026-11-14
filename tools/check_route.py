"""路由回归：顺序遍历 vs 分类路由，赢家差异必须符合预期。

2026-11-14 阶段 2 第 4 步（切执行路径）专用。

为什么不复用 `tools/check_behavior.py`：
    那个测的是「每个 handler 单独跑」的第一个命中者，测不出**调用顺序**变化。
    路由改的是顺序（先跑分类到的 handler），所以要**同一个 task 顺序跑**
    才能测出真实差异 —— handler 之间有状态（`_picked_subject` 等）。

用法：
    python tools/check_route.py           # 打印差异
    python tools/check_route.py --gate    # 差异超出白名单就退出码 1
"""

import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402
from src.tasks.training.handlers import HANDLERS  # noqa: E402
from tools.check_handlers import DryRunTask  # noqa: E402
from tools import replay_frames  # noqa: E402

# 允许的差异：(顺序赢家, 路由赢家) → (允许帧数上限, 原因)
#
# 2026-11-14 实测（4114 帧）：
#   handle_event_choice → handle_go_out  12 帧
#       **修 bug**：周末首页（「出行」+商店通知）上，event_choice 的判据
#       先命中 → 点「训练」(1391,580)；正确动作是点「出行」(1377,748)。
#       163 帧 go_out 里 151 帧顺序遍历本来就点「出行」，只有这 12 帧被抢。
ALLOWED = {
    ("handle_event_choice", "handle_go_out"):
        (12, "修 bug：周末首页误点「训练」→ 点「出行」"),
    ("handle_match_result", "handle_result"):
        (2, "两者动作相同（都点「下一步」）"),
}


def norm(actions):
    return [list(a) if isinstance(a, (list, tuple)) else a for a in actions or []]


def run(frame, data, routed):
    """routed=True 走分类路由，False 走顺序遍历。"""
    from src.tasks.training.pages import classify_page
    from src.tasks.training import page_handlers

    texts = [Box(x=d[0], y=d[1], width=d[2], height=d[3], name=d[4],
                 confidence=1.0) for d in data]
    task = DryRunTask(frame, texts)
    if routed:
        page, _ = classify_page(task, texts)
        names = page_handlers.handlers_for(page or "")
        by_name = {h.__name__: h for h in HANDLERS}
        for n in names:
            h = by_name.get(n)
            if h is None:
                continue
            try:
                if h(task, texts):
                    return h.__name__, norm(task.actions)
            except Exception as e:                        # noqa: BLE001
                return "ERR:%s" % type(e).__name__, norm(task.actions)
    for h in HANDLERS:
        try:
            if h(task, texts):
                return h.__name__, norm(task.actions)
        except Exception as e:                            # noqa: BLE001
            return "ERR:%s" % type(e).__name__, norm(task.actions)
    return None, norm(task.actions)


def main():
    gate = "--gate" in sys.argv
    diffs = collections.Counter()
    samples = {}
    total = 0
    for tag, _path, frame, data in replay_frames.frames_with_cache():
        total += 1
        seq = run(frame, data, False)
        rt = run(frame, data, True)
        if seq[0] != rt[0] or seq[1] != rt[1]:
            diffs[(seq[0], rt[0])] += 1
            samples.setdefault((seq[0], rt[0]),
                               (tag, [d[4] for d in data], seq[1], rt[1]))

    print("帧数 %d，差异 %d 组" % (total, len(diffs)))
    ok = True
    for (a, b), n in diffs.most_common():
        limit, why = ALLOWED.get((a, b), (0, "★未登记的差异"))
        flag = "OK " if n <= limit else "超 "
        print("  %s%4d  顺序=%-26s 路由=%-26s %s"
              % (flag, n, a, b, why))
        if n > limit:
            ok = False
            tag, names, sa, ra = samples[(a, b)]
            print("        %s" % tag)
            print("        顺序动作 %s / 路由动作 %s" % (sa, ra))
            print("        %s" % (" | ".join(names))[:240])
    if gate:
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
