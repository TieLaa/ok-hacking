"""连跑 N 轮养成训练，每轮用同一个指定队长（每轮都开新局，会删档）。

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
        json.dump({"captain": captain, "allow_delete": True}, f, ensure_ascii=False)
    print(f"[runner] next_captain.json = {captain}, allow_delete=True", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captain", default=DEFAULT_CAPTAIN)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=2400)
    args = ap.parse_args()

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    for i in range(1, args.rounds + 1):
        write_captain(args.captain)
        print(f"\n===== ROUND {i}/{args.rounds} {time.strftime('%H:%M:%S')} =====", flush=True)
        code = subprocess.call(
            [sys.executable, "tools/mcp/client.py", "tools/mcp/training.py",
             "--timeout", str(args.timeout)],
            cwd=ROOT, env=env)
        print(f"[runner] round {i} exit={code} {time.strftime('%H:%M:%S')}", flush=True)
        if i < args.rounds:
            time.sleep(8)


if __name__ == "__main__":
    main()
