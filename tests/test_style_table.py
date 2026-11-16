"""流派登记表的值钉住（改读取点时当裁判，同 `test_shop_decision` 的思路）。

表里每个值都是从旧代码抄过来的；这里把「抄成了什么」钉住，
以后调读取点时一旦拿错行，测试先红。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.training.strategy.styles import (  # noqa: E402
    STYLES, UNKNOWN, style_spec)


class StyleTable(unittest.TestCase):
    def test_four_styles(self):
        self.assertEqual(set(STYLES), {"训练流", "商店流", "扭蛋流", "小游戏流"})

    def test_unknown_is_conservative(self):
        self.assertIs(style_spec("不在表里"), UNKNOWN)
        self.assertTrue(style_spec(None).saves_money)
        self.assertIsNone(style_spec("").weekend_main)
        # 其余两项按「非训练流」处理，与收口前的 `style != "训练流"` 等价
        self.assertTrue(style_spec("").cash_first)
        self.assertTrue(style_spec("").teammate_first)

    def test_weekend(self):
        self.assertIsNone(style_spec("训练流").weekend_main)
        self.assertEqual(style_spec("扭蛋流").weekend_main, "步行街")
        self.assertEqual(style_spec("小游戏流").weekend_main, "游戏厅")
        self.assertEqual(style_spec("商店流").weekend_plan, ("餐厅",))
        self.assertEqual(style_spec("扭蛋流").weekend_plan, ("步行街", "餐厅"))

    def test_flags(self):
        self.assertFalse(style_spec("训练流").cash_first)
        self.assertFalse(style_spec("训练流").teammate_first)
        self.assertTrue(style_spec("扭蛋流").ends_day_after_venue)
        self.assertFalse(style_spec("小游戏流").ends_day_after_venue)
        self.assertTrue(style_spec("小游戏流").saves_money)
        self.assertFalse(style_spec("商店流").saves_money)


if __name__ == "__main__":
    unittest.main()
