"""离线自测：周末地图的队友气泡判据（用 logs/debug 里的真实帧 + OCR 坐标）。

期望：游戏厅/餐厅/步行街 有气泡 → True；公园 没气泡 → False。
"""

import sys

import cv2

ROOT = __file__.rsplit("\\tools\\", 1)[0]
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402
from src.tasks.BaseGirlsTask import BaseGirlsTask  # noqa: E402


class Probe:
    MATE_BUBBLE_MIN_FRACTION = BaseGirlsTask.MATE_BUBBLE_MIN_FRACTION
    MATE_REGION_DX = BaseGirlsTask.MATE_REGION_DX
    MATE_REGION_UP_TOP = BaseGirlsTask.MATE_REGION_UP_TOP
    MATE_REGION_UP_BOTTOM = BaseGirlsTask.MATE_REGION_UP_BOTTOM
    venue_has_mate = BaseGirlsTask.venue_has_mate
    frame = None
    _tick_frame = None


def main():
    folder = r"logs\debug\1026-184525_nochange3"
    frame = cv2.imread(folder + r"\frame.jpg")
    if frame is None:
        print("读不到标定帧")
        return 1
    boxes = []
    with open(folder + r"\ocr.txt", encoding="utf-8") as f:
        for line in f:
            name, coords = line.rsplit("\t", 1)
            x, y, w, h = [int(v) for v in coords.strip().strip("()").split(",")]
            boxes.append(Box(x=x, y=y, width=w, height=h, name=name.strip()))

    probe = Probe()
    expect = {"游戏厅": True, "餐厅": True, "步行街": True, "公园": False}
    failed = []
    for name, want in expect.items():
        box = next((b for b in boxes if b.name.strip() == name), None)
        got = probe.venue_has_mate(box, frame) if box else None
        status = "OK" if got == want else "MISMATCH"
        print(f"{name}: got={got} want={want} {status}")
        if got != want:
            failed.append(name)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
