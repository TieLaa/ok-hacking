"""离线看科目预览的评分明细（不启动游戏）。

用法：
    .venv\\Scripts\\python.exe tools\\score_preview.py docs/shots/2026-10-26-subject-crit.png
"""

import glob
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.check_handlers import DryRunTask, load_boxes  # noqa: E402


def score_one(path, holder):
    frame = cv2.imread(path)
    if frame is None:
        print(f"{path}: 读不到图")
        return
    boxes = load_boxes(path, holder, use_cache=True)
    task = DryRunTask(frame, boxes)
    r = task.analyze_preview(boxes)
    print(f"\n== {os.path.basename(path)} ==")
    print(f"场上名字 {r['names']} → 队友 {r['mates']}｜经理 {r['manager']}")
    print(f"器材 {r['equip']}｜满经验 {r['full']}｜金框 {r['gold']}｜彩虹 {int(r['crit'])}")
    print(f"彩色暴击 {r['color']}｜经理暴击 {r['manager_crit']}")
    print(f"总分 {task.subject_score(r):.2f}")
    for line in task.logs:
        print("  log:", line)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    paths = []
    for a in args or ["docs/shots/2026-10-26-subject-crit.png"]:
        paths.extend(sorted(glob.glob(a)))
    holder = {"ocr": None}
    for p in paths:
        score_one(p, holder)


if __name__ == "__main__":
    main()
