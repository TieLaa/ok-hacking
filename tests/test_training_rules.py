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
        self.ocr_calls = 0
        self.ocr_queue = []          # 依次弹出的「下一帧」OCR 结果（模拟动画）

    def _ocr_region(self, region_name=None, box=None):
        self.ocr_calls += 1
        if self.ocr_queue:
            return self.ocr_queue.pop(0)
        return super()._ocr_region(region_name, box)

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

    def test_gacha_waits_for_open_animation(self):
        """2026-10-27 用户反馈：第一次进步行街会先播扭蛋开场动画。

        动画期间 OCR 只有街道招牌，读不到卡；旧代码立刻退出，白丢一次扭蛋。
        现在必须原地重试 3 次（约 2 秒）再决定退出。
        """
        texts = [box(760, 80, "请选择1个扭蛋i", 400, 70),
                 box(640, 950, "确定", 120, 60),
                 box(30, 990, "能力详情", 150, 40)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_street(texts))
        self.assertGreaterEqual(task.ocr_calls, 3)          # 确实等了动画
        self.assertEqual(task.actions[-1][0], "click")      # 仍无卡才退出
        self.assertGreater(task.actions[-1][1], task.width * 0.9)   # 右上角 ↩

    def test_gacha_animation_then_cards_appear(self):
        """动画播完后刷出三张卡：要正常选卡，不能退出。"""
        empty = [box(760, 80, "请选择1个扭蛋i", 400, 70),
                 box(1075, 20, "200", 60, 30),      # 顶栏零用钱
                 box(640, 950, "确定", 120, 60)]
        real = gacha_texts([
            ("投射增加++", "主力投射+72"),
            ("运控增加++", "主力运控+72"),
            ("体能增加++", "主力体能+72"),
        ], stamina="45/130", money="200", price="60")
        task = FakeTask(empty, qualities=["金", "金", "金"])
        task.ocr_queue = [real]                      # 第一次重试就刷出卡
        self.assertTrue(task.handle_street(empty))
        self.assertEqual(task.actions[0][0], "click")        # 选了卡
        self.assertEqual(task.actions[-1][1], "确定")        # 并点确定

    def test_stamina_recover_parse(self):
        """2026-10-27 用户截图：精力恢复++ 的两种写法要能算出总量。"""
        self.assertEqual(FakeTask.stamina_recover(
            "立刻恢复90精力，之后每天恢复30点，持续4天，队友属性大幅提升"), 210)
        self.assertEqual(FakeTask.stamina_recover("立刻恢复40精力"), 40)
        self.assertEqual(FakeTask.stamina_recover("每天恢复10点，持续4天"), 40)
        # 相反效果不能当成恢复
        self.assertEqual(FakeTask.stamina_recover("消耗所有精力，意识训练"), 0)
        self.assertEqual(FakeTask.stamina_recover("清空精力，运气+30"), 0)
        self.assertEqual(FakeTask.stamina_recover("主力运控+72，队友属性大幅提升"), 0)

    def test_recover_card_beats_equal_quality(self):
        """2026-10-27 用户截图（45/130，金卡+队友 ×2）：

        运控增加++ 与 精力恢复++ 同为基础 141 分，旧 max() 取第一个 → 买错。
        恢复 210 点（上限 130-45=85 有效）折半 +42 → 恢复卡必须胜出。
        """
        task = FakeTask(gacha_texts([
            ("运控增加++", "主力运控+72，队友属性大幅提升"),
            ("投射增加+", "主力投射+48，队友属性中幅提升"),
            ("精力恢复++", "立刻恢复90精力，之后每天恢复30点，持续4天，队友属性大幅提升"),
        ], stamina="45/130", money="106", price=["100", "80", "100"]),
            qualities=["金", "紫", "金"], mates=[True, False, True])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][0], "click")
        self.assertGreaterEqual(task.actions[0][1], 1300)   # 点第三张卡，不是第一张

    def test_recover_card_not_bought_at_full_stamina(self):
        """体力满时恢复卡不加分：别为了回不上的精力白花钱。"""
        task = FakeTask(gacha_texts([
            ("运控增加++", "主力运控+72，队友属性大幅提升"),
            ("精力恢复++", "立刻恢复90精力，之后每天恢复30点，持续4天，队友属性大幅提升"),
        ], stamina="130/130", money="300", price=["100", "100"]),
            qualities=["金", "金"], mates=[True, True])
        self.assertTrue(task.handle_street(task._texts))
        self.assertLess(task.actions[0][1], 1300)           # 仍选第一张运控卡

    def test_all_blue_should_exit_without_buying(self):
        """三张全蓝：直接退出，不能买（用户规则：只买黄紫）。"""
        task = FakeTask(gacha_texts([
            ("训练增强", "训练可以额外获得1点主属性"),
            ("防守增加", "主力防守+30"),
            ("投射增加", "主力投射+30"),
        ], stamina="72/121"), qualities=["蓝", "蓝", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        # leave_street -> 文字退路都没有时点右上角 ↩ (0.957w, 0.047h)
        self.assertEqual(task.actions, [("click", 1837, 50)])

    def test_clear_card_rejected_when_stamina_high(self):
        """体力 72 时，金卡但清空精力 → 拒绝，退出。"""
        task = FakeTask(gacha_texts([
            ("精力转换", "消耗所有精力，意识训练"),
            ("防守增加", "主力防守+30"),
            ("投射增加", "主力投射+30"),
        ], stamina="72/121"), qualities=["金", "蓝", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions, [("click", 1837, 50)])

    def test_unknown_money_exits_without_buying(self):
        """读不到零用钱时不能盲买：买不起会卡在扭蛋页（2026-10-26 实测）。"""
        task = FakeTask(gacha_texts([
            ("体能增加++", "主力体能+72"),
            ("弹跳增加", "主力弹跳+30"),
            ("体能增加+", "主力体能+48"),
        ], stamina="52/121"), qualities=["金", "蓝", "紫"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions, [("click", 1837, 50)])

    def test_clear_card_allowed_when_stamina_low(self):
        """体力 10 < 阈值 30：允许买清空卡（拿体力换属性）。"""
        task = FakeTask(gacha_texts([
            ("精力转换", "消耗所有精力，意识训练"),
            ("防守增加", "主力防守+30"),
            ("投射增加", "主力投射+30"),
        ], stamina="10/121", money="200"), qualities=["蓝", "蓝", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][0], "click")      # 点卡片
        self.assertNotEqual(task.actions[0][1], 1837)      # 不是退出

    def test_cannot_afford_should_exit(self):
        """零用钱 50、卡片 60：买不起就退出，不能点确定卡在扭蛋页。"""
        task = FakeTask(gacha_texts([
            ("训练增强", "训练可以额外获得1点主属性"),
            ("器材暴击+", "器材的暴击加成提高10%"),
            ("投射增加", "主力投射+30"),
        ], stamina="72/121", money="50", price="60"), qualities=["蓝", "紫", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions, [("click", 1837, 50)])

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
        ], stamina="72/121", money="200"), qualities=["蓝", "紫", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        # 第二张卡的卡名中心 x=960+280/2=1100
        self.assertEqual(task.actions[0][0], "click")
        self.assertEqual(task.actions[0][1], 1100)


class EventRules(unittest.TestCase):

    def test_result_page_not_vertical_choice(self):
        """2026-10-27 事故：划船结算页「工资／获得／获得」被当成竖向三选一。

        页面同时有「成功！…下一步」，是结算页；事件选项页没有「下一步」。
        """
        texts = [
            box(500, 300, "成功！", 160, 40),
            box(337, 235, "工资", 90, 34),
            box(560, 300, "多精力+4", 120, 30),
            box(900, 470, "获得", 90, 34),
            box(900, 600, "获得", 90, 34),
            box(1160, 680, "下一步", 140, 46),
        ]
        task = FakeTask(texts)
        self.assertTrue(len(task.choice_candidates(texts)) >= 2)   # 形态上确实像三行
        self.assertFalse(task.handle_event_choice(texts))          # 但不能当选项点
        self.assertEqual(task.actions, [])
        self.assertTrue(task.handle_result(texts))                 # 交给结算页
        self.assertEqual(task.actions[-1][1], "下一步")

    def test_week_summary_not_choice(self):
        """2026-10-27 00:02 实机：本周总结页左列位置名被当成竖向三选一。

        日志原文：`事件选项（竖向）：控卫、分卫、小前、大前、中锋 → 选第一个「控卫」`
        连点 4~5 拍。页面在播动画（分数每拍都在变），「3 次无变化」的看门狗判不出来。
        """
        texts = [
            box(300, 40, "本周总结", 220, 40),
            box(120, 40, "球队评分：国", 180, 40),
            box(700, 120, "球员", 90, 40),
            box(810, 120, "位置", 90, 40),
            box(930, 120, "评分", 90, 40),
            box(495, 287, "控卫", 80, 40),
            box(495, 370, "分卫", 80, 40),
            box(495, 452, "小前", 80, 40),
            box(495, 535, "大前", 80, 40),
            box(495, 617, "中锋", 80, 40),
        ]
        task = FakeTask(texts)
        self.assertTrue(len(task.choice_candidates(texts)) >= 3)   # 形态上确实像一列
        self.assertFalse(task.handle_event_choice(texts))          # 但这是球队表格
        self.assertEqual(task.actions, [])

    def test_race_ladder_not_choice(self):
        """2026-10-27 事故：全国大赛关卡列表被当成竖向三选一。

        右侧信息栏「对手实力／关卡加成／奖励预览」同列竖排，全中 choice_candidates
        的形态判据；TriggerTask 没有看门狗，于是每 0.5 秒点一次「对手实力」点了 30 秒。
        """
        texts = [
            box(1274, 397, "对手实力", 143, 50),
            box(1274, 554, "关卡加成", 144, 47),
            box(1274, 708, "奖励预览", 147, 48),
            box(1468, 894, "前往挑战", 187, 68),
            box(404, 464, "10-9", 181, 120),
        ]
        task = FakeTask(texts)
        self.assertTrue(len(task.choice_candidates(texts)) >= 3)   # 形态上确实像三行
        self.assertFalse(task.handle_event_choice(texts))          # 但这是关卡页
        self.assertEqual(task.actions, [])

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


class ResultButtonRules(unittest.TestCase):
    """结算页按钮必须整词匹配，台词里的「继续」不能当成按钮。"""

    def test_dialog_line_is_not_a_button(self):
        texts = [box(400, 600, "呵呵，那就请继续保持这份斗志，向着目标再次出发吧！", 900, 50)]
        task = FakeTask(texts)
        self.assertFalse(task.handle_result(texts))
        self.assertEqual(task.actions, [])

    def test_exact_next_step_clicks(self):
        texts = [box(800, 900, "下一步", 100, 50)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_result(texts))
        self.assertEqual(task.actions[0][0], "click_box")


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

    def test_street_exit_clicks_give_up(self):
        """扭蛋页右上角 ↩ 点不动，退出必须点底部「放弃」（2026-10-26 实测）。"""
        texts = [box(700, 100, "请选择1个扭蛋", 300, 50),
                 box(400, 660, "放弃", 260, 60)]
        task = FakeTask(texts)
        task.leave_street()
        self.assertEqual(task.actions[-1][0], "click_box")
        self.assertEqual(task.actions[-1][1], "放弃")

    def test_street_exit_without_give_up_clicks_back_arrow(self):
        """2026-10-27：第一次进店的扭蛋页底部只有「能力详情/确定」，没有「放弃」。

        旧兜底点 (0.346w, 0.896h) 落在两个按钮中间的空白，连续 9 拍点不动。
        没有文字退路时必须点右上角 ↩ (0.957w, 0.047h)。
        """
        texts = [box(700, 100, "请选择1个扭蛋", 300, 50),
                 box(640, 950, "确定", 120, 60),
                 box(30, 990, "能力详情", 150, 40)]
        task = FakeTask(texts)
        task.leave_street()
        self.assertEqual(task.actions[-1][0], "click")
        ax, ay = task.actions[-1][1], task.actions[-1][2]
        self.assertGreaterEqual(ax, int(task.width * 0.94))
        self.assertLessEqual(ay, int(task.height * 0.08))

    def test_map_dialogue_is_not_a_park_page(self):
        """2026-10-27：地图页对白「…要去野餐」「…公园的草坪是假的」含判据词。

        旧逻辑把地图页当成公园内页并 mark_visited，之后「都去过了 → 返回」
        死循环 9 拍、任务被判卡死。有「出行」按钮就必须不是公园内页。
        """
        texts = self.MAP + [
            box(400, 260, "小澪奈总是吵着要去野餐，我", 700, 40),
            box(400, 320, "告诉她公园的草坪是假的，她", 700, 40),
            box(1700, 930, "出行", 160, 60),
        ]
        task = FakeTask(texts)
        task.weekend_state()
        self.assertFalse(task.handle_park(texts))
        self.assertEqual(task._weekend["visited"], set())

    def test_place_click_ignores_dialogue_text(self):
        """地图上点地点必须点地点名那一整框，不能点含「公园」的对白长句。"""
        texts = self.MAP + [box(400, 260, "告诉她公园的草坪是假的，她", 700, 40)]
        task = FakeTask(texts)
        task.weekend_state()
        self.assertTrue(task.handle_weekend(texts))
        self.assertEqual(task.actions[-1][0], "click_box")
        self.assertNotIn("告诉她", task.actions[-1][1])
        self.assertIn(task.actions[-1][1], ("公园", "步行街", "游戏厅", "餐厅"))

    def test_weekend_home_is_not_map(self):
        """2026-10-27 实机：首页台词含「公园」+ 商店按钮 + 出行按钮。

        旧判据把首页当地图，台词被点、公园被误记已去，todo 空 → 点右上角。
        首页独有「出行」，地图上没有 → 有出行就不是地图。
        """
        texts = [box(1700, 930, "出行", 160, 60),
                 box(1700, 300, "商店", 60, 30),
                 box(400, 260, "告诉她公园的草坪是假的，她", 700, 40)]
        task = FakeTask(texts)
        task.weekend_state()
        task._weekend["visited"] = {"公园"}
        self.assertFalse(task.handle_weekend(texts))
        self.assertEqual(task.actions, [])

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


class QuickMatchRepeatGuard(unittest.TestCase):
    """常驻任务没有看门狗：同一画面连点 N 拍必须自己停手（2026-10-27 实机连点 30 秒）。"""

    def _bare(self):
        class Bare:
            pass

        t = Bare()
        t._repeat_sig = None
        t._repeat_count = 0
        t._mute_sig = None
        t._mute_ticks = 0
        t.warnings, t.dumps = [], []
        t.log_warning = lambda msg: t.warnings.append(msg)
        t.dump_unknown = lambda texts, tag: t.dumps.append(tag)
        return t

    def test_mute_after_three_same_screen_hits(self):
        from src.tasks.QuickMatchTask import (QuickMatchTask, REPEAT_LIMIT,
                                              REPEAT_RETRY_TICKS)
        t = self._bare()
        texts = [box(1274, 397, "对手实力", 143, 50)]
        sig = frozenset({"对手实力"})

        for i in range(REPEAT_LIMIT):
            self.assertFalse(QuickMatchTask._is_muted(t, sig), f"第 {i + 1} 拍不该静音")
            QuickMatchTask._note_hit(t, texts, sig, "handle_event_choice")

        self.assertEqual(len(t.dumps), 1, "第 3 拍应该存证一次")
        self.assertEqual(len(t.warnings), 1)
        self.assertTrue(QuickMatchTask._is_muted(t, sig), "第 4 拍起必须停手")

        for _ in range(REPEAT_RETRY_TICKS):
            QuickMatchTask._is_muted(t, sig)
        self.assertFalse(QuickMatchTask._is_muted(t, sig), "停手期满要允许重试")

    def test_match_hud_is_exempt(self):
        """比赛内 HUD 本来就该连续点，不参与限流。"""
        from src.tasks.QuickMatchTask import QuickMatchTask
        t = self._bare()
        texts = [box(900, 300, "加速", 150, 50)]
        sig = frozenset({"加速"})
        for _ in range(10):
            QuickMatchTask._note_hit(t, texts, sig, "handle_in_match")
        self.assertIsNone(t._mute_sig)
        self.assertEqual(t.dumps, [])


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
