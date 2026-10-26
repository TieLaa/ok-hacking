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
import time
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


class AttributeValues(unittest.TestCase):
    """底部六维面板取值：OCR 会把「999/1450」拆开、把等级粘在数值前。"""

    def test_reads_all_six_values(self):
        texts = [
            box(214, 830, "投射", 100, 57), box(330, 834, "B+", 68, 55),
            box(384, 834, "501/1300", 186, 55),
            box(792, 834, "体能", 84, 51), box(883, 821, "C+354/±90", 246, 77),
            box(222, 909, "弹跳", 92, 54), box(334, 913, "A+", 62, 52),
            box(388, 917, "999", 86, 46), box(460, 919, "/1450", 104, 44),
            box(784, 907, "防守", 94, 60), box(898, 913, "B", 46, 48),
            box(946, 911, "424/1300", 188, 56),
            box(218, 987, "运控", 92, 51), box(336, 989, "B+", 62, 49),
            box(380, 986, "526/1300", 188, 59),
            box(788, 987, "意识", 88, 53), box(900, 989, "B", 46, 47),
            box(946, 989, "475/", 110, 51), box(1028, 979, "/+29", 126, 63),
        ]
        task = FakeTask(texts)
        self.assertEqual(task.read_attribute_values(texts),
                         {"投射": 501, "体能": 354, "弹跳": 999,
                          "防守": 424, "运控": 526, "意识": 475})


class SubjectRanking(unittest.TestCase):
    """科目排序：分数 → 队长推荐属性 → 数值低的 → 屏幕靠上（2026-10-26 用户口径）。"""

    def test_score_wins_first(self):
        self.assertGreater(FakeTask.subject_rank(3.0, False, 999, 900),
                           FakeTask.subject_rank(2.0, True, 100, 100))

    def test_recommended_wins_when_score_tied(self):
        self.assertGreater(FakeTask.subject_rank(2.0, True, 900, 900),
                           FakeTask.subject_rank(2.0, False, 100, 100))

    def test_lower_value_wins_when_both_tied(self):
        self.assertGreater(FakeTask.subject_rank(2.0, False, 400, 900),
                           FakeTask.subject_rank(2.0, False, 800, 300))

    def test_unknown_value_loses(self):
        self.assertGreater(FakeTask.subject_rank(2.0, False, 900, 900),
                           FakeTask.subject_rank(2.0, False, None, 100))


class WeekendMapRules(unittest.TestCase):
    """周末地图：地点都去过要退回首页，且地图不能被当成剧情页连点 ▼。"""

    MAP = [box(814, 461, "游戏厅", 148, 57), box(1538, 606, "公园", 102, 51),
           box(258, 748, "餐厅", 104, 60), box(1010, 897, "步行街", 148, 58)]

    def test_all_places_visited_exits_map(self):
        task = FakeTask(list(self.MAP))
        task.weekend_state()                      # 先固定 date，避免 handle_weekend 清空 visited
        task._weekend["visited"] = {"公园", "步行街", "游戏厅"}
        self.assertTrue(task.handle_weekend(task._texts))
        self.assertEqual(task.actions[-1][0], "click")
        self.assertGreaterEqual(task.actions[-1][1], int(task.width * 0.9))   # 右上角返回

    def test_clicking_place_does_not_mark_visited(self):
        """地图上点一下不等于进去过：visited 必须等地点的内页确认（2026-10-26）。"""
        task = FakeTask(list(self.MAP))
        task.weekend_state()
        self.assertTrue(task.handle_weekend(task._texts))
        self.assertEqual(task._weekend["visited"], set())

    def test_park_page_marks_visited(self):
        texts = [box(600, 100, "在公园干点什么好呢?", 600, 50),
                 box(700, 700, "划船", 100, 50),
                 box(900, 700, "野餐", 100, 50),
                 box(1100, 700, "散步", 100, 50)]
        task = FakeTask(texts)
        task.weekend_state()
        task.handle_park(texts)
        self.assertIn("公园", task._weekend["visited"])

    def test_map_is_not_treated_as_dialog(self):
        task = FakeTask(list(self.MAP))
        self.assertFalse(task.handle_story_arrow(task._texts))
        self.assertEqual(task.actions, [])


class EmergencyStopSmoke(unittest.TestCase):
    """F8 轮询兜底：注册失败也要能启动，ready 必须置位（旧版有竞态）。"""

    def test_ready_is_set_after_start(self):
        from src.util.emergency_stop import EmergencyStop
        stop = EmergencyStop()
        stop.ensure_started(wait=2.0)
        self.assertTrue(stop.ready.is_set())
        self.assertIsNotNone(stop._poll_thread)


class QuickMatchTaskSmoke(unittest.TestCase):
    """常驻快速比赛模块：类装配是否正确（不启动游戏）。"""

    def test_handlers_and_state_exist(self):
        from src.tasks.QuickMatchTask import QuickMatchTask
        self.assertTrue(QuickMatchTask.HANDLERS)
        for handler in QuickMatchTask.HANDLERS:
            self.assertTrue(callable(handler))
        for name in ("init_match_state", "handle_prepare", "handle_result",
                     "handle_in_match", "handle_match_intro"):
            self.assertTrue(hasattr(QuickMatchTask, name), name)

    def test_registered_as_trigger_task(self):
        from src.config import config
        modules = [m for m, _ in config["trigger_tasks"]]
        self.assertIn("src.tasks.QuickMatchTask", modules)


class MatchIntroFastForward(unittest.TestCase):
    """开场（跳球前）左下角只有一个 ▶▶：也要点，别等（用户 2026-10-26 现场截图）。"""

    LINEUP = [box(760, 300, "都立北空之丘", 300, 60),
              box(1000, 300, "崇德馆高等学校", 300, 60),
              box(700, 400, "真崎", 80, 40)]

    def test_clicks_fast_forward_during_intro(self):
        task = FakeTask(list(self.LINEUP))
        task._match_intro_at = time.monotonic()
        self.assertTrue(task.handle_in_match(task._texts))
        self.assertEqual(task.actions, [("click", 119, 1002)])

    def test_switches_manual_to_auto(self):
        texts = [box(40, 950, "手动", 80, 40)]
        task = FakeTask(texts)
        task._match_intro_at = time.monotonic()
        self.assertTrue(task.handle_in_match(texts))
        self.assertEqual(task.actions[0][0], "click_box")

    def test_auto_closes_intro_window(self):
        texts = [box(40, 950, "自动", 80, 40)]
        task = FakeTask(texts)
        task._match_intro_at = time.monotonic()
        self.assertTrue(task.handle_in_match(texts))
        self.assertEqual(task._match_intro_at, 0.0)

    def test_no_click_without_intro_or_hud(self):
        task = FakeTask(list(self.LINEUP))
        self.assertFalse(task.handle_in_match(task._texts))
        self.assertEqual(task.actions, [])

    def test_prepare_page_is_not_stolen(self):
        texts = [box(1100, 950, "前往比赛", 200, 50)]
        task = FakeTask(texts)
        task._match_intro_at = time.monotonic()
        self.assertFalse(task.handle_in_match(texts))
        self.assertEqual(task.actions, [])

    def test_nba_wipe_does_not_click(self):
        texts = [box(800, 400, "NBA", 200, 100)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_match_intro(texts))
        self.assertEqual(task.actions, [])


class BoostTrainingClicks(unittest.TestCase):
    """加强训练：按 收益×成功率 的期望值选卡，再点「确定」（2026-10-26 用户口径）。"""

    CARDS = [
        box(746, 125, "请选择一种加强训练", 426, 54),
        box(654, 256, "100%", 126, 58),
        box(1146, 256, "80%", 100, 58),
        box(674, 379, "+12", 94, 50),
        box(1152, 377, "+36", 92, 52),
        box(906, 874, "确定", 98, 51),
    ]

    def test_picks_right_card_when_ev_higher(self):
        task = FakeTask(list(self.CARDS))
        self.assertTrue(task.handle_boost_training(list(self.CARDS)))
        self.assertEqual(task.actions[0], ("click", 1196, 285))   # 80%/+36 = 28.8 > 12
        self.assertTrue(any(a[0] == "click_box" and a[1] == "确定" for a in task.actions))

    def test_picks_left_card_when_ev_higher(self):
        texts = [
            box(746, 125, "请选择一种加强训练", 426, 54),
            box(654, 256, "100%", 126, 58),
            box(1146, 256, "80%", 100, 58),
            box(674, 379, "+50", 94, 50),
            box(1152, 377, "+30", 92, 52),
            box(906, 874, "确定", 98, 51),
        ]
        task = FakeTask(texts)
        self.assertTrue(task.handle_boost_training(texts))
        self.assertEqual(task.actions[0], ("click", 717, 285))    # 100%/+50 = 50 > 24


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
