"""离线检查：两张 debug 帧在新判据下的走向。"""
import sys

import cv2

ROOT = r"D:\workspace\ok-hacking"
sys.path.insert(0, ROOT)

from tools.check_handlers import DryRunTask, load_boxes  # noqa: E402


def run(png, handler=None):
    frame = cv2.imread(f"{ROOT}\\docs\\shots\\{png}")
    boxes = load_boxes(f"{ROOT}\\docs\\shots\\{png}", {"ocr": None}, use_cache=True)
    task = DryRunTask(frame, boxes)
    out = handler(task, boxes)
    print(f"== {png} ==")
    for line in task.logs:
        print("  log:", line)
    print("  actions:", task.actions)
    print("  return:", out)


run("2026-10-26-park.png", lambda t, b: t.handle_park(b))
run("2026-10-26-story-choice.png", lambda t, b: t.handle_story_arrow(b))
