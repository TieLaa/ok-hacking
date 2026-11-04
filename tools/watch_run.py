"""盯一次训练任务：按秒记录「有没有推进」，超过 N 秒没动静就留一条 stall 记录。

为什么存在（2026-10-26 用户口径）：
    「超过 5 秒没推进说明卡住，立即 debug 并继续推进」。
    任务内部现在改的是**另一条**判据（`training/flow.py: day_home_stall` ——
    休息室/校门口同一天点超过 `卡住点击上限` 才干预，见 `docs/RULES.md §14.1`）；
    这个脚本仍在**外部**按「多久没推进」观察任务，只读文件、不点鼠标、不截图，
    所以不会和任务抢输入，也不会影响游戏状态。

判定口径：
    一次「推进」= `logs/clicks.jsonl` 新增一条点击，或 `logs/ok-script.log`
    新增一行**非噪声**日志（OCR 噪声行不算，见 NOISE）。
    两边都超过 --idle 秒没动静 → 记一条 stall，并把最后一行日志带上。

产物（--out 目录）：
    timeline.log   每 30 秒一条心跳 + 所有 stall 事件 + 结束原因
    summary.json   结束时的统计（时长/点击数/ stall 次数/ 最后状态）

用法：
    python tools/watch_run.py --out logs/run_session --idle 5
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 这些日志行只是 OCR/等待噪声，不代表流程推进
NOISE = (
    "ocr_zone", "ocr result negative", "update_window_size", "StartTab",
    "DeviceManager", "hwnd_window", "gpu_driver", "Config:init",
    "removing duplicate", "Window geometry", "openvino", "FeatureSet",
    "本帧没有识别到文字", "ocr failed",
)


def read_new(path, offset):
    """从 offset 往后读新内容，返回 (新行列表, 新 offset)。"""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    if not data:
        return [], offset
    return data.decode("utf-8", "replace").splitlines(), offset + len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "run_session"))
    ap.add_argument("--idle", type=float, default=5.0)
    ap.add_argument("--heartbeat", type=float, default=30.0)
    ap.add_argument("--timeout", type=float, default=4 * 3600)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    log_path = os.path.join(ROOT, "logs", "ok-script.log")
    click_path = os.path.join(ROOT, "logs", "clicks.jsonl")
    timeline_path = os.path.join(args.out, "timeline.log")
    summary_path = os.path.join(args.out, "summary.json")

    log_off = os.path.getsize(log_path) if os.path.exists(log_path) else 0
    click_off = os.path.getsize(click_path) if os.path.exists(click_path) else 0

    started = time.time()
    last_progress = started
    last_beat = 0.0
    last_line = ""
    last_state = ""
    clicks = 0
    stalls = []
    stall_open = False
    reason = "unknown"
    finished = False

    def write(line):
        with open(timeline_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    write(f"# watch start {time.strftime('%Y-%m-%d %H:%M:%S')} idle={args.idle}s")

    while time.time() - started < args.timeout:
        now = time.time()

        new_clicks, click_off = read_new(click_path, click_off)
        for raw in new_clicks:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            clicks += 1
            last_state = f"{rec.get('state') or '-'} ({rec.get('x')},{rec.get('y')})"
            last_progress = now
            stall_open = False

        new_logs, log_off = read_new(log_path, log_off)
        for raw in new_logs:
            if any(n in raw for n in NOISE):
                continue
            last_line = raw.rstrip()
            last_progress = now
            stall_open = False
            if "task finished without ui" in raw:
                finished = True
                reason = "task finished"
            elif "达到最大操作数" in raw:
                reason = "max steps"
            elif "连续 3 轮" in raw or "已经 3 轮" in raw:
                reason = "stuck 3 rounds"
            elif "紧急停止" in raw:
                reason = "emergency stop"

        idle = now - last_progress
        if idle > args.idle and not stall_open:
            stall_open = True
            stalls.append({"at": time.strftime("%H:%M:%S"), "idle": round(idle, 1),
                           "last": last_line[-160:]})
            write(f"[{time.strftime('%H:%M:%S')}] STALL idle={idle:.1f}s 最后一行: {last_line[-160:]}")

        if now - last_beat >= args.heartbeat:
            last_beat = now
            write(f"[{time.strftime('%H:%M:%S')}] alive step_state={last_state} "
                  f"clicks={clicks} idle={idle:.1f}s stalls={len(stalls)}")

        if finished:
            break
        time.sleep(1.0)

    summary = {
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "ended": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": round(time.time() - started, 1),
        "clicks": clicks,
        "stalls": stalls,
        "stall_count": len(stalls),
        "last_state": last_state,
        "last_line": last_line[-200:],
        "reason": reason if finished else "timeout",
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    write(f"# watch end {summary['ended']} clicks={clicks} stalls={len(stalls)} reason={summary['reason']}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
