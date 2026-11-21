"""准备页槽位检测的回归用例（2026-11-21）。

用 `tests/fixtures/prep/` 的真实标定帧钉住三页空/满判据：
    队友 4 槽、笔记 2 槽、器材 5 槽。
只读图像，不启动游戏、不 OCR。
"""

import os
import sys
import unittest

import cv2

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
