"""准备页槽位检测的回归用例（2026-11-21）。

用 `tests/fixtures/prep/` 的真实标定帧钉住三页空/满判据：
    队友 4 槽、笔记 2 槽、器材 5 槽。
只读图像，不启动游戏、不 OCR。
"""

import math
import os
import sys
import unittest

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.training import prep_slots  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "prep")


class PrepSlotsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.frames = {}
        for name in ("team_empty", "team_full", "note_empty", "note_full",
                     "equipment_empty", "equipment_full"):
            path = os.path.join(FIXTURES, name + ".jpg")
            frame = cv2.imread(path)
            assert frame is not None, f"缺少标定帧: {path}"
            cls.frames[name] = frame

    def test_slot_counts(self):
        self.assertEqual(len(prep_slots.slots("team")), 4)
        self.assertEqual(len(prep_slots.slots("note")), 2)
        self.assertEqual(len(prep_slots.slots("equipment")), 5)

    def test_team_empty(self):
        self.assertEqual(
            prep_slots.empty_slots(self.frames["team_empty"], "team"),
            [True] * 4)

    def test_team_full(self):
        self.assertEqual(
            prep_slots.empty_slots(self.frames["team_full"], "team"),
            [False] * 4)

    def test_note_empty(self):
        self.assertEqual(
            prep_slots.empty_slots(self.frames["note_empty"], "note"),
            [True, True])

    def test_note_full(self):
        self.assertEqual(
            prep_slots.empty_slots(self.frames["note_full"], "note"),
            [False, False])

    def test_equipment_empty(self):
        self.assertEqual(
            prep_slots.empty_slots(self.frames["equipment_empty"], "equipment"),
            [True] * 5)

    def test_equipment_full(self):
        self.assertEqual(
            prep_slots.empty_slots(self.frames["equipment_full"], "equipment"),
            [False] * 5)

    def test_equipment_star_is_not_plus(self):
        """2026-11-27 实拍：器材第 3 槽的居中方星曾被当成空位 `+`。"""
        frame = np.zeros_like(self.frames["equipment_full"])
        box = prep_slots.slots("equipment")[2]
        x, y, w, h = box
        cx, cy = x + w // 2, y + h // 2
        radius = w // 2 - 2
        points = []
        for i in range(8):
            angle = -math.pi / 2 + i * math.pi / 4
            r = radius if i % 2 == 0 else int(radius * 0.28)
            points.append((int(cx + r * math.cos(angle)),
                           int(cy + r * math.sin(angle))))
        cv2.fillPoly(frame, [np.array(points, np.int32)], (0, 200, 255))
        self.assertFalse(prep_slots.empty_slots(frame, "equipment")[2])

        # 同一位置放真 `+`，仍然要判空，别把空槽一起误伤。
        plus = np.zeros_like(frame)
        arm = max(10, int(w * 0.22))
        cv2.rectangle(plus, (cx - arm // 2, y), (cx + arm // 2, y + h),
                      (0, 200, 255), -1)
        cv2.rectangle(plus, (x, cy - arm // 2), (x + w, cy + arm // 2),
                      (0, 200, 255), -1)
        self.assertTrue(prep_slots.empty_slots(plus, "equipment")[2])

    def test_scales_to_1920x1080(self):
        """标定帧是 1548x871；游戏客户区常见 1920x1080，判据不能变。"""
        for name, page, expected in (
                ("note_empty", "note", [True, True]),
                ("note_full", "note", [False, False]),
                ("equipment_empty", "equipment", [True] * 5),
                ("equipment_full", "equipment", [False] * 5),
        ):
            frame = cv2.resize(self.frames[name], (1920, 1080))
            self.assertEqual(prep_slots.empty_slots(frame, page), expected,
                             f"{name} 放大到 1920x1080 后判据变了")

    def test_unknown_page(self):
        with self.assertRaises(KeyError):
            prep_slots.slots("unknown")


if __name__ == "__main__":
    unittest.main()
