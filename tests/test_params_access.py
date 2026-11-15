"""`params` 属性式访问的回归（2026-11-15）。

`params.get("scoring.manager")` 在调用处看不见键名；属性式 `params.scoring.manager`
能被补全提示出来，减少「再写一个同义常量」的概率。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.training import params  # noqa: E402


class ParamsAttributeAccess(unittest.TestCase):
    def test_matches_get(self):
        self.assertEqual(params.rest.stamina_threshold,
                         params.get("rest.stamina_threshold"))
        self.assertEqual(params.scoring.manager,
                         params.get("scoring.manager"))
        self.assertEqual(params.weekend.work_money_gate,
                         params.get("weekend.work_money_gate"))

    def test_nested_dict_supports_get(self):
        self.assertEqual(
            params.rest.thursday_threshold_by_style.get("扭蛋流"), 60)
        self.assertIsNone(
            params.rest.thursday_threshold_by_style.get("不存在的流派"))

    def test_missing_key_raises(self):
        with self.assertRaises(AttributeError):
            _ = params.no_such_group
        with self.assertRaises(AttributeError):
            _ = params.scoring.no_such_key


if __name__ == "__main__":
    unittest.main()
