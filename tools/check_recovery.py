"""恢复路径的离线回归：用 docs/shots/ 的真实截图 + OCR 缓存验证判据。

不驱动游戏，只回答三个问题：

1. 这张图会被判成「在大厅」吗（is_lobby）？
2. 这张图会被判成「在比赛/小游戏里」吗（looks_like_match，命中就该拒绝盲点）？
3. 回大厅时能不能找到退出控件（find_exit_control），还是只能靠返回箭头？

断言（不满足直接 exit 1）：
- expected.json 里记为 handle_lobby 的图，is_lobby 必须为 True；
- 记为 handle_in_match / handle_match_intro 的图，looks_like_match 必须为 True
  （否则恢复逻辑会往比赛界面里乱点）。

用法：

    .venv\\Scripts\\python.exe tools\\check_recovery.py
    .venv\\Scripts\\python.exe tools\\check_recovery.py --verbose
"""

import glob
import json
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.BaseGirlsTask import BaseGirlsTask  # noqa: E402
from tools.check_handlers import load_boxes  # noqa: E402

SHOTS = os.path.join(ROOT, "docs", "shots")
EXPECTED = os.path.join(SHOTS, "expected.json")

# 这些 handler 命中时，恢复逻辑必须认得出「不能盲点」
MATCH_HANDLERS = {"handle_in_match", "handle_match_intro"}
LOBBY_HANDLERS = {"handle_lobby"}


class Probe:
    """只借 BaseGirlsTask 的纯判据，不构造真任务（不需要 executor）。"""

    EXIT_TEXTS = BaseGirlsTask.EXIT_TEXTS
    LOBBY_MARKS = BaseGirlsTask.LOBBY_MARKS
    NOT_LOBBY_MARKS = BaseGirlsTask.NOT_LOBBY_MARKS
    MATCH_STRONG_MARKS = BaseGirlsTask.MATCH_STRONG_MARKS
    MATCH_WEAK_MARKS = BaseGirlsTask.MATCH_WEAK_MARKS

    def __init__(self, width, height):
        self.width = width
        self.height = height

    is_lobby = BaseGirlsTask.is_lobby
    looks_like_match = BaseGirlsTask.looks_like_match
    find_exit_control = BaseGirlsTask.find_exit_control


def main():
    verbose = "--verbose" in sys.argv
    with open(EXPECTED, "r", encoding="utf-8") as f:
        expected = json.load(f)

    paths = sorted(glob.glob(os.path.join(SHOTS, "*.png")))
    if not paths:
        print("docs/shots 里没有截图")
        return 1

    ocr_holder = {"ocr": None}
    fails = []
    print(f"{'截图':<38} {'期望 handler':<24} {'大厅':<6} {'比赛':<6} 退出控件")
    print("-" * 100)
    for path in paths:
        base = os.path.basename(path)
        frame = cv2.imread(path)
        boxes = load_boxes(path, ocr_holder, use_cache=True)
        if frame is None or boxes is None:
            fails.append(f"{base}: 读图/OCR 失败")
            continue
        probe = Probe(frame.shape[1], frame.shape[0])
        lobby = probe.is_lobby(boxes)
        match = probe.looks_like_match(boxes)
        control = probe.find_exit_control(boxes)
        control_text = f"{control.name}@({control.x},{control.y})" if control else "（用返回箭头）"
        want = expected.get(base, "?")
        print(f"{base:<38} {want:<24} {str(lobby):<6} {str(match):<6} {control_text}")

        first = want.split(",")[0].strip() if want != "-" else "-"
        if first in LOBBY_HANDLERS and not lobby:
            fails.append(f"{base}: 期望是大厅，但 is_lobby=False")
        if first in MATCH_HANDLERS and not match:
            fails.append(f"{base}: 期望是比赛界面，但 looks_like_match=False"
                         "（恢复逻辑会往比赛里乱点）")
        if verbose:
            names = "/".join(t.name for t in boxes[:12])
            print(f"    OCR: {names}")

    print("-" * 100)
    if fails:
        print("恢复判据回归失败：")
        for item in fails:
            print(f"  - {item}")
        return 1
    print("恢复判据回归通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
