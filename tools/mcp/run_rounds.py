"""连跑 N 轮养成训练，每轮用同一个指定队长。

⚠️ **不再自动删档**（2026-11-02 用户口径）：开训练消耗的专注在删档时
不返还，脚本只会在「训练确认」弹窗点「继续训练」。所以要连跑多轮，
每轮结束后必须**人工**在大厅开新局 —— 本脚本只能保证队长和轮次切换，
保证不了存档是新的。不做人工开新局时，后续「轮次」实际都跑同一个存档。

为什么要这个脚本而不是在 shell 里拼命令：队长名是中文，PowerShell 5.1 读
UTF-8 无 BOM 的脚本会把中文当 ANSI 弄成乱码，写进 next_captain.json 的队长名
就匹配不上（2026-10-30 实测）。中文只出现在这里（UTF-8 源码），由 Python 写文件。

用法：
    .venv\\Scripts\\python.exe tools\\mcp\\run_rounds.py            # 默认 2 轮佐久间雨乃
    .venv\\Scripts\\python.exe tools\\mcp\\run_rounds.py --rounds 1
"""

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAPTAIN_FILE = os.path.join(ROOT, "logs", "mcp", "next_captain.json")
DEFAULT_CAPTAIN = "佐久间雨乃"


def write_captain(captain):
    os.makedirs(os.path.dirname(CAPTAIN_FILE), exist_ok=True)
    with open(CAPTAIN_FILE, "w", encoding="utf-8") as f:
        json.dump({"captain": captain}, f, ensure_ascii=False)
    print(f"[runner] next_captain.json = {captain}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captain", default=DEFAULT_CAPTAIN)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=2400)
    args = ap.parse_args()

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    log_path = os.path.join(ROOT, "logs", "mcp", "run_rounds.log")
    with open(log_path, "a", encoding="utf-8") as log:
        for i in range(1, args.rounds + 1):
            write_captain(args.captain)
            head = f"\n===== ROUND {i}/{args.rounds} {time.strftime('%m-%d %H:%M:%S')} =====\n"
            print(head.strip(), flush=True)
            log.write(head)
            log.flush()
            code = subprocess.call(
                [sys.executable, "tools/mcp/client.py", "tools/mcp/training.py",
                 "--timeout", str(args.timeout)],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            tail = f"[runner] round {i} exit={code} {time.strftime('%H:%M:%S')}\n"
            print(tail.strip(), flush=True)
            log.write(tail)
            log.flush()
            if i < args.rounds:
                time.sleep(8)


if __name__ == "__main__":
    main()
