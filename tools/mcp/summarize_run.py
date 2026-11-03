"""把一次 MCP 训练 run 的 `run.log` 数成一张表（写运行记录用）。

为什么存在：`docs/NEXT_RUN.md` 要求每轮回报「本局关键动作计数 / unknown 计数 /
是否卡死」，以前是人工在日志里翻（`logs/mcp/sento_summary.txt` 就是这么手抄的）。
这里把口径固定下来，三份 RUN_REPORT 之间才能横向比。

用法：

    .venv\\Scripts\\python.exe tools\\mcp\\summarize_run.py logs\\mcp\\<run_id>\\run.log
    .venv\\Scripts\\python.exe tools\\mcp\\summarize_run.py logs\\mcp\\<run_id>   # 目录也行

只读日志，不碰游戏、不需要 runtime。计数规则改了要同步 docs/NEXT_RUN.md。
"""

import argparse
import io
import json
import os
import re
import sys

# (标签, 正则) —— 顺序即输出顺序
COUNTERS = [
    ("训练（开始）", r"训练「[^」]+」"),
    ("休息确认", r"休息确认："),
    ("失败率过高→强制休息", r"失败率过高，强制休息"),
    ("专注不足→休息", r"→ 休息"),
    ("技能三选一", r"技能评分.*?选第 \d+ 张"),
    ("技能够确定", r"技能：确定"),
    ("科目探测（每选一次科目）", r"选科目「"),
    ("彩框命中（彩虹 1）", r"彩虹 1"),
    ("金框命中（金框 1）", r"金框 1"),
    ("商店购买", r"商店：买「"),
    ("商店确认购买", r"商店：确认购买"),
    ("扭蛋：确定", r"扭蛋：确定"),
    ("扭蛋：选卡（每次决定买）", r"扭蛋：.*→ 选「"),
    ("扭蛋：三张卡都没认出来", r"三张卡都没认出来"),
    ("扭蛋：卡片一直在变", r"卡片一直在变"),
    ("游戏厅购买", r"游戏厅：买"),
    ("周末地点（进店/进场）", r"周末地点："),
    ("事件选项", r"事件选项"),
    ("跳过比赛", r"跳过比赛"),
    ("注册球员", r"注册球员"),
    ("训练结果→点屏幕继续", r"训练结果：点屏幕继续"),
    ("卡死判定（看门狗）", r"卡死"),
    ("unknown 存证", r"存证 logs/unknown"),
]

FACT_PATTERNS = [
    ("队长选定", re.compile(r"选定队长「([^」]+)」")),
    ("队长卡（逐张读到的名字）", re.compile(r"队长卡 \d+（第\d+屏）：(.+)$")),
    ("配置指定队长", re.compile(r"本次指定队长：(.+)$")),
    ("流派", re.compile(r"流派：(\S+)")),
    ("日历队长→流派", re.compile(r"训练计划：队长「([^」]+)」→ 流派「([^」]+)」")),
    ("日期键变化", re.compile(r"日期键 (.+?)（今日行程重置）")),
    ("结果", re.compile(r"训练结果：(\{.*\})")),
]


def load(path):
    if os.path.isdir(path):
        path = os.path.join(path, "run.log")
    with open(path, encoding="utf-8", errors="replace") as f:
        return path, f.read().splitlines()


def summarize(lines):
    counts = []
    for label, pattern in COUNTERS:
        rx = re.compile(pattern)
        counts.append((label, sum(1 for line in lines if rx.search(line))))

    facts = {}
    for label, rx in FACT_PATTERNS:
        hits = [m.groups() for line in lines for m in [rx.search(line)] if m]
        if hits:
            facts[label] = hits if len(hits) > 1 else hits[0]

    first = lines[0][:8] if lines and re.match(r"\d\d:\d\d:\d\d", lines[0]) else "?"
    last = lines[-1][:8] if lines and re.match(r"\d\d:\d\d:\d\d", lines[-1]) else "?"
    return counts, facts, first, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="run.log 路径或 run 目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown 表")
    ap.add_argument("-o", "--out", help="把结果写进这个文件（UTF-8，避免 PowerShell 重定向把中文弄成乱码）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    path, lines = load(args.run)
    counts, facts, first, last = summarize(lines)

    buffer = io.StringIO()

    def emit(text=""):
        print(text)
        buffer.write(text + "\n")

    if args.json:
        emit(json.dumps({"log": path, "start": first, "end": last,
                         "counts": dict(counts), "facts": facts},
                        ensure_ascii=False, indent=2))
    else:
        emit(f"# {path}")
        emit(f"# 起 {first} → 终 {last}（{len(lines)} 行日志）\n")
        emit("| 项 | 次数 |")
        emit("|---|---|")
        for label, n in counts:
            emit(f"| {label} | {n} |")
        emit()
        for label, value in facts.items():
            text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
            emit(f"- {label}: {text[:300]}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(buffer.getvalue())
        print(f"（已写入 {args.out}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
