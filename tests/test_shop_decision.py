"""商店决策的基线用例（2026-11-15）。

只做一件事：把 `pick_shop_item` 当前的行为钉住，供谓词化重写时当裁判。
**不改逻辑**；如果某条用例和文档口径冲突，先记下来，不要顺手改代码。

为什么单独一个文件：`tests/test_training_rules.py` 是多会话共写的大文件，
这套决策表放在这里，谓词化时只动这个文件。
"""

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.training.places.shop import ShopPlace  # noqa: E402

POLICY = "红包→彩→金→紫"


def item(name, price, quality="金", y=300):
    return {"name": name, "price": price, "quality": quality, "y": y}


class ShopTask(ShopPlace):
    """只实现 `pick_shop_item` 需要的最小环境（不启动游戏、不 OCR）。"""

    def __init__(self, style="训练流", day=(8, "一")):
        self._style = style
        self._day = SimpleNamespace(date=day)
        self.config = {}
        self.logs = []

    @property
    def style(self):
        return self._style

    @property
    def style_known(self):
        return True

    def current_day(self, texts=None):
        return self._day.date

    def log_info(self, msg):
        self.logs.append(msg)


class ShopPageShopTask(ShopTask):
    """模拟商店内页：顶栏没有「第N周 星期X」，`current_day` 读不到。"""

    def current_day(self, texts=None):
        return (None, None)


class ShopDecisionBaseline(unittest.TestCase):
    def pick(self, items, money=300, week=6, day="一", stamina=None,
             stamina_max=None, style="训练流", policy=POLICY, **kw):
        task = ShopTask(style=style, day=(week, day))
        pick, _reason = task.pick_shop_item(
            items, money, policy, stamina=stamina, stamina_max=stamina_max, **kw)
        return pick["name"] if pick else None

    # ---------- 现金必拿 ----------
    def test_cash_free_first(self):
        self.assertEqual(self.pick(
            [item("老板的馈赠", 0), item("器材手册+", 100)], money=0),
            "老板的馈赠")

    def test_cash_free_even_when_money_unknown(self):
        self.assertEqual(self.pick(
            [item("老板的馈赠", 0), item("器材手册+", 100)], money=None),
            "老板的馈赠")

    def test_cash_paid_when_affordable(self):
        self.assertEqual(self.pick(
            [item("大乐透", 60), item("器材手册+", 100)], money=60),
            "大乐透")

    def test_cash_paid_not_bought_when_poor(self):
        self.assertIsNone(self.pick([item("大乐透", 60)], money=50))

    # ---------- 第 7 周前后 ----------
    def test_gacha_style_before_week7_only_takes_cash(self):
        # 扭蛋流 / 小游戏流前 6 周零花钱留给周末：连 0 元商品都不买，只拿现金必拿。
        self.assertIsNone(self.pick(
            [item("器材手册+", 100), item("赠品", 0, "蓝")],
            week=3, style="扭蛋流"))

    def test_training_style_buys_paid_before_week7(self):
        # 训练流没有「留钱给周末」这条，进店就按顺序买。
        self.assertEqual(self.pick(
            [item("器材手册+", 100)], week=3), "器材手册+")

    def test_gacha_style_before_week7_on_shop_page_without_date(self):
        # 商店内页顶栏没有日期：current_day 读不到，沿用当天状态的日期。
        task = ShopPageShopTask(style="扭蛋流", day=(1, "一"))
        pick, _reason = task.pick_shop_item(
            [item("器材指导教程", 120)], 250, POLICY)
        self.assertIsNone(pick)

    def test_training_style_on_shop_page_without_date(self):
        # 同一页训练流不受「留钱」限制，仍按顺序买。
        task = ShopPageShopTask(style="训练流", day=(1, "一"))
        pick, _reason = task.pick_shop_item(
            [item("器材指导教程", 120)], 250, POLICY)
        self.assertEqual(pick["name"], "器材指导教程")

    # ---------- 最后一天之前留 120 给扭蛋（2026-11-15 用户口径） ----------
    def test_keeps_120_for_gacha_before_last_day(self):
        # 149 块买 60 的卡，买完只剩 89 < 120 → 不买
        self.assertIsNone(self.pick([item("投射秘籍", 60)], money=149, week=6))

    def test_spends_only_above_the_floor(self):
        # 200 块买 60，剩 140 ≥ 120 → 买
        self.assertEqual(self.pick([item("投射秘籍", 60)], money=200, week=6),
                         "投射秘籍")

    def test_last_day_ignores_the_floor(self):
        # 第 7 周周日是最后一天：149 也照样买
        self.assertEqual(self.pick([item("投射秘籍", 60)], money=149,
                                   week=7, day="日"), "投射秘籍")

    def test_floor_does_not_block_free_items(self):
        self.assertEqual(self.pick([item("赠品", 0, "蓝")], money=30, week=6),
                         "赠品")

    def test_floor_is_strategy_param(self):
        from src.tasks.training import params
        self.assertEqual(
            int(params.get("shop.keep_money_before_last_day")), 120)

    # ---------- 品质与排序 ----------
    def test_quality_color_first(self):
        self.assertEqual(self.pick(
            [item("奇妙投射秘籍", 60, "紫"),
             item("器材手册+", 100, "金"),
             item("全能饮料+", 120, "彩")]), "全能饮料+")

    def test_same_quality_prefers_larger_value(self):
        self.assertEqual(self.pick(
            [item("全能饮料+", 110, "金"), item("全能饮料++", 150, "金")]),
            "全能饮料++")

    def test_blue_never_bought(self):
        self.assertIsNone(self.pick([item("投射教科书", 100, "蓝")]))

    def test_unknown_money_does_not_buy_paid(self):
        self.assertIsNone(self.pick([item("器材手册+", 100)], money=None))

    def test_free_ignores_money(self):
        self.assertEqual(self.pick([item("赠品", 0)], money=0), "赠品")

    def test_free_negative_drink_now_buyable(self):
        # 2026-11-24：苦味/怪味不再在永不买名单；0 元免费照拿（不触发精力门槛）
        self.assertEqual(self.pick([item("苦味饮料", 0)]), "苦味饮料")

    # ---------- 最后一周 / 最后一场 ----------
    def test_final_week_no_milk(self):
        self.assertIsNone(self.pick([item("特训牛奶", 50)], week=7, day="三"))

    def test_final_week_attribute_still_ok(self):
        self.assertEqual(self.pick(
            [item("全能饮料+", 100)], week=7, day="三"), "全能饮料+")

    def test_final_match_blue_attribute_allowed(self):
        self.assertEqual(self.pick(
            [item("全能饮料+", 100, "蓝")], week=7, day="日"), "全能饮料+")

    # ---------- 精力商品 ----------
    def test_stamina_overflow_rejected(self):
        self.assertIsNone(self.pick(
            [item("精力饮料++", 50)], stamina=120, stamina_max=130))

    def test_stamina_ok_when_room(self):
        self.assertEqual(self.pick(
            [item("精力饮料++", 50)], stamina=50, stamina_max=130),
            "精力饮料++")

    def test_stamina_group_before_other_goods(self):
        """2026-11-11 起：现金 → 牛奶 → 精力 → 其余（按品质）。

        注意：2026-11-07 的「恢复精力垫底」已被这条管道取代，
        精力类现在排在普通器材/教科书之前。
        """
        self.assertEqual(self.pick(
            [item("精力饮料++", 50), item("器材手册+", 100)],
            stamina=50, stamina_max=130), "精力饮料++")

    # ---------- 总开关 ----------
    def test_policy_buy_nothing(self):
        self.assertIsNone(self.pick(
            [item("老板的馈赠", 0)], policy="不买"))


if __name__ == "__main__":
    unittest.main()
