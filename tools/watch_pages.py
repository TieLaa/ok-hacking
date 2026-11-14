"""实跑监控：盯着 run.log 的页面分类 / 命中 / 异常，实时报告。

专门为「验证页面分类路由」写的（2026-11-14 阶段 2 第 4 步）：
- 统计「页面分类：X → Y」的分布，看 unknown 占比；
- 统计「命中 handle_xxx」，和分类出的页面交叉，
  找出**分类认成 A、实际却由 B 处理**的帧（路由抢错/路由没生效）；
- 抓「页面分类冲突」「异常」「卡住」这类红灯。

用法：
    python tools/watch_pages.py            # 一次性报告（读当前 run.log 全部）
    python tools/watch_pages.py --follow   # 持续跟踪（每 10 秒刷新增量）
"""

import collections
import io
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "run.log")

TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3})")
PAGE = re.compile(r"页面分类：(\S+) → (\S+?)（候选 \[([^\]]*)\]）")
HIT = re.compile(r"命中 (handle_\w+)（第 (\d+) 步）")
CONFLICT = re.compile(r"页面分类冲突：\[([^\]]*)\] → 暂取 (\S+)")
BAD = re.compile(r"(异常|停止任务|硬卡|noresp|未能识别|死循环|推不动)")


def read_lines(path=LOG):
    if not os.path.exists(path):
        return []
    return io.open(path, encoding="utf-8", errors="replace").read().splitlines()


def analyse(lines):
    """把日志行拆成「帧」：每帧 = 页面分类 + 随后的命中。"""
    page_hist = collections.Counter()
    hit_hist = collections.Counter()
    pairs = collections.Counter()
    conflicts = collections.Counter()
    bad = collections.Counter()
    pair_samples = {}

    cur_page = None
    cur_cands = ()
    for line in lines:
        m = PAGE.search(line)
        if m:
            cur_page = None if m.group(2) == "unknown" else m.group(2)
            cur_cands = tuple(c.strip().strip("'\"")
                              for c in m.group(3).split(",") if c.strip())
            page_hist[m.group(2)] += 1
            if len(cur_cands) > 1:
                conflicts[cur_cands] += 1
            continue
        m = CONFLICT.search(line)
        if m:
            conflicts[(m.group(1),)] += 1
            continue
        m = HIT.search(line)
        if m:
            hit_name = m.group(1)
            hit_hist[hit_name] += 1
            if cur_page is not None:
                pairs[(cur_page, hit_name)] += 1
                pair_samples.setdefault((cur_page, hit_name), line.strip()[:120])
            cur_page = None
            continue
        m = BAD.search(line)
        if m:
            bad[m.group(1)] += 1

    return page_hist, hit_hist, pairs, conflicts, bad, pair_samples


# 页面 → 应该处理的 handler（和 src/tasks/training/page_handlers.py 同源）
PAGE_TO_HANDLER = {
    "announcement": "handle_announcement",
    "captain": "handle_captain",
    "arcade": "handle_arcade",
    "boost_training": "handle_boost_training",
    "confirm_team": "handle_confirm_team",
    "construction": "handle_construction",
    "equipment": "handle_equipment",
    "event_choice": "handle_event_choice",
    "game_title": "handle_game_title",
    "go_out": "handle_go_out",
    "izumi_question": "handle_izumi_question",
    "lobby": "handle_lobby",
    "manuscript_submit": "handle_manuscript_submit",
    "note_study": "handle_note_study",
    "prepare": "handle_prepare",
    "rest_confirm": "handle_rest_confirm",
    "shop": "handle_shop",
    "skill_select": "handle_skill_select",
    "spin_wheel": "handle_spin_wheel",
    "stamina_recover": "handle_stamina_recover",
    "stamina_short": "handle_stamina_short",
    "stock_pick": "handle_stock_pick",
    "street": "handle_street",
    "team": "handle_team",
    "theme_select": "handle_theme_select",
    "training_entry": "handle_training_entry",
    "training_plan": "handle_training_plan",
    "training_score": "handle_training_score",
    "weekend_map": "handle_weekend",
}


def report(lines, verbose=True):
    page_hist, hit_hist, pairs, conflicts, bad, samples = analyse(lines)
    total_pages = sum(page_hist.values())
    unknown = page_hist.get("unknown", 0)
    print("=" * 70)
    print("页面分类 %d 次，unknown %d 次（%.0f%%）"
          % (total_pages, unknown, 100 * unknown / max(total_pages, 1)))
    print()
    print("--- 页面分布 ---")
    for p, n in page_hist.most_common(20):
        print("  %-18s %4d" % (p, n))

    print()
    print("--- 分类 → 实际处理（只列不一致的）---")
    mismatch = 0
    for (page, handler), n in pairs.most_common():
        want = PAGE_TO_HANDLER.get(page)
        if want is None:
            continue
        if handler == want:
            continue
        mismatch += n
        if verbose:
            print("  %4d  分类=%-16s 期望=%-24s 实际=%s"
                  % (n, page, want, handler))
    if not mismatch:
        print("  无（路由全部按预期生效）")

    print()
    print("--- 冲突 / 红灯 ---")
    if not conflicts:
        print("  无判据冲突")
    for c, n in conflicts.most_common(10):
        print("  %4d  候选=%s" % (n, list(c)))
    if not bad:
        print("  无异常关键词")
    for b, n in bad.most_common(10):
        print("  %4d  %s" % (n, b))
    print("=" * 70)
    return mismatch


def main():
    follow = "--follow" in sys.argv
    interval = 10
    if "--interval" in sys.argv:
        interval = int(sys.argv[sys.argv.index("--interval") + 1])
    if not follow:
        lines = read_lines()
        # --since 支持两种写法：'2026-11-14 20:16'（按时间）或 'tail:2000'（按行）
        if "--since" in sys.argv:
            arg = sys.argv[sys.argv.index("--since") + 1]
            if arg.startswith("tail:"):
                lines = lines[-int(arg[5:]):]
            else:
                lines = [l for l in lines if TS.match(l)
                         and TS.match(l).group(1) >= arg]
        report(lines)
        return
    # follow 模式：从当前末尾开始，只跟踪新增
    seen = len(read_lines())
    print("从第 %d 行开始跟踪（每 %d 秒刷一次）" % (seen, interval))
    while True:
        lines = read_lines()
        if len(lines) < seen:
            seen = 0            # 日志轮转
        new = lines[seen:]
        if new:
            seen = len(lines)
            print("\n[%s] 新增 %d 行" % (time.strftime("%H:%M:%S"), len(new)))
            report(new)
        time.sleep(interval)


if __name__ == "__main__":
    main()
