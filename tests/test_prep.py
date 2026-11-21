"""准备页决策层的单元测试（2026-11-21）。

不启动游戏、不 OCR：只钉住「模式 × 首局/后续局 × 空/满 × MCP」的决策表。
"""

import os
import sys
import unittest

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.training import prep  # noqa: E402
from src.tasks.training.handlers.entry import EntryHandlers  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "prep")


class FakeTask:
    def __init__(self, mode=prep.MODE_AUTO, round_no=1, mcp=False):
        self.config = {prep.KEY: mode}
        self._round_no = round_no
        self.mcp_driven = mcp
        self.logs = []

    def log_info(self, message, notify=False):
        self.logs.append(message)


class PrepDecisionTest(unittest.TestCase):

    def decide(self, mode=prep.MODE_AUTO, round_no=1, empty=(True,), mcp=False):
        return prep.decide(
            FakeTask(mode, round_no, mcp), "team", list(empty) if empty is not None else None)

    # ---------- 自动选择 ----------
    def test_auto_first_round_always_clicks(self):
        self.assertEqual(self.decide(round_no=1, empty=(True, False)), "auto")
        self.assertEqual(self.decide(round_no=1, empty=(False, False)), "auto")

    def test_auto_later_round(self):
        self.assertEqual(self.decide(round_no=2, empty=(False, True)), "auto")
        self.assertEqual(self.decide(round_no=2, empty=(False, False)), "next")
        self.assertEqual(self.decide(round_no=2, empty=None), "auto")

    # ---------- 半自动 ----------
    def test_semi_waits_on_empty_or_unknown(self):
        self.assertEqual(self.decide(prep.MODE_SEMI, empty=(True,)), "wait")
        self.assertEqual(self.decide(prep.MODE_SEMI, empty=None), "wait")

    def test_semi_advances_when_full(self):
        self.assertEqual(self.decide(prep.MODE_SEMI, empty=(False, False)), "next")

    def test_semi_is_same_first_and_later(self):
        for round_no in (1, 2, 5):
            self.assertEqual(
                self.decide(prep.MODE_SEMI, round_no=round_no, empty=(True,)),
                "wait")

    # ---------- 预配置（阶段 1 占位） ----------
    def test_preset_placeholder_behaves_like_auto(self):
        self.assertEqual(self.decide(prep.MODE_PRESET, round_no=1, empty=(True,)), "auto")
        self.assertEqual(self.decide(prep.MODE_PRESET, round_no=2, empty=(True,)), "auto")
        self.assertEqual(self.decide(prep.MODE_PRESET, round_no=2, empty=(False,)), "next")

    # ---------- MCP 覆盖 ----------
    def test_mcp_never_waits(self):
        self.assertEqual(self.decide(prep.MODE_SEMI, empty=(True,), mcp=True), "auto")
        self.assertEqual(self.decide(prep.MODE_SEMI, empty=(False,), mcp=True), "next")
        self.assertEqual(self.decide(prep.MODE_SEMI, empty=None, mcp=True), "auto")
        self.assertEqual(self.decide(prep.MODE_PRESET, empty=(True,), mcp=True), "auto")

    # ---------- 配置兜底 ----------
    def test_unknown_mode_falls_back_to_auto(self):
        self.assertEqual(prep.mode(FakeTask("不存在")), prep.MODE_AUTO)
        self.assertEqual(prep.mode(FakeTask("")), prep.MODE_AUTO)

    def test_legacy_alias(self):
        self.assertEqual(prep.mode(FakeTask("全自动")), prep.MODE_AUTO)


class PrepActionTask(EntryHandlers):
    """只给 `prep_action` 用的最小任务：不点击、不 OCR。"""

    def __init__(self, frame, mode=prep.MODE_AUTO, round_no=1):
        self.config = {prep.KEY: mode}
        self._round_no = round_no
        self._tick_frame = frame
        self.info = {}
        self.logs = []

    def log_info(self, message, notify=False):
        self.logs.append(message)

    def log_warning(self, message, notify=False):
        self.logs.append(message)


class PrepActionTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.frames = {}
        for name in ("team_empty", "team_full", "note_empty", "note_full",
                     "equipment_empty", "equipment_full"):
            cls.frames[name] = cv2.imread(os.path.join(FIXTURES, name + ".jpg"))

    def test_semi_note_empty_waits(self):
        task = PrepActionTask(self.frames["note_empty"], prep.MODE_SEMI)
        self.assertEqual(task.prep_action("note"), "wait")
        self.assertEqual(prep.waiting_page(task), "note")
        self.assertEqual(task.info.get("prep_wait"), "选择笔记")

    def test_semi_note_full_next_and_clears(self):
        task = PrepActionTask(self.frames["note_full"], prep.MODE_SEMI)
        prep.begin_wait(task, "note")
        self.assertEqual(task.prep_action("note"), "next")
        self.assertIsNone(prep.waiting_page(task))
        self.assertNotIn("prep_wait", task.info)

    def test_auto_first_round_team_empty_auto(self):
        task = PrepActionTask(self.frames["team_empty"], prep.MODE_AUTO, 1)
        self.assertEqual(task.prep_action("team"), "auto")

    def test_auto_later_round_equipment(self):
        task = PrepActionTask(self.frames["equipment_empty"], prep.MODE_AUTO, 2)
        self.assertEqual(task.prep_action("equipment"), "auto")
        task = PrepActionTask(self.frames["equipment_full"], prep.MODE_AUTO, 2)
        self.assertEqual(task.prep_action("equipment"), "next")


class PrepWaitTest(unittest.TestCase):

    def test_begin_wait_logs_once(self):
        task = FakeTask()
        prep.begin_wait(task, "note")
        prep.begin_wait(task, "note")
        self.assertEqual(prep.waiting_page(task), "note")
        self.assertEqual(len(task.logs), 1)

    def test_switch_page_logs_again(self):
        task = FakeTask()
        prep.begin_wait(task, "team")
        prep.begin_wait(task, "equipment")
        self.assertEqual(prep.waiting_page(task), "equipment")
        self.assertEqual(len(task.logs), 2)

    def test_clear_wait(self):
        task = FakeTask()
        prep.begin_wait(task, "team")
        self.assertEqual(prep.clear_wait(task), "team")
        self.assertIsNone(prep.waiting_page(task))
        self.assertIsNone(prep.clear_wait(task))

    def test_heartbeat(self):
        task = FakeTask()
        state = prep.begin_wait(task, "team")
        self.assertFalse(prep.wait_heartbeat(task, now=state["since"] + 10))
        self.assertTrue(prep.wait_heartbeat(task, now=state["since"] + 31))
        self.assertFalse(prep.wait_heartbeat(task, now=state["since"] + 40))
        self.assertTrue(prep.wait_heartbeat(task, now=state["since"] + 65))


if __name__ == "__main__":
    unittest.main()
