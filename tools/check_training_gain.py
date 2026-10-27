"""按日志统计「科目信号 → 实际属性增益」，用来校准科目评分权重。

为什么存在：
    科目评分（队友 1 / 装备 1 / 经理 2.1 / 金框 2.2 / 彩框 2.3）是拍的，
    但游戏日志里每次训练结果都带真实增量（「弹跳+13（器材+1）」），
    每次选科目也都记了当时的信号组合。把两者配对，就能用实测校准权重，
    而不是靠感觉。2026-10-27 第一次跑出：金框 +32~35 点、经理 +9~13 点、
    队友/装备接近 0（118 个样本，金框只有 16 个，置信区间很宽）。

用法：
    .venv\\Scripts\\python.exe tools\\check_training_gain.py            # 扫 logs/ 下所有 ok-script*.log
    .venv\\Scripts\\python.exe tools\\check_training_gain.py <log> ...  # 只扫指定日志

输出：
    各信号的「有/无」均值、差值；按锚点折算的建议权重；样本不足会明确写出来。

退出码：
    0 = 有可用样本；1 = 一个样本都没配对上（日志格式变了或没跑过训练）。
"""

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEL = re.compile(
    r"选科目「(?P<subj>[^」]+)」（队友 (?P<mates>\d+)｜经理 (?P<mgr>\d+)｜"
    r"装备 (?P<equip>\d+)｜满经验 (?P<full>\d+)｜金框 (?P<gold>\d+)"
    r"(?:｜彩虹 (?P<crit>\d))?")
ATTR = re.compile(r"(投射|弹跳|防守|体能|运控|意识)\s*\+\s*(\d+)")
RESULT = ("属性提升了", "精力降低了", "精力提升了")
WEEK = re.compile(r"第(\d+)周")

# 一个 1.0 分对应的属性点数：队友实测 +1.4、装备 +0.9，取均值。
POINTS_PER_SCORE = 1.15


def load(paths):
    rows, week = [], 0
    for path in paths:
        if not os.path.exists(path):
            continue
        pending = None
        for line in open(path, encoding="utf-8", errors="replace"):
            w = WEEK.search(line)
            if w:
                week = int(w.group(1))
            m = SEL.search(line)
            if m:
                pending = {k: int(m.group(k)) for k in ("mates", "mgr", "equip", "full", "gold")}
                pending.update(subj=m.group("subj"), week=week,
                               crit=int(m.group("crit")) if m.group("crit") else 0)
                continue
            if pending is None or not any(k in line for k in RESULT):
                continue
            gains = [int(v) for _, v in ATTR.findall(line)]
            if gains:
                pending["gain"] = sum(gains)
                rows.append(pending)
                pending = None
    return rows


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def main(argv):
    paths = argv[1:] or sorted(glob.glob(os.path.join(ROOT, "logs", "ok-script*.log")))
    rows = load(paths)
    if not rows:
        print("没有配对到任何样本：确认日志里有「选科目」「属性提升了」两种行")
        return 1

    print(f"日志 {len(paths)} 份｜样本 {len(rows)} 次训练")
    print(f"\n{'信号':<8}{'有':>5}{'均值':>9}{'无':>5}{'均值':>9}{'差':>9}")
    for label, key in (("经理", "mgr"), ("金框", "gold"), ("彩框", "crit"), ("满经验", "full")):
        on = [r["gain"] for r in rows if r[key]]
        off = [r["gain"] for r in rows if not r[key]]
        if not on or not off:
            print(f"{label:<8} 样本不足（有 {len(on)} / 无 {len(off)}）")
            continue
        print(f"{label:<8}{len(on):>5}{mean(on):>9.1f}{len(off):>5}{mean(off):>9.1f}"
              f"{mean(on) - mean(off):>+9.1f}")

    print(f"\n按锚点 {POINTS_PER_SCORE:.2f} 点/分折算（队友/装备各 1 分）：")
    for label, key in (("经理", "mgr"), ("金框", "gold"), ("彩框", "crit")):
        on = [r["gain"] for r in rows if r[key]]
        off = [r["gain"] for r in rows if not r[key]]
        if not on or not off:
            continue
        diff = mean(on) - mean(off)
        print(f"  {label}: {diff:+.1f} 点 → 约 {diff / POINTS_PER_SCORE:.1f} 分"
              f"（样本 {len(on)}，仅供参考）")

    print("\n提示：金框/彩框样本通常很少，滤波区间很宽；满经验若为负值说明被器材等级污染。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
