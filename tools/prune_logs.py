"""按保存价值给 `logs/` 做保留：截图（jpg）按天数清，OCR 文本（txt）全留。

为什么需要（2026-11-01 实测）：`logs/` 5.19 GB / 22558 个文件，其中
    logs/unknown   5459 张 jpg = 2.16 GB   ← 同目录 5843 个 txt 只有 1.9 MB
    logs/events    3619 张 jpg = 1.48 GB
    logs/debug     1095 张 jpg = 0.64 GB
一个「没见过的界面」存证里，**真正被读的是 txt**（AGENTS.md 的硬规则：先读文本，
看不出来才看图），所以删 jpg 保 txt 能拿回 2 GB 而几乎不损失信息。

规则（按目录，理由见各条注释）：
    1. unknown：只删 jpg，txt 永久保留；超过 --days 天才删。
    2. events：报告页（tools/report_web.py）用的截图，保留最近 --days 天。
    3. debug：handler 判据的调试帧，保留最近 --days 天。
    4. mcp/<run_id>：最近 --keep-runs 次运行的 run.log / status.json 全留（那是运行
       记录的证据链），frames/ 按天数删。
    5. 总量兜底：清完仍超过 --max-gb，就从最老的 jpg 开始删到低于上限。
    6. **永不删**：docs/shots（判据回归语料）、logs/clicks.jsonl、logs/reports、
       logs/mcp/connections。

安全阀：
    - 默认 dry-run，只打印会删什么；`--apply` 才真删。
    - 检测到有 run 正在跑（任意 logs/mcp/*/status.json 的 state == running）时，
      `--apply` 直接拒绝 —— 否则会删掉正在写的帧。

用法：
    .venv\\Scripts\\python.exe tools\\prune_logs.py                 # 看能回收多少
    .venv\\Scripts\\python.exe tools\\prune_logs.py --apply         # 真删
    .venv\\Scripts\\python.exe tools\\prune_logs.py --apply --days 7 --max-gb 1.5
"""

import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")

# (目录, 保留天数, 是否保留同名 txt)
BY_AGE = [
    ("unknown", 3, True),     # 只删 jpg，txt 全留
    ("events", 2, False),
    ("debug", 3, True),
]


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
    for path in glob.glob(os.path.join(LOG_DIR, "mcp", "*", "status.json")):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            if json.load(open(path, encoding="utf-8")).get("state") == "running":
                return os.path.basename(os.path.dirname(path))
        except (OSError, ValueError):
            continue
    return None


def collect(days):
    """返回 (待删列表, 说明)。每条是 (path, size, reason)。"""
    now = time.time()
    todo = []
    for name, keep_days, keep_txt in BY_AGE:
        base = os.path.join(LOG_DIR, name)
        if not os.path.isdir(base):
            continue
        cutoff = now - keep_days * 86400
        pats = ("*.jpg", "*.jpeg", "*.png") if keep_txt else ("*",)
        for pattern in pats:
            for path in glob.glob(os.path.join(base, pattern)):
                if not os.path.isfile(path):
                    continue
                if keep_txt and not path.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                if stat.st_mtime < cutoff:
                    todo.append((path, stat.st_size, f"{name}: 超过 {keep_days} 天"))
    return todo


def collect_mcp_runs(keep_runs):
    """mcp/<run_id>/ 里只留最近 keep_runs 次的 run.log + status.json。"""
    runs = sorted(glob.glob(os.path.join(LOG_DIR, "mcp", "*", "status.json")),
                  key=os.path.getmtime, reverse=True)
    todo = []
    for path in runs[keep_runs:]:
        run_dir = os.path.dirname(path)
        for f in ("run.log", "status.json"):
            p = os.path.join(run_dir, f)
            if os.path.isfile(p):
                todo.append((p, os.path.getsize(p), f"mcp: 第 {keep_runs + 1} 次以后的旧 run"))
    return todo


def total_size():
    total = 0
    for dp, _, files in os.walk(LOG_DIR):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def enforce_cap(todo, max_bytes):
    """清完还超上限：按 jpg 的 mtime 从老到新继续删（同一次运行不会重复列出）。"""
    have = total_size() - sum(s for _, s, _ in todo)
    if have <= max_bytes:
        return []
    picked, extra = set(p for p, _, _ in todo), []
    jpgs = []
    for dp, _, files in os.walk(LOG_DIR):
        for f in files:
            if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            p = os.path.join(dp, f)
            if p in picked:
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            jpgs.append((st.st_mtime, p, st.st_size))
    jpgs.sort()
    freed = 0
    for _, p, size in jpgs:
        if have - freed <= max_bytes:
            break
        extra.append((p, size, "总量上限：最老的截图先删"))
        freed += size
    return extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的删（默认只 dry-run）")
    ap.add_argument("--days", type=int, default=None, help="统一覆盖各目录的保留天数")
    ap.add_argument("--keep-runs", type=int, default=10, help="logs/mcp 保留最近几次 run")
    ap.add_argument("--max-gb", type=float, default=1.5, help="logs/ 总量上限")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if not os.path.isdir(LOG_DIR):
        print("没有 logs 目录")
        return 0

    days = {name: (args.days if args.days is not None else d) for name, d, _ in BY_AGE}
    todo = collect(days)
    todo += collect_mcp_runs(args.keep_runs)
    todo += enforce_cap(todo, int(args.max_gb * 1024 ** 3))

    before = total_size()
    freed = sum(s for _, s, _ in todo)
    print(f"logs/ 当前 {human(before)}（{sum(len(f) for _, _, f in os.walk(LOG_DIR))} 个文件）")
    print(f"可回收 {human(freed)}，{len(todo)} 个文件")
    by_reason = {}
    for _, size, reason in todo:
        key = reason.split(":")[0]
        n, s = by_reason.get(key, (0, 0))
        by_reason[key] = (n + 1, s + size)
    for key, (n, s) in sorted(by_reason.items()):
        print(f"  {key:<10} {n:>6} 个  {human(s):>10}")
    print(f"预计清理后：{human(max(before - freed, 0))}")

    if not args.apply:
        print("\n（dry-run：加 --apply 才会真删）")
        return 0

    running = active_run()
    if running:
        print(f"\n拒绝执行：run {running} 还在跑，删帧会毁掉它的存证。跑完再来。")
        return 1

    removed = 0
    for path, _, _ in todo:
        try:
            os.remove(path)
            removed += 1
        except OSError as e:
            print(f"  删不掉 {path}: {e}")
    print(f"\n已删除 {removed} 个文件，logs/ 现在 {human(total_size())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
