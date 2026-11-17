"""公园决策的基线用例（2026-11-17）。

目的和 `test_shop_decision.py` 一样：把 `park_pick` 现在的行为钉住，
给「把判断搬进 strategy/styles.py」当裁判。**不改逻辑**；
某条用例和文档口径冲突时，先记下来，不要顺手改代码。

为什么单独一个文件：`test_training_rules.py` 是多会话共写的大文件，
搬迁时只动这个文件。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.test_training_rules import FakeTask, box  # noqa: E402


def park_texts(week=1, day="日", stamina=None, race=False, no_day=False):
    """真实布局（docs/shots/2026-10-26-park.png）：散步 / 划船 / 野餐 横排。"""
    texts = [box(760, 80, "在公园干点什么好呢?", 460, 60),
             box(430, 400, "散步", 120, 40),
             box(790, 400, "划船", 120, 40),
             box(1150, 400, "野餐", 120, 40),
             box(30, 20, f"第{week}周", 120, 40)]
    if not no_day:
        texts.append(box(200, 20, f"星期{day}", 130, 40))
    if stamina:
        texts.append(box(380, 16, stamina, 210, 53))
    if race:
        # 「划船比赛！」浮层贴在划船图标上方（实拍 y 比选项文字高约 40px）
        texts.append(box(790, 360, "划船比赛！", 170, 36))
    return texts


class ParkDecisionBaseline(unittest.TestCase):
    def pick(self, style="训练流", week=1, day="日", stamina=None, mates=None,
             race=False, no_day=False, config=None, chooser=None):
        texts = park_texts(week, day, stamina, race, no_day)
        task = FakeTask(texts, config=config)
        task._style = style
        st = task.weekend_state()
        counts = dict(mates or {})
        task.mate_face_count = lambda b, **k: counts.get(b.name.strip(), 0)
        if chooser is not None:
            st.park_choice = chooser
        return task.park_pick(texts, style, day=None if no_day else day)

    # ---------- 训练流：按精力缺口 ----------
    def test_training_gap_thresholds(self):
        """缺口 ≥ 周末野餐缺口（默认 70）→ 野餐；< 70 → 划船。"""
        self.assertEqual(self.pick(stamina="130/130"), "划船")   # 缺口 0
        self.assertEqual(self.pick(stamina="100/130"), "划船")   # 缺口 30
        self.assertEqual(self.pick(stamina="61/130"), "划船")    # 缺口 69
        self.assertEqual(self.pick(stamina="60/130"), "野餐")    # 缺口 70
        self.assertEqual(self.pick(stamina="20/130"), "野餐")    # 缺口 110

    def test_training_gap_missing_falls_back_to_old_rule(self):
        """缺口读不到 → 旧回退：周日看精力（这里也读不到 → 划船）。"""
        self.assertEqual(self.pick(stamina=None, day="日"), "划船")
        self.assertEqual(self.pick(stamina=None, day="六"), "划船")

    def test_training_choice_cached(self):
        """旧回退会写 `st.park_choice`，已有值时直接用它。"""
        self.assertEqual(self.pick(stamina=None, chooser="野餐"), "野餐")

    def test_training_last_week_always_boat(self):
        """第 7 周没有训练日，精力不再兑现 → 划船（缺口再大也一样）。"""
        self.assertEqual(self.pick(week=7, day="六", stamina="20/130"), "划船")
        self.assertEqual(self.pick(week=7, day="日", stamina="20/130"), "划船")

    def test_training_vacation_eve(self):
        """第 3 周周末：距目标 100 差 > 划船 +30 才野餐。"""
        self.assertEqual(self.pick(week=3, day="六", stamina="60/130"), "野餐")
        self.assertEqual(self.pick(week=3, day="六", stamina="80/130"), "划船")
        self.assertEqual(self.pick(week=3, day="日", stamina="60/130"), "野餐")

    # ---------- 其它流派：配置默认 + 队友 / 划船比赛 ----------
    def test_other_style_default(self):
        self.assertEqual(self.pick(style="扭蛋流", mates={}), "野餐")
        self.assertEqual(self.pick(style="扭蛋流", mates={},
                                   config={"公园选项": "划船"}), "划船")
        self.assertEqual(self.pick(style="商店流", mates={}), "野餐")

    def test_other_style_teammates(self):
        """划船比野餐多 2 个及以上 → 划船；只多 1 个不改。"""
        self.assertEqual(self.pick(style="扭蛋流", mates={"划船": 2, "野餐": 0}), "划船")
        self.assertEqual(self.pick(style="扭蛋流", mates={"划船": 3, "野餐": 1}), "划船")
        self.assertEqual(self.pick(style="扭蛋流", mates={"划船": 1, "野餐": 0}), "野餐")
        self.assertEqual(self.pick(style="扭蛋流", mates={"划船": 0, "野餐": 2}), "野餐")

    def test_other_style_boat_race_badge(self):
        """「划船比赛！」只在默认野餐时改判。"""
        self.assertEqual(self.pick(style="扭蛋流", race=True), "划船")
        self.assertEqual(self.pick(style="扭蛋流", race=True,
                                   config={"公园选项": "划船"}), "划船")
        self.assertEqual(self.pick(style="扭蛋流", race=True,
                                   config={"公园选项": "散步"}), "散步")

    def test_other_style_last_week_and_vacation(self):
        self.assertEqual(self.pick(style="扭蛋流", week=7, day="六"), "划船")
        self.assertEqual(self.pick(style="扭蛋流", week=3, day="六",
                                   stamina="60/130"), "野餐")
        self.assertEqual(self.pick(style="扭蛋流", week=3, day="六",
                                   stamina="80/130"), "划船")

    # ---------- 日期来源不一致（搬迁时最容易改坏的一条） ----------
    def test_week_readable_but_day_missing(self):
        """顶栏只读到周、读不到星期：

        - `current_day()` 返回 (None, None) → 海边前规则不触发（没有完整日期）；
        - `top_bar().get("week")` 仍能读到 7 → 最后一周规则照常触发 → 划船。
        这两条用的是**不同来源**，搬迁时不能合并成一个 week。
        """
        self.assertEqual(self.pick(week=7, day="六", stamina="20/130",
                                   no_day=True), "划船")

    def test_day_missing_on_normal_week(self):
        """普通周、只有周数：没有完整日期 → 训练流按缺口走。"""
        self.assertEqual(self.pick(week=1, day="六", stamina="20/130",
                                   no_day=True), "野餐")
        self.assertEqual(self.pick(week=1, day="六", stamina="100/130",
                                   no_day=True), "划船")


if __name__ == "__main__":
    unittest.main()
