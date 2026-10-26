"""训练规则的纯逻辑测试（不启动游戏、不跑 OCR）。

为什么要这些测试：
    回归图只能验证「这张图命中哪个 handler」，验证不了「选哪张卡 / 点哪里」。
    2026-10-26 的两次事故（72 体力买清空扭蛋、事件二选一点中间空白）
    都属于「handler 命中了但决策错了」，必须用构造数据测。

运行：
    .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_training_rules.py" -v
"""

import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402
from tools.check_handlers import DryRunTask  # noqa: E402


def box(x, y, name, w=240, h=60):
    return Box(x=x, y=y, width=w, height=h, name=name)


class FakeTask(DryRunTask):
    """可注入品质/队友的 DryRunTask（取色逻辑被替换）。"""

    def __init__(self, texts, qualities=None, mates=None, config=None):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        super().__init__(frame, texts)
        self.config = config or {}
        self._qualities = list(qualities or [])
        self._mates = list(mates or [])

    def card_band_quality(self, cx):
        q = self._qualities.pop(0) if self._qualities else "其他"
        return q, 0, 0

    def has_mate_badge(self, cx):
        return self._mates.pop(0) if self._mates else False


def gacha_texts(cards, stamina=None, money=None, price="60"):
    """cards = [(名字, 效果), ...]，生成扭蛋页的 OCR 框。"""
    texts = [box(760, 80, "请选择1个扭蛋i", 400, 70)]
    if stamina:
        texts.append(box(380, 16, stamina, 210, 53))
    if money:
        texts.append(box(1050, 20, money, 80, 40))
    xs = [480, 960, 1440]
    for i, (name, effect) in enumerate(cards):
        texts.append(box(xs[i], 330, name, 280, 60))
        texts.append(box(xs[i], 520, effect, 280, 60))
        p = price[i] if isinstance(price, (list, tuple)) else price
        texts.append(box(xs[i], 700, p, 80, 55))
    texts.append(box(860, 950, "确定", 200, 60))
    return texts


class GachaRules(unittest.TestCase):

    def test_all_blue_should_exit_without_buying(self):
        """三张全蓝：直接退出，不能买（用户规则：只买黄紫）。"""
        task = FakeTask(gacha_texts([
            ("训练增强", "训练可以额外获得1点主属性"),
            ("防守增加", "主力防守+30"),
            ("投射增加", "主力投射+30"),
        ], stamina="72/121"), qualities=["蓝", "蓝", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        # exit_page -> (0.958w, 0.060h)
        self.assertEqual(task.actions, [("click", 1839, 64)])

    def test_clear_card_rejected_when_stamina_high(self):
        """体力 72 时，金卡但清空精力 → 拒绝，退出。"""
        task = FakeTask(gacha_texts([
            ("精力转换", "消耗所有精力，意识训练"),
            ("防守增加", "主力防守+30"),
            ("投射增加", "主力投射+30"),
        ], stamina="72/121"), qualities=["金", "蓝", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions, [("click", 1839, 64)])

    def test_clear_card_allowed_when_stamina_low(self):
        """体力 10 < 阈值 30：允许买清空卡（拿体力换属性）。"""
        task = FakeTask(gacha_texts([
            ("精力转换", "消耗所有精力，意识训练"),
            ("防守增加", "主力防守+30"),
            ("投射增加", "主力投射+30"),
        ], stamina="10/121"), qualities=["蓝", "蓝", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][0], "click")      # 点卡片
        self.assertNotEqual(task.actions[0][1], 1839)       # 不是退出

    def test_cannot_afford_should_exit(self):
        """零用钱 50、卡片 60：买不起就退出，不能点确定卡在扭蛋页。"""
        task = FakeTask(gacha_texts([
            ("训练增强", "训练可以额外获得1点主属性"),
            ("器材暴击+", "器材的暴击加成提高10%"),
            ("投射增加", "主力投射+30"),
        ], stamina="72/121", money="50", price="60"), qualities=["蓝", "紫", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions, [("click", 1839, 64)])

    def test_affordable_pick_next(self):
        """前两张买不起时，选买得起的第三张（不是直接退出）。"""
        task = FakeTask(gacha_texts([
            ("训练增强", "训练可以额外获得1点主属性"),
            ("器材暴击+", "器材的暴击加成提高10%"),
            ("投射增加", "主力投射+30"),
        ], stamina="72/121", money="60", price=["80", "80", "60"]),
            qualities=["紫", "紫", "紫"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][0], "click")
        self.assertEqual(task.actions[0][1], 1580)

    def test_purple_beats_blue(self):
        """金/紫优先，蓝卡不参与排序。"""
        task = FakeTask(gacha_texts([
            ("训练增强", "训练可以额外获得1点主属性"),
            ("器材暴击+", "器材的暴击加成提高10%"),
            ("投射增加", "主力投射+30"),
        ], stamina="72/121"), qualities=["蓝", "紫", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        # 第二张卡的卡名中心 x=960+280/2=1100
        self.assertEqual(task.actions[0][0], "click")
        self.assertEqual(task.actions[0][1], 1100)


class EventRules(unittest.TestCase):

    def test_choice_candidates_ignore_big_titles(self):
        """训练入口页的「备战 / 东部大赛」是大标题，不能当成竖向选项。"""
        texts = [
            box(505, 478, "备战", 232, 160),
            box(320, 540, "东部大赛", 602, 270),
            box(1534, 903, "启程", 134, 74),
        ]
        task = FakeTask(texts)
        self.assertEqual(task.choice_candidates(texts), [])

    def test_weekend_dialogue_not_choice(self):
        """周末首页的对话气泡（带逗号/问号/学校名）不能当选项。"""
        texts = [
            box(900, 250, "眼镜的度数又上涨了，教练", 400, 50),
            box(900, 320, "您可以陪我去配一副新的吗？", 400, 50),
            box(900, 390, "北空之丘女子高校", 400, 50),
        ]
        self.assertEqual(FakeTask(texts).choice_candidates(texts), [])

    def test_map_places_not_choice(self):
        """周末地图上的地点名是导航，不是事件选项。"""
        texts = [
            box(900, 250, "游戏厅", 200, 60),
            box(900, 350, "公园", 200, 60),
            box(900, 450, "步行街", 200, 60),
        ]
        self.assertEqual(FakeTask(texts).choice_candidates(texts), [])

    def test_weekend_visited_not_reentered(self):
        """游戏厅已经去过（没有可跳过的游戏）→ 不再进，改去公园。"""
        texts = [
            box(900, 250, "游戏厅", 200, 60),
            box(900, 350, "公园", 200, 60),
            box(900, 450, "餐厅", 200, 60),
        ]
        task = FakeTask(texts)
        task._weekend = {"date": (None, None), "visited": {"游戏厅"},
                         "shop_checked": False, "bought": False, "park_choice": None}
        self.assertTrue(task.handle_weekend(texts))
        self.assertEqual(task.actions[0], ("click_box", "公园", 1000, 380))

    def test_dialog_continue_yields_to_choice(self):
        """三选一页的「跳过」是灰的，dialog_continue 必须让位。"""
        texts = [
            box(1720, 121, "跳过", 84, 46),
            box(750, 332, "更衣室加设空衣柜", 424, 59),
            box(700, 471, "JellyRabbit毛绒玩具", 528, 65),
            box(780, 612, "了解千圣的想法", 370, 59),
        ]
        task = FakeTask(texts)
        self.assertFalse(task.handle_dialog_continue(texts))


class TopBarRules(unittest.TestCase):

    def test_zero_stamina_parsed(self):
        """0/121 必须解析成 0，不能是 None（否则公园会误判体力充足）。"""
        task = FakeTask([box(380, 16, "0/121", 210, 53)])
        self.assertEqual(task.top_bar()["stamina"], 0)

    def test_glued_week_prefix(self):
        task = FakeTask([box(380, 16, "369/115", 210, 53)])
        self.assertEqual(task.top_bar()["stamina"], 69)


class StoryArrowClicks(unittest.TestCase):
    """剧情页一次只点一下，重试交给主循环（2026-10-26 用户观察）。

    旧实现无条件连点 3 下，对话一下就能推进时，第 2、3 下会点在空处或下一层 UI 上。
    """

    DIALOG = "训练辛苦了，尝试一下最新的特殊训练吧，大家都等着你"

    def test_clicks_once_per_tick(self):
        task = FakeTask([box(400, 850, self.DIALOG, 900, 50)])
        self.assertTrue(task.handle_story_arrow(task._texts))
        self.assertEqual(len(task.actions), 1)


class StallWatchdog(unittest.TestCase):
    """5 秒看门狗：只有「界面指纹没变 + 一直没有任何点击」才算卡住。

    只看指纹会误判剧情 ▼ 连点（文字不变但点击在推进，2026-10-26 实测）。
    """

    def make(self, last_click=None):
        task = FakeTask([])
        task._last_click_at = last_click or 0.0
        return task

    def test_recent_click_is_not_stall(self):
        task = self.make(last_click=1000.0)
        self.assertFalse(task._stalled(7, 7, 5, now=1003.0))

    def test_no_click_for_five_seconds_is_stall(self):
        task = self.make(last_click=1000.0)
        self.assertTrue(task._stalled(7, 7, 5, now=1006.0))

    def test_screen_changed_is_not_stall(self):
        task = self.make(last_click=1000.0)
        self.assertFalse(task._stalled(8, 7, 5, now=1006.0))

    def test_never_clicked_is_not_stall(self):
        task = self.make()
        self.assertFalse(task._stalled(7, 7, 5, now=1006.0))

    def test_click_point_uses_box_center(self):
        task = self.make()
        self.assertEqual(task._click_point(box(100, 200, "x", 200, 60), -1), (200, 230))


if __name__ == "__main__":
    unittest.main()
