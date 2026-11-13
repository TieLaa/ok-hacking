"""让路闸门：动作区间之外的输入必须算用户的。

2026-11-13 用户反馈「我操作的时候鼠标依然被抢」：旧 `mark_script_input` 在动作
结束后 20ms 读 `GetLastInputInfo`，把用户在这段时间的输入无条件认成脚本自己的，
下一拍 `wait_for_user_idle` 读到同一个 tick → 直接放行。持续操作时鼠标事件约
16ms 一个，这个窗口几乎必然命中。

这组测试锁住三条契约：
  1. 动作结束之后才出现的输入，不许认领；
  2. 动作区间内的输入照常认领（否则脚本自己的点击会让下一次白等 3 秒）；
  3. `_action_started_at` 只在 `_ready_to_act` 一处记，所有动作共用。
"""

import unittest
from unittest import mock

from src.interaction import GirlsInteraction as mod
from src.interaction.GirlsInteraction import GirlsInteraction


class _Fake(GirlsInteraction):
    """绕过 `__init__`（不碰 pywin32 capture），只测让路记账。"""

    def __init__(self):
        self.user_idle_wait = 3.0
        self._script_input_tick = None
        self._action_started_at = None
        self.steal_count = 0
        self.abort_check = None
        self._stop_requested = False
        self._ticks = []
        self._now = 0

    def _last_input_tick(self):
        return self._ticks.pop(0) if self._ticks else None

    def _now_tick(self):
        return self._now


class MarkScriptInput(unittest.TestCase):
    def test_input_after_action_is_not_claimed(self):
        """动作结束后用户才动：不许认领，否则下一次直接抢。"""
        task = _Fake()
        task._action_started_at = 1000
        task._now = 1200                 # 动作结束于 1200
        task._ticks = [1300]             # 用户在 1200 之后动
        task.mark_script_input(settle=0)
        self.assertIsNone(task._script_input_tick)

    def test_input_inside_action_is_claimed(self):
        """动作区间内的输入仍是脚本自己的（否则每一次都白等 3 秒）。"""
        task = _Fake()
        task._action_started_at = 1000
        task._now = 1200
        task._ticks = [1100]
        task.mark_script_input(settle=0)
        self.assertEqual(task._script_input_tick, 1100)

    def test_no_since_keeps_legacy_behavior(self):
        """外部直接调（不经过 `_ready_to_act`）时退回旧行为。"""
        task = _Fake()
        task._action_started_at = None
        task._now = 1200
        task._ticks = [1300]
        task.mark_script_input(settle=0)
        self.assertEqual(task._script_input_tick, 1300)


class WaitForUserIdle(unittest.TestCase):
    def test_wait_when_user_input_appears(self):
        task = _Fake()
        task.user_idle_wait = 0.05
        task._script_input_tick = 1000
        task._ticks = [1300]             # 用户在我上次动作之后又动了
        with mock.patch.object(mod.time, "sleep", lambda *_: None), \
                mock.patch.object(task, "_idle_seconds", return_value=9.0):
            waited = task.wait_for_user_idle()
        self.assertTrue(waited)
        self.assertEqual(task.steal_count, 1)

    def test_no_wait_when_tick_unchanged(self):
        task = _Fake()
        task._script_input_tick = 1000
        task._ticks = [1000]
        self.assertFalse(task.wait_for_user_idle())


class ReadyToAct(unittest.TestCase):
    def test_records_action_start(self):
        task = _Fake()
        task._now = 555
        with mock.patch.object(task, "wait_for_user_idle", return_value=False), \
                mock.patch.object(task, "ensure_foreground", return_value=True):
            self.assertTrue(task._ready_to_act())
        self.assertEqual(task._action_started_at, 555)

    def test_need_foreground_false_does_not_steal(self):
        """input_text 只发键盘，不许为了打字去抢前台。"""
        task = _Fake()
        task._now = 777
        with mock.patch.object(task, "wait_for_user_idle", return_value=False), \
                mock.patch.object(task, "ensure_foreground",
                                  side_effect=AssertionError("不该抢前台")):
            self.assertTrue(task._ready_to_act(need_foreground=False))
        self.assertEqual(task._action_started_at, 777)


if __name__ == "__main__":
    unittest.main()
