"""按保留期清 `logs/`：**策略在 `src/tasks/retention.py`，本脚本只管打印与执行**。

2026-11-10 归集（用户口径：「你把这些清理挂在哪里了？归集到一起行不行」）：
原来这里和 `EvidenceSink.prune()` 各写一套「什么算过期」，现在两边都调
`retention.plan()`，只是管的类别不同：

    运行时（每局开头）  kinds = retention.RUNTIME（局目录、clicks、按天日志、连接记录）
    本脚本（手动）      kinds = retention.ALL（再加平铺老目录、mcp 旧 run、总量兜底）

用法：
    .venv\\Scripts\\python.exe tools\\prune_logs.py                    # 看能回收多少
    .venv\\Scripts\\python.exe tools\\prune_logs.py --apply            # 真删
    .venv\\Scripts\\python.exe tools\\prune_logs.py --days 7 --max-gb 1.5
    .venv\\Scripts\\python.exe tools\\prune_logs.py --no-rounds        # 不动新结构的局目录

安全阀：
    - 默认 dry-run，只打印会删什么；`--apply` 才真删。
    - 有 run 正在跑（任意 logs/mcp/*/status.json 的 state == running 且文件还在写）
      时 `--apply` 直接拒绝 —— 否则会删掉正在写的帧。
"""

import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, ROOT)
from src.tasks import retention  # noqa: E402  保留策略的唯一来源


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024


def active_run(max_age_min=60):
    """有没有 run **正在**跑（有的话 --apply 必须停手）。

    光看 `state == running` 不够：runtime 被 taskkill / 崩溃时不会写终态，
    status.json 会永远停在 running（2026-11-01 撞到一条 10-30 的僵尸记录，
    把清理拦了下来）。所以再加一道「这个文件最近还在被写」的时间闸门。
    """
    cutoff = time.time() - max_age_min * 60
    for path in glob.glob(
            os.path.join(retention.LOG_ROOT, "mcp", "*", "status.json")):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            with open(path, encoding="utf-8") as f:
                if json.load(f).get("state") == "running":
                    return os.path.basename(os.path.dirname(path))
        except (OSError, ValueError):
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的删（默认只 dry-run）")
    ap.add_argument("--days", type=int, default=3,
                    help="保留天数（默认 3，与配置项「日志保留天数」一致）")
    ap.add_argument("--keep-runs", type=int, default=10, help="logs/mcp 保留最近几次 run")
    ap.add_argument("--max-gb", type=float, default=1.5, help="logs/ 总量上限")
    ap.add_argument("--no-rounds", action="store_true",
                    help="不清理新结构的整局目录（只清老的平铺目录等）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if not os.path.isdir(retention.LOG_ROOT):
        print("没有 logs 目录")
        return 0

    kinds = tuple(k for k in retention.ALL if not (args.no_rounds and k == "round"))
    actions = retention.plan(
        args.days, retention.LOG_ROOT, kinds=kinds,
        mcp_keep_runs=args.keep_runs,
        legacy_days={name: args.days for name, _d, _t in retention.LEGACY},
        max_bytes=int(args.max_gb * 1024 ** 3),
    )

    before = retention.total_size(retention.LOG_ROOT)
    freed = sum(a["size"] for a in actions)
    files = sum(len(f) for _dp, _dn, f in os.walk(retention.LOG_ROOT))
    print(f"logs/ 当前 {human(before)}（{files} 个文件）")
    print(f"可回收 {human(freed)}，{len(actions)} 项")

    grouped = {}
    for item in actions:
        n, s = grouped.get(item["reason"].split(":")[0], (0, 0))
        grouped[item["reason"].split(":")[0]] = (n + 1, s + item["size"])
    for reason, (n, s) in sorted(grouped.items(), key=lambda kv: -kv[1][1]):
        print(f"  {reason:<12} {n:>6} 项  {human(s):>10}")
    print(f"预计清理后：{human(max(before - freed, 0))}")

    if not args.apply:
        print("\n（dry-run：加 --apply 才会真删）")
        return 0

    running = active_run()
    if running:
        print(f"\n拒绝执行：run {running} 还在跑，删帧会毁掉它的存证。跑完再来。")
        return 1

    result = retention.apply(actions)
    print(f"\n已处理 {result['removed']} 项"
          f"（点击流水裁掉 {result['lines']} 行），"
          f"logs/ 现在 {human(retention.total_size(retention.LOG_ROOT))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
