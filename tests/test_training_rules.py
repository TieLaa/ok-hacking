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

try:
    import cv2
except ImportError:                        # noqa: N816
    cv2 = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402
from tools.check_handlers import DryRunTask  # noqa: E402
from src.tasks.training.parsers import (  # noqa: E402
    CRIT_RING_MIN, ring_is_border)


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

    @staticmethod
    def _card_idx(cx):
        """扭蛋三张卡的列索引（gacha_texts 的卡心 x ≈ 0.32 / 0.57 / 0.82）。"""
        return 0 if cx < 0.42 else (1 if cx < 0.70 else 2)

    def card_band_quality(self, cx):
        # 按**列**取品质，而不是按读取次数 pop：任务现在会为了确认动画稳定多读一帧，
        # 次数会变，但同一张卡的品质不应该跟着变。
        idx = self._card_idx(cx)
        q = self._qualities[idx] if idx < len(self._qualities) else "其他"
        return q, 0, 0

    def has_mate_badge(self, cx):
        idx = self._card_idx(cx)
        return self._mates[idx] if idx < len(self._mates) else False


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

    def test_settle_ignores_animation_frame(self):
        """2026-10-27 10:07 实机：卡片飞入/翻转的中途也能读出 3 张卡。

        脚本当时 0.3 秒就点「确定」，买下中间那张紫卡；1.7 秒后重进才看清
        同价位的金卡。settle_gacha_cards 必须返回**稳定帧**，不能返回动画中间态。
        """
        mid = gacha_texts([
            ("体能增加+", "主力体能+48"),
            ("防守增加+", "主力防守+48"),
            ("弹跳增加", "主力弹跳+20"),
        ], stamina="106/130", money="150", price="80")
        stable = gacha_texts([
            ("防守增加++", "主力防守+72，队友属性大幅提升"),
            ("防守增加++", "主力防守+72，队友属性大幅提升"),
            ("弹跳增加", "主力弹跳+20"),
        ], stamina="106/130", money="150", price=["100", "80", "48"])
        task = FakeTask(mid, qualities=["紫", "紫", "蓝"],
                        mates=[False, False, False])
        task.ocr_queue = [stable, stable, stable]
        names = [c["name"] for c in task.settle_gacha_cards(mid)]
        self.assertEqual(names, ["防守增加++", "防守增加++", "弹跳增加"])

    def test_settle_returns_when_two_frames_agree(self):
        """同一帧连读两次一致 → 直接认账，不多等。"""
        same = gacha_texts([
            ("投射增加++", "主力投射+72"),
            ("运控增加++", "主力运控+72"),
            ("体能增加++", "主力体能+72"),
        ], stamina="45/130", money="200", price="60")
        task = FakeTask(same, qualities=["金", "金", "金"])
        task.ocr_queue = [same, same, same]
        cards = task.settle_gacha_cards(same)
        self.assertEqual(len(cards), 3)
        self.assertEqual(task.ocr_calls, 1)     # 第 2 帧一致就返回，不跑满 3 轮

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
        task.ocr_queue = [real, real]                # 动画结束后卡片持续存在
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

    def test_weekend_movie_dialogue_not_choice(self):
        """2026-10-27 10:07 实机：周末前「和角色去看电影」的对话被当成竖向三选一。

        日志原文：`事件选项（竖向）：我要去和小式一起去看电影、《飞天鱼人、大战十三头鲨》
        → 选第一个「我要去和小式一起去看电影」`，连点 5 拍都是空点；
        这页正确做法是点一下推进，回到周末首页再点「出行」。
        """
        texts = [
            box(1073, 202, "我要去和小式一起去看电影", 444, 50),
            box(1073, 305, "《飞天鱼人、大战十三头鲨》", 444, 50),
            box(1600, 50, "跳过", 90, 40),
        ]
        task = FakeTask(texts)
        # 右上角「跳过」确实在 top_right 区域（has_skip 成立，旧逻辑就靠它命中）
        self.assertIsNotNone(
            task.match_text(task.texts_in_region(texts, "top_right"), "跳过"))
        self.assertFalse(task.handle_event_choice(texts))
        self.assertEqual(task.actions, [])

    def test_movie_dialogue_without_punct_still_not_choice(self):
        """OCR 漏读顿号时，靠「居中」判据挡住：字幕在 cx≈0.68，选项在 0.50。"""
        texts = [
            box(1073, 202, "我要去和小式一起去看电影", 444, 50),
            box(1073, 305, "《飞天鱼人大战十三头鲨》", 444, 50),
            box(1600, 50, "跳过", 90, 40),
        ]
        task = FakeTask(texts)
        self.assertFalse(task.handle_event_choice(texts))
        self.assertEqual(task.actions, [])

    def test_centered_vertical_choice_still_hits(self):
        """真竖向三选一必须仍然命中（几何取自 2026-10-26-story-choice.png：cx=0.50）。"""
        texts = [
            box(810, 331, "更衣室加设空衣柜", 300, 50),
            box(810, 483, "JellyRabbit毛绒玩具", 300, 50),
            box(810, 612, "了解千圣的想法", 300, 50),
            box(1700, 120, "跳过", 90, 40),
        ]
        task = FakeTask(texts)
        self.assertTrue(task.handle_event_choice(texts))
        self.assertEqual(task.actions[-1][1], "更衣室加设空衣柜")

    def test_prose_with_decision_word_is_not_subject_page(self):
        """2026-10-27 事故：「球星卡训练事件·广播站支持」正文里有「决定」二字。

        旧判据用包含匹配找「决定」→ 事件页被当成科目页，3 次点屏幕中央没反应，
        任务被判「点不动」停止。科目页的「决定」永远是独立按钮框（整词匹配）。
        端到端回归见 docs/shots/2026-10-27-broadcast-event.png。
        """
        texts = [
            box(353, 366, "成功后，决定通过广播站平台支持球队。", 492, 33),
            box(313, 594, "开设球队广播时段", 284, 44),
        ]
        task = FakeTask(texts)
        self.assertIsNotNone(task.match_text(texts, "决定"))     # 包含匹配会命中正文
        self.assertIsNone(task.exact_text(texts, ("决定",)))     # 但没有独立的按钮框

    def test_manager_help_animation_is_claimed(self):
        """2026-10-27 用户截图：「球队经理的帮助！」过场动画以前被当成未知界面。

        代价是每 3 秒存证一张（一局 16 张「没见过这个界面」），日志也误导成识别不了。
        """
        texts = [box(300, 30, "第3周", 120, 40),
                 box(400, 120, "球队经理的帮助！", 600, 90)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_popup(texts))
        self.assertEqual(task.actions[-1][0], "click")

    def test_boost_training_not_stolen_by_manager_help(self):
        """加强训练页左上角也写着「球队经理的帮助」，不能被过场 handler 点掉。

        那页有真正的二选一卡（handle_boost_training 处理），抢走就等于随机选一张。
        """
        texts = [box(60, 200, "球队经理的帮助", 240, 60),
                 box(700, 180, "请选择一种加强训练", 600, 60),
                 box(590, 260, "100%", 80, 40),
                 box(990, 260, "100%", 80, 40),
                 box(590, 430, "+12", 90, 50),
                 box(990, 430, "+36", 90, 50),
                 box(590, 640, "确定", 200, 60)]
        task = FakeTask(texts)
        self.assertFalse(task.handle_popup(texts))
        self.assertTrue(task.handle_boost_training(texts))

    def test_boost_two_numbers_per_card(self):
        """2026-10-27 用户录屏：每张卡上有卡顶「100%」+ 卡内两个同名「+36」。

        旧写法按「每个百分比算一张卡」处理，同一张卡会被算成两张，
        还会冒出图上看不见的 80%（日志：`100%/+12=12  80%/+36=29`）。
        """
        texts = [box(700, 180, "请选择一种加强训练", 600, 60),
                 box(590, 250, "100%", 90, 40),
                 box(990, 250, "100%", 90, 40),
                 box(560, 400, "+12", 80, 45),
                 box(570, 700, "+12", 80, 45),
                 box(960, 400, "+36", 80, 45),
                 box(970, 700, "+36", 80, 45),
                 box(590, 1000, "确定", 200, 60)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_boost_training(texts))
        joined = " ".join(task.logs)
        self.assertIn("100%/+12=12", joined)
        self.assertIn("选 100%/+36", joined)
        self.assertNotIn("80%", joined)

    def test_multi_choice_skips_flat_stamina(self):
        """2026-10-27 10:09 实机：三个选项里「不理会(精力+10)」被选中，实际该选属性。

        日志原文：`事件选项：不理会(10·回精力)、稍微靠近他(4)、迎面走向他(3)
        → 选「不理会」（横向多选 → 期望最高）`——多选分支原来没排除纯回精力。
        """
        from src.tasks.training.handlers.match import MatchHandlers
        infos = [
            {"name": "不理会", "ev": 10, "flat": True, "x": 100, "y": 300},
            {"name": "稍微靠近他", "ev": 4, "flat": False, "x": 500, "y": 300},
            {"name": "迎面走向他", "ev": 3, "flat": False, "x": 900, "y": 300},
        ]
        pick, why = MatchHandlers._pick_choice(infos, "h")
        self.assertEqual(pick["name"], "稍微靠近他")
        self.assertIn("回精力", why)

    def test_multi_choice_all_flat_falls_back(self):
        """三个都是回精力时，退回按数值取最大（不能因此不选）。"""
        from src.tasks.training.handlers.match import MatchHandlers
        infos = [
            {"name": "A", "ev": 10, "flat": True, "x": 100, "y": 300},
            {"name": "B", "ev": 20, "flat": True, "x": 500, "y": 300},
        ]
        pick, _why = MatchHandlers._pick_choice(infos, "h")
        self.assertEqual(pick["name"], "B")

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


class SubjectScoreWeights(unittest.TestCase):
    """科目打分权重（用户 2026-10-27 定稿）：队友1 装备1 经理2.1 金框1.2 彩框1.3。"""

    def test_weights(self):
        r = {"mates": 2, "equip": 1, "manager": 1, "gold": 1, "color": 1}
        self.assertAlmostEqual(FakeTask.subject_score(r), 2 + 1 + 2.1 + 1.2 + 2.3, places=4)

    def test_crit_outranks_manager(self):
        """2026-10-29 用户口径：彩框 2.3 要压过经理 2.1（有彩框就该练）。"""
        crit = {"mates": 1, "equip": 1, "manager": 0, "gold": 0, "color": 1}
        mgr = {"mates": 1, "equip": 1, "manager": 1, "gold": 0, "color": 0}
        self.assertGreater(FakeTask.subject_score(crit), FakeTask.subject_score(mgr))

    def test_crit_border_excludes_gold_border(self):
        """彩框和金框互斥：彩虹边框里有一段橙黄，不能不互斥地双重计分。

        两者同源（装备/经理经验满了才亮），用户 2026-10-27 截图确认：
        科目按钮彩框 ↔ 中间装备卡彩框，是同一个来源。
        """
        task = FakeTask([])
        task.has_crit_border = lambda texts=None, subject=None: True
        task.has_gold_border = lambda texts=None, subject=None: True   # 即使金框也判 True
        task.read_equipment = lambda texts: (1, 1)
        task.read_scene = lambda texts: {"names": [], "mates": 0, "manager": 0}
        r = task.analyze_preview([])
        self.assertTrue(r["crit"])
        self.assertEqual(r["color"], 1)
        self.assertEqual(r["gold"], 0)                     # 彩框优先，金框不计
        self.assertAlmostEqual(task.subject_score(r), 1 + 2.3, places=4)

    def test_borders_come_from_button_not_from_cards(self):
        """金框/彩框只看科目按钮外圈；器材卡再多也不额外加分（避免重复计分）。"""
        task = FakeTask([])
        task.has_gold_border = lambda texts=None, subject=None: True
        task.has_crit_border = lambda texts=None, subject=None: False
        task.read_equipment = lambda texts: (3, 2)          # 3 张卡、2 张满经验
        task.read_scene = lambda texts: {"names": [], "mates": 0, "manager": 0}
        r = task.analyze_preview([])
        self.assertEqual(r["gold"], 1)
        self.assertEqual(r["color"], 0)
        self.assertEqual(r["equip"], 3)
        # 装备只按张数算一次，不会因为「卡是金框」翻倍
        self.assertAlmostEqual(task.subject_score(r), 3 + 1.2, places=4)
        # 旧实现「数器材卡上的金框」已经删掉，防止有人再把它加回来重复计分
        self.assertFalse(hasattr(task, "count_gold_cards"))

    def test_ring_threshold_ignores_selection_highlight(self):
        """选中行天然就比别的行橙（0.075~0.114），不能当成金框。

        2026-10-27 第六局：21 次选科目报 13 次金框，用户当场指出「刚才没有金框」。
        真彩虹框实测 0.333，西瓜图标那行 0.100。
        """
        self.assertFalse(ring_is_border(0.114, 0.027))      # 旧门限 0.10 会误判
        self.assertFalse(ring_is_border(0.103, 0.027))
        self.assertFalse(ring_is_border(0.100, 0.024))      # 西瓜图标
        self.assertFalse(ring_is_border(0.150, 0.005))      # 低于绝对下限
        self.assertTrue(ring_is_border(0.333, 0.024))       # 真彩虹/金框
        self.assertFalse(ring_is_border(0.431, 0.554))      # 度假页：整屏都黄，基线也高
        self.assertTrue(ring_is_border(0.333, None, floor=CRIT_RING_MIN))


class RegisterPlayerFlow(unittest.TestCase):
    """一局打完的注册球员六步（用户 2026-10-27 提供的 6 屏截图）。

    下一步 → 编队 → 自动编队 → 保存调整 → 关闭 → 左上角 « 返回。
    必须靠 `_register_stage` 串起来，不能凭「页面上有战术板」就动手。
    """

    def test_band_page_without_entry_is_untouched(self):
        """玩家自己从阵容菜单进的战术板：没有走过第 1 步，一个字都不能点。"""
        texts = [
            box(100, 80, "战术板", 160, 40),
            box(1120, 980, "自动编队", 280, 60),
            box(600, 980, "保存调整", 280, 60),
        ]
        task = FakeTask(texts)
        self.assertFalse(task.handle_register_player(texts))
        self.assertEqual(task.actions, [])

    def test_six_steps_in_order(self):
        task = FakeTask([])

        p1 = [box(700, 240, "注册球员", 200, 40),
              box(1900, 950, "下一步", 300, 60),
              box(400, 950, "记录笔记", 300, 60)]
        self.assertTrue(task.handle_register_player(p1))
        # 点的是「下一步」文字框中心（第一次 tries=1 不加偏移）
        self.assertEqual(task.actions[-1][0], "click")
        self.assertEqual(task.actions[-1][1:], (2050, 980))
        self.assertEqual(task._register_stage, "next")

        p2 = [box(600, 200, "训练结束", 240, 40),
              box(1600, 800, "编队", 200, 60),
              box(700, 800, "前往主界面", 260, 60)]
        self.assertTrue(task.handle_register_player(p2))
        self.assertEqual(task.actions[-1][1], "编队")
        self.assertEqual(task._register_stage, "team")

        p3 = [box(100, 80, "战术板", 160, 40),
              box(1120, 980, "自动编队", 280, 60),
              box(600, 980, "保存调整", 280, 60)]
        self.assertTrue(task.handle_register_player(p3))
        self.assertEqual(task.actions[-1][1], "自动编队")
        self.assertEqual(task._register_stage, "auto")

        p4 = [box(100, 80, "战术板", 160, 40),
              box(600, 980, "保存调整", 280, 60),
              box(1400, 500, "评分上升", 200, 40)]
        self.assertTrue(task.handle_register_player(p4))
        self.assertEqual(task.actions[-1][1], "保存调整")
        self.assertEqual(task._register_stage, "save")

        p5 = [box(800, 300, "更新最高队伍等级", 400, 50),
              box(900, 900, "关闭", 220, 60)]
        self.assertTrue(task.handle_register_player(p5))
        self.assertEqual(task.actions[-1][1], "关闭")
        self.assertEqual(task._register_stage, "closed")

        p6 = [box(100, 80, "战术板", 160, 40)]
        self.assertTrue(task.handle_register_player(p6))
        self.assertEqual(task.actions[-1][0], "click")     # 左上角没有文字，按坐标点
        self.assertEqual(task._register_stage, "back")

    def test_team_edit_page_auto_select_then_save(self):
        """编队详情页（右侧注册球员名单 + 重置/自动选择/保存编队）。

        用户截图里没有这一屏，但 2026-10-27 16:18 实跑确实进了：编队 → 详情页。
        顺序必须是 自动选择 → 保存编队，反过来会把空阵容存下去。
        """
        task = FakeTask([])
        task._register_stage = "team"
        page = [box(940, 550, "重置", 90, 34),
                box(1120, 550, "自动选择", 190, 40),
                box(1000, 645, "保存编队", 240, 50)]
        self.assertTrue(task.handle_register_player(page))
        self.assertEqual(task.actions[-1][1], "自动选择")
        self.assertEqual(task._register_stage, "team_auto")

        self.assertTrue(task.handle_register_player(page))
        self.assertEqual(task.actions[-1][1], "保存编队")
        self.assertEqual(task._register_stage, "team_saved")

    def test_event_prose_with_training_end_is_not_register_popup(self):
        """事件页正文含「训练结束后…」不能被当成本局收尾弹窗。

        2026-10-27 18:12 实跑：「训练结束后，米歇尔理事长拿来了一本穴位按摩秘籍」
        三选一事件被判成收尾弹窗，随手点了最右边的黄按钮，事件等于乱选。
        """
        texts = [
            box(360, 300, "训练结束后，米歇尔理事长拿来了一本穴位按摩秘籍", 700, 30),
            box(300, 620, "放弃按摩", 200, 40),
            box(700, 620, "安心按摩", 200, 40),
        ]
        task = FakeTask(texts)
        self.assertIsNotNone(task.match_text(texts, "训练结束"))     # 包含匹配会命中正文
        self.assertIsNone(task.exact_text(texts, ("训练结束",)))      # 但没有独立标题框
        self.assertFalse(task.handle_register_player(texts))

    def test_training_end_popup_wins_over_next_step(self):
        """弹窗正文含「注册球员」、背后还露着「下一步」时，必须点「编队」。

        2026-10-28 18:17 实跑：训练结束弹窗上「注册球员」出现在正文
        「成为注册球员后，可以编队加入队伍。」，而背景页的「下一步」仍在右侧下方，
        结果第 1 步抢到了，一直点被弹窗盖住的「下一步」，空烧 890 拍。
        """
        texts = [
            box(323, 138, "训练结束", 138, 43),
            box(702, 333, "辛苦了！", 123, 40),
            box(487, 383, "成为注册球员后，可以编队加入队伍。", 552, 36),
            box(499, 621, "前往主界面", 182, 48),
            box(913, 620, "编队", 79, 49),
            box(921, 757, "下一步", 87, 29),        # 被弹窗盖住的背景按钮
        ]
        task = FakeTask(texts)
        self.assertTrue(task.handle_register_player(texts))
        self.assertEqual(task.actions[-1][1], "编队")

    def test_tick_cap_gives_up(self):
        """同一个 stage 连续 20 拍没推进 → 放弃（不许无限重试）。"""
        texts = [box(913, 620, "编队", 79, 49),
                 box(323, 138, "训练结束", 138, 43)]
        task = FakeTask(texts)
        task.click_box = lambda box, **kw: None            # 点了也不动
        gave_up = None
        for i in range(1, 31):
            if not task.handle_register_player(texts):
                gave_up = i
                break
        self.assertIsNotNone(gave_up, "连续 30 拍都没放弃，说明拍数上限没生效")
        self.assertLessEqual(gave_up, 25)

    def test_close_is_not_taken_by_popup_handler(self):
        """「更新最高队伍等级」弹窗的关闭必须由注册流程处理，不能落到 handle_popup。"""
        from src.tasks.training.handlers import HANDLERS
        names = [h.__name__ for h in HANDLERS]
        self.assertLess(names.index("handle_register_player"), names.index("handle_popup"))


class RingDetection(unittest.TestCase):
    """金框/彩框必须量**指名科目的那一行**；指名的行不存在时判无框。

    2026-10-27 17:04 事故：探测「防守」时 OCR 没认出防守那行，代码兜底退回
    「决定所在行」= 投射，于是投射的金框 0.482 被记到防守头上，
    两个科目同分 4.30，真正的金框科目被挤掉。
    """

    def _frame_with_gold_ring(self, cy):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        h, w = frame.shape[:2]
        y1, y2 = int(cy - h * 0.055), int(cy + h * 0.055)
        x1, x2 = int(w * 0.752), int(w * 0.968)
        t = 14
        orange = (30, 140, 255)                    # BGR
        frame[y1:y1 + t, x1:x2] = orange
        frame[y2 - t:y2, x1:x2] = orange
        frame[y1:y2, x1:x1 + t] = orange
        frame[y1:y2, x2 - t:x2] = orange
        return frame

    def test_named_row_only(self):
        cy_hit, cy_other = 300, 700
        frame = self._frame_with_gold_ring(cy_hit)
        texts = [
            box(1500, cy_hit - 30, "投射", 110, 66),
            box(1500, cy_other - 30, "体能", 110, 66),
        ]
        task = DryRunTask(frame, texts)
        self.assertTrue(task.has_gold_border(texts, subject="投射"))
        self.assertFalse(task.has_gold_border(texts, subject="体能"))

    def test_missing_row_is_not_gold(self):
        """指名的科目这一帧没认出来 → 不能拿「决定所在行」顶替。"""
        frame = self._frame_with_gold_ring(300)
        texts = [
            box(1500, 270, "投射", 110, 66),
            box(1500, 670, "体能", 110, 66),
            box(1700, 830, "决定", 158, 111),
        ]
        task = DryRunTask(frame, texts)
        self.assertFalse(task.has_gold_border(texts, subject="防守"))


class QuickMatchGate(unittest.TestCase):
    """快速比赛：三选一/二选一必须先进比赛才处理（2026-10-28 用户反馈）。"""

    def test_match_hud_detected(self):
        from src.tasks.QuickMatchTask import QuickMatchTask
        texts = [box(100, 20, "第2节", 80, 30), box(400, 20, "比分", 60, 30)]
        self.assertTrue(QuickMatchTask._has_match_hud(texts))

    def test_lobby_page_is_not_match(self):
        from src.tasks.QuickMatchTask import QuickMatchTask
        texts = [box(60, 60, "强化", 90, 40), box(300, 660, "+99", 90, 40)]
        self.assertFalse(QuickMatchTask._has_match_hud(texts))


class LobbySubpage(unittest.TestCase):
    """大厅子菜单（强化/招募/卡池）任务一律不动（2026-10-28 用户反馈）。"""

    def test_upgrade_page_is_ignored(self):
        texts = [
            box(60, 60, "强化", 90, 40),
            box(300, 420, "等级27", 120, 36),
            box(300, 600, "额外零用钱", 140, 32),
            box(300, 660, "+99", 90, 40),
        ]
        task = FakeTask(texts)
        self.assertTrue(task.is_lobby_subpage(texts))

    def test_training_flow_not_ignored(self):
        """真正训练页里也有「强化」字样（器材强化），不能误伤。"""
        texts = [box(60, 60, "强化", 90, 40),
                 box(600, 120, "失败率0%", 120, 32),
                 box(1700, 800, "决定", 150, 60)]
        task = FakeTask(texts)
        self.assertFalse(task.is_lobby_subpage(texts))

    def test_training_scenario_page_is_not_lobby_subpage(self):
        """训练剧本页右侧有「强化/图鉴/成就」页签，但它必须放行。

        2026-10-29 实跑：从大厅点「训练」进来后整页被判成大厅子菜单，
        任务不点「启程」也不走兜底，5 秒后看门狗点屏幕中央，连续 3 轮直接停任务。
        """
        texts = [box(47, 38, "《", 47, 40),
                 box(124, 40, "训练剧本", 137, 38),
                 box(897, 112, "大师模式", 125, 37),
                 box(1413, 171, "强化", 75, 44),
                 box(1347, 327, "训练记录", 141, 43),
                 box(409, 398, "备战", 186, 104),
                 box(1238, 729, "启程", 108, 59)]
        task = FakeTask(texts)
        self.assertFalse(task.is_lobby_subpage(texts))
        called = {}
        task.click_text_and_expect = (
            lambda t, name, expect, **kw: called.setdefault("name", name) or True)
        self.assertTrue(task.handle_training_entry(texts))
        self.assertEqual(called.get("name"), "启程")


class EventCardChoices(unittest.TestCase):
    """底排横向 3 选 1 大卡（按摩事件那种）：按期望值选，不能卡死。

    2026-10-28 实跑：放弃按摩(属性+10) / 安心按摩(70%属性+30) / 神秘按摩(30%属性+60)，
    三个白底大卡既不是居中一列也不是黄色按钮 → 没有 handler 认领 → 卡死停任务。
    """

    def test_massage_event_picks_best_ev(self):
        texts = [
            box(60, 40, "第2周", 90, 34), box(200, 40, "星期四", 90, 34),
            box(420, 330, "训练结束后，米歇尔理事长拿来了一本穴位按摩秘籍", 700, 37),
            box(240, 700, "放弃按摩", 170, 45),
            box(700, 700, "安心按摩", 170, 45),
            box(1170, 700, "神秘按摩", 170, 45),
            box(240, 760, "属性+10", 130, 33),
            box(640, 760, "70%概率 属性+30", 260, 37),
            box(1110, 760, "30%概率 属性+60", 260, 37),
        ]
        task = FakeTask(texts)
        self.assertTrue(task.handle_event_choice(texts))
        self.assertEqual(task.actions[-1][1], "安心按摩")     # EV 21 > 18 > 10


class TapToContinue(unittest.TestCase):
    """MVP / 胜负过场：只有一行「点击屏幕继续」，必须点一下推进（2026-10-28 用户实测）。"""

    def test_mvp_screen_is_clicked(self):
        texts = [
            box(500, 300, "MVP", 300, 120),
            box(900, 300, "真崎", 200, 90),
            box(305, 477, "得分", 90, 40),
            box(560, 700, "点击屏幕继续", 200, 32),
        ]
        task = FakeTask(texts)
        self.assertTrue(task.handle_tap_to_continue(texts))
        # 必须点在「点击屏幕继续」这行字上（点屏幕正中无效）
        self.assertEqual(task.actions[-1][0], "click_box")
        self.assertEqual(task.actions[-1][1], "点击屏幕继续")

    def test_page_with_buttons_is_not_stolen(self):
        texts = [box(560, 700, "点击屏幕继续", 200, 32),
                 box(1000, 900, "下一步", 200, 60)]
        task = FakeTask(texts)
        self.assertFalse(task.handle_tap_to_continue(texts))
        self.assertEqual(task.actions, [])


class ArcadeRules(unittest.TestCase):
    """游戏厅：「自动」= 打过的小游戏，只能买带它那几张（用户 2026-10-27）。"""

    def _task(self, texts, money=300, pay=True, quality="金"):
        task = FakeTask(texts, config={"游戏厅付费": pay})
        task.read_money = lambda texts=None: money
        # 品质靠帧取色，测试里直接给结论（金/紫才买）
        task.card_quality_at = lambda cx, y1, y2: (quality, 30 if quality == "金" else 100, 150)
        return task

    def test_only_auto_card_is_bought(self):
        texts = [
            box(600, 115, "请选择一个小游戏", 220, 36),
            box(199, 236, "自动", 64, 30),
            box(308, 238, "弹跳", 70, 42),
            box(251, 370, "弹跳20~70", 155, 31),
            box(355, 484, "64", 44, 32),
            box(520, 236, "运控", 70, 39),          # 没有「自动」= 没打过，不能买
            box(539, 370, "运控20~70", 178, 31),
            box(615, 646, "确定", 71, 39),
        ]
        task = self._task(texts)
        self.assertTrue(task.handle_arcade(texts))
        self.assertEqual(task.actions[-2][1], "弹跳20~70")
        # 买完要先等小游戏自己打完（进场动画没有按钮，别让看门狗判卡死）
        self.assertGreater(task._arcade_wait_since, 0)

    def test_blue_card_is_not_bought(self):
        """蓝色品质不买（用户 2026-10-27：只买黄/紫 + 带自动）。"""
        texts = [
            box(600, 115, "请选择一个小游戏", 220, 36),
            box(199, 236, "自动", 64, 30),
            box(308, 238, "弹跳", 70, 42),
            box(251, 370, "弹跳20~70", 155, 31),
            box(615, 646, "确定", 71, 39),
        ]
        task = self._task(texts, quality="蓝")
        self.assertTrue(task.handle_arcade(texts))
        self.assertEqual(task.actions[-1][0], "click")      # 只退出，不买

    def test_discount_price_takes_the_lower_number(self):
        """打折时卡片上有两个价格、位置还会偏（用户 2026-10-27），取折后那个。

        OCR 还会把原价和现价粘成 "10080"，按 3 位切回来当 100 / 80 取小值。
        """
        texts = [
            box(600, 115, "请选择一个小游戏", 220, 36),
            box(199, 236, "自动", 64, 30),
            box(308, 238, "弹跳", 70, 42),
            box(251, 370, "弹跳20~70", 155, 31),
            box(184, 453, "打折", 68, 55),
            box(355, 484, "80", 44, 32),
            box(392, 486, "64", 46, 31),
            box(615, 646, "确定", 71, 39),
        ]
        task = self._task(texts, money=300)
        self.assertTrue(task.handle_arcade(texts))
        self.assertEqual(task.actions[-2][1], "弹跳20~70")
        self.assertIn("价格 64", task.logs[-1])

    def test_no_auto_card_exits(self):
        texts = [
            box(600, 115, "请选择一个小游戏", 220, 36),
            box(520, 236, "运控", 70, 39),
            box(539, 370, "运控20~70", 178, 31),
            box(615, 646, "确定", 71, 39),
        ]
        task = self._task(texts)
        self.assertTrue(task.handle_arcade(texts))
        self.assertEqual(task.actions[-1][0], "click")      # exit_page：点右上角返回

    def test_paid_flag_off_exits(self):
        texts = [
            box(600, 115, "请选择一个小游戏", 220, 36),
            box(199, 236, "自动", 64, 30),
            box(308, 238, "弹跳", 70, 42),
            box(251, 370, "弹跳20~70", 155, 31),
            box(615, 646, "确定", 71, 39),
        ]
        task = self._task(texts, pay=False)
        self.assertTrue(task.handle_arcade(texts))
        self.assertEqual(task.actions[-1][0], "click")


class EffectExpectation(unittest.TestCase):
    """效果文本 → 真实属性总量（用户 2026-10-27 口径）。"""

    def ev(self, *lines):
        return FakeTask._effect_ev([box(400, 300, t, 300, 40) for t in lines])

    def test_full_attributes_count_six_times(self):
        """「全属性+5」是六项各 +5 = 30 点，不是 5 点。"""
        self.assertEqual(self.ev("全属性+5"), 30)
        self.assertEqual(self.ev("全属性+10"), 60)

    def test_single_and_double_attributes_add_up(self):
        self.assertEqual(self.ev("投射+3"), 3)
        self.assertEqual(self.ev("投射+2，弹跳+3"), 5)

    def test_stamina_is_worth_little(self):
        """精力只有 1/5 价值（纯回精力在挑选阶段还会被排除）。"""
        self.assertAlmostEqual(self.ev("精力+10"), 2.0)
        self.assertAlmostEqual(self.ev("精力+10，投射+5"), 7.0)

    def test_probability_multiplies(self):
        self.assertAlmostEqual(self.ev("50%概率 全属性+5"), 15.0)
        self.assertIsNone(self.ev("没有数值"))


class GoldBorderRules(unittest.TestCase):
    """金框 = 科目按钮外圈的**相对**亮度（海边度假那套界面所有按钮都黄）。"""

    NAMES = ["投射", "弹跳", "防守", "体能", "运控", "意识"]
    YS = [0.22, 0.35, 0.47, 0.59, 0.71, 0.84]

    def _frame(self, ring_row, all_orange):
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for i, y in enumerate(self.YS):
            cy = int(1080 * y)
            x1, x2 = int(1920 * 0.755), int(1920 * 0.965)
            y1, y2 = cy - 40, cy + 40
            # BGR：橙黄按钮（度假/全 MAX）或蓝色按钮（普通）
            color = (80, 170, 245) if all_orange else (230, 160, 60)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, -1)
            if ring_row == i:
                cv2.rectangle(img, (x1 - 6, y1 - 6), (x2 + 6, y2 + 6), (60, 200, 250), 8)
        return img

    def _task(self, ring_row, all_orange):
        texts = [box(int(1920 * 0.80), int(1080 * y) - 20, name, 120, 40)
                 for name, y in zip(self.NAMES, self.YS)]
        sel = ring_row or 0
        texts.append(box(int(1920 * 0.93), int(1080 * self.YS[sel]) - 20, "决定", 100, 40))
        task = FakeTask(texts)
        task._executor.frame = self._frame(ring_row, all_orange)
        return task

    def test_gold_ring_on_selected_row(self):
        task = self._task(ring_row=0, all_orange=False)
        self.assertTrue(task.has_gold_border(task._texts))

    def test_no_ring_means_false(self):
        task = self._task(ring_row=None, all_orange=False)
        self.assertFalse(task.has_gold_border(task._texts))

    def test_resort_page_all_yellow_is_not_gold_ring(self):
        """海边度假：6 个科目按钮全被强制 MAX → 按钮本身全是橙黄。

        绝对阈值会把 6 行都判成有金框；相对判据要求选中行明显亮于其它行。
        """
        task = self._task(ring_row=None, all_orange=True)
        self.assertFalse(task.has_gold_border(task._texts))


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
    """科目排序：分数 → 数值低的 → 屏幕靠上（2026-10-27：推荐属性已取消）。"""

    def test_score_wins_first(self):
        self.assertGreater(FakeTask.subject_rank(3.0, 999, 900),
                           FakeTask.subject_rank(2.0, 100, 100))

    def test_lower_value_wins_when_score_tied(self):
        self.assertGreater(FakeTask.subject_rank(2.0, 400, 900),
                           FakeTask.subject_rank(2.0, 800, 300))

    def test_unknown_value_loses(self):
        self.assertGreater(FakeTask.subject_rank(2.0, 900, 900),
                           FakeTask.subject_rank(2.0, None, 100))


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

    def _map(self, day):
        """地图页 + 顶栏日期（weekend_priority 现在按「星期+流派」排行程）。"""
        return list(self.MAP) + [box(30, 20, "第1周", 120, 40),
                                 box(200, 20, day, 130, 40)]

    def _home_texts(self, day):
        return [box(30, 20, "第1周", 120, 40),
                box(200, 20, day, 130, 40),
                box(380, 16, "120/130", 210, 53),
                box(950, 560, "商店", 130, 50),
                box(1150, 700, "出行", 160, 70)]

    @staticmethod
    def styled(style, texts, **kwargs):
        """流派现在只由队长决定：测试里直接写运行期变量（= 日历已解析）。"""
        task = FakeTask(texts, **kwargs)
        task._style = style
        return task

    def test_sunday_enters_shop_too(self):
        """商店流周日在首页先逛一次商店（用户 2026-10-29 保留）。"""
        task = self.styled("商店流", self._home_texts("星期日"))
        self.assertTrue(task.handle_go_out(task._texts))
        self.assertEqual(task.actions[-1][0], "click_box")
        self.assertEqual(task.actions[-1][1], "商店")

    def test_training_style_enters_shop_on_sunday(self):
        """训练流周日也先逛商店，再出行（用户 2026-10-29 新口径）。"""
        task = self.styled("训练流", self._home_texts("星期日"))
        self.assertTrue(task.handle_go_out(task._texts))
        self.assertEqual(task.actions[-1][1], "商店")
        task.actions.clear()
        self.assertTrue(task.handle_go_out(task._texts))
        self.assertEqual(task.actions[-1][1], "出行")

    def test_saturday_does_not_enter_shop(self):
        """2026-10-29：周六所有人都不进商店 —— 其他流派去餐厅打工。"""
        for style in ("商店流", "训练流", "扭蛋流", "小游戏流"):
            task = self.styled(style, self._home_texts("星期六"))
            self.assertTrue(task.handle_go_out(task._texts))
            self.assertEqual(task.actions[-1][1], "出行", style)

    def test_sunday_non_shop_styles_go_out_directly(self):
        """周日只有训练流/商店流先逛店；扭蛋流、小游戏流直接出行。"""
        for style in ("扭蛋流", "小游戏流"):
            task = self.styled(style, self._home_texts("星期日"))
            self.assertTrue(task.handle_go_out(task._texts))
            self.assertEqual(task.actions[-1][1], "出行", style)

    def test_sunday_place_by_style(self):
        """周日地点：训练流→公园、商店流→餐厅、扭蛋流→步行街、小游戏流→游戏厅。"""
        want = {"训练流": "公园", "商店流": "餐厅", "扭蛋流": "步行街",
                "小游戏流": "游戏厅"}
        for style, place in want.items():
            task = self.styled(style, self._map("星期日"))
            task.weekend_state()
            self.assertTrue(task.handle_weekend(task._texts), style)
            self.assertEqual(task.actions[-1][1], place, style)

    def test_sunday_spend_then_work(self):
        """周日扭蛋流/小游戏流：先花钱，再去餐厅打工（表里两条，无条件）。"""
        for style, place in (("扭蛋流", "步行街"), ("小游戏流", "游戏厅")):
            task = self.styled(style, self._map("星期日"))
            task.weekend_state()
            self.assertTrue(task.handle_weekend(task._texts), style)
            self.assertEqual(task.actions[-1][1], place, style)     # 先主地点
            task.actions.clear()
            task._weekend["visited"] = {place}                      # 走完了
            self.assertTrue(task.handle_weekend(task._texts), style)
            self.assertEqual(task.actions[-1][1], "餐厅", style)     # 接着打工

    def test_saturday_non_training_styles_go_to_restaurant(self):
        """周六除训练流外都去餐厅打工（不进步行街/游戏厅）。"""
        for style in ("商店流", "扭蛋流", "小游戏流"):
            task = self.styled(style, self._map("星期六"))
            task.weekend_state()
            self.assertTrue(task.handle_weekend(task._texts), style)
            self.assertEqual(task.actions[-1][1], "餐厅", style)

    def test_saturday_training_style_keeps_old_route(self):
        """训练流周六原设定不变：步行街 → 游戏厅 → 公园。"""
        task = self.styled("训练流", self._map("星期六"))
        task.weekend_state()
        self.assertTrue(task.handle_weekend(task._texts))
        self.assertEqual(task.actions[-1][1], "步行街")

    def test_sunday_training_style_is_park(self):
        """训练流周日就是公园（地点表说了算，没有额外开关）。"""
        task = self.styled("训练流", self._map("星期日"))
        task.weekend_state()
        self.assertTrue(task.handle_weekend(task._texts))
        self.assertEqual(task.actions[-1][1], "公园")

    def test_shop_only_once_per_day(self):
        """同一天不重复进店：第二拍直接点「出行」。"""
        task = self.styled("商店流", self._home_texts("星期日"))
        task.handle_go_out(task._texts)
        task.actions.clear()
        self.assertTrue(task.handle_go_out(task._texts))
        self.assertEqual(task.actions[-1][1], "出行")

    def test_training_style_never_enters_shop_on_saturday(self):
        """训练流周六不逛商店（钱优先换步行街/游戏厅的黄道具）。"""
        task = self.styled("训练流", self._home_texts("星期六"))
        task.handle_go_out(task._texts)
        self.assertEqual(task.actions[-1][1], "出行")

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


class StylePlanRules(unittest.TestCase):
    """流派识别 + 按「星期+流派」排行程（用户 2026-10-29 口径）。"""

    CALENDAR = [box(184, 74, "望月", 158, 91),
                box(339, 96, "的训练计划", 294, 62)]

    def _home(self, day):
        return [box(30, 20, "第1周", 120, 40),
                box(200, 20, day, 130, 40),
                box(380, 16, "120/130", 210, 53),
                box(950, 560, "商店", 130, 50),
                box(1150, 700, "出行", 160, 70)]

    # ---------- 日历认队长 ----------

    def test_calendar_name_split_boxes(self):
        """实测帧里名字和标题是两个框（望月 / 的训练计划），要能拼出「望月」。"""
        task = FakeTask(list(self.CALENDAR))
        self.assertEqual(task.read_plan_captain(task._texts), "望月")

    def test_calendar_name_in_one_box(self):
        task = FakeTask([box(184, 74, "真崎的训练计划", 450, 90)])
        self.assertEqual(task.read_plan_captain(task._texts), "真崎")

    def test_calendar_name_overlaps_title(self):
        """真帧（logs/live_run/plan_*）：名字右边缘压过标题左边 11px，不能被排除。

        闪堂 (187,74,163,89) ／ 的训练计划 (339,91,295,71) ／ 星期 (182,170,68,35)
        """
        texts = [box(187, 74, "闪堂", 163, 89),
                 box(339, 91, "的训练计划", 295, 71),
                 box(182, 170, "星期", 68, 35),
                 box(365, 170, "星期二", 87, 35),
                 box(357, 280, "投射训练", 108, 37),
                 box(539, 283, "弹跳训练", 106, 34),
                 box(721, 283, "意识训练", 108, 32)]
        task = FakeTask(texts)
        self.assertEqual(task.read_plan_captain(texts), "闪堂")
        self.assertEqual(FakeTask.style_for_name("闪堂"), "训练流")
        self.assertTrue(task.handle_training_plan(texts))
        self.assertEqual(task.style, "训练流")

    def test_week_prefix_is_not_captain(self):
        """顶栏「第」/「第5周」不能当队长（真帧里它们在 (37,22) 和 (192,21)）。"""
        texts = [box(37, 22, "第", 34, 28),
                 box(192, 21, "第", 31, 27),
                 box(339, 91, "的训练计划", 295, 71),
                 box(182, 170, "星期", 68, 35),
                 box(365, 170, "星期二", 87, 35),
                 box(357, 280, "投射训练", 108, 37),
                 box(539, 283, "弹跳训练", 106, 34),
                 box(721, 283, "意识训练", 108, 32)]
        task = FakeTask(texts)
        self.assertIsNone(task.read_plan_captain(texts))

    def test_calendar_week_header_is_not_captain(self):
        """2026-10-29 实跑：名字漏识别时只剩「星期」列头，不能被当成队长。

        日志原文：`训练计划：队长「星期」不在角色表里 → 沿用「训练流」`。
        """
        texts = [box(184, 170, "星期", 68, 37),
                 box(339, 96, "的训练计划", 294, 62),
                 box(365, 170, "星期二", 87, 35),
                 box(547, 171, "星期三", 90, 34),
                 box(357, 280, "运控训练", 108, 37),
                 box(539, 283, "投射训练", 108, 34),
                 box(721, 283, "意识训练", 108, 32)]
        task = FakeTask(texts)
        self.assertIsNone(task.read_plan_captain(texts))
        self.assertTrue(task.handle_training_plan(texts))
        self.assertIsNone(task._style)                 # 不能写成「星期」
        self.assertEqual(task.style, "训练流")          # 兜底
        self.assertLess(task._style_probe_tries, 3)    # 还要允许再试

    def test_calendar_prefers_roster_name_over_week_header(self):
        """名字和「星期」列头同时在时，取能查到流派的那个人名。"""
        texts = [box(184, 74, "真崎", 158, 91),
                 box(339, 96, "的训练计划", 294, 62),
                 box(184, 170, "星期", 68, 37),
                 box(365, 170, "星期二", 87, 35),
                 box(357, 280, "投射训练", 108, 37)]
        task = FakeTask(texts)
        self.assertEqual(task.read_plan_captain(texts), "真崎")

    def test_training_day_dialogue_is_not_calendar_title(self):
        """训练日台词「今天的训练计划，我看看」不在左上角，不能当队长名。"""
        texts = [box(600, 400, "今天的训练计划，我看看", 500, 50),
                 box(30, 20, "第1周", 120, 40),
                 box(200, 20, "星期五", 130, 40)]
        task = FakeTask(texts)
        self.assertIsNone(task.read_plan_captain(texts))

    def test_style_for_name_uses_roster_prefix(self):
        """日历只显示姓氏，按前缀查角色表的流派。"""
        self.assertEqual(FakeTask.style_for_name("真崎"), "训练流")
        self.assertEqual(FakeTask.style_for_name("佐久间"), "训练流")
        self.assertEqual(FakeTask.style_for_name("雨森"), "商店流")
        self.assertEqual(FakeTask.style_for_name("远野"), "小游戏流")
        self.assertEqual(FakeTask.style_for_name("羽漱川"), "扭蛋流")
        self.assertEqual(FakeTask.style_for_name("羽田"), "商店流")
        self.assertEqual(FakeTask.style_for_name("希雅"), "训练流")
        self.assertIsNone(FakeTask.style_for_name("查无此人"))

    def test_calendar_page_sets_style_and_closes(self):
        """日历页：读到队长 → 写流派 → 点右上角关掉（不能留给地点 handler）。"""
        task = FakeTask(list(self.CALENDAR))
        self.assertTrue(task.handle_training_plan(task._texts))
        self.assertEqual(task.style, "训练流")
        self.assertEqual(task.actions[-1][0], "click")
        self.assertGreaterEqual(task.actions[-1][1], int(task.width * 0.94))

    def test_calendar_page_closed_even_when_name_unknown(self):
        """名字认成乱码也要关日历（否则整页挡住训练日，天天卡这里）。"""
        texts = [box(184, 74, "NITAVNIN", 158, 91),
                 box(339, 96, "的训练计划", 294, 62),
                 box(365, 170, "星期二", 87, 35),
                 box(547, 171, "星期三", 90, 34),
                 box(357, 280, "运控训练", 108, 37),
                 box(539, 283, "投射训练", 108, 34),
                 box(721, 283, "意识训练", 108, 32)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_training_plan(texts))
        self.assertGreaterEqual(task.actions[-1][1], int(task.width * 0.94))
        self.assertEqual(task.style, "训练流")       # 查不到 → 兜底

    def test_training_day_dialogue_not_claimed_as_calendar(self):
        """训练日台词「今天的训练计划，我看看」不能被当成日历页（否则会点右上角跳过）。"""
        texts = [box(300, 500, "今天的训练计划，我看看", 600, 50),
                 box(200, 20, "星期四", 130, 40),
                 box(1200, 900, "训练", 120, 60),
                 box(1500, 300, "投射", 100, 40),
                 box(1500, 400, "体能", 100, 40)]
        task = FakeTask(texts)
        self.assertFalse(task.handle_training_plan(texts))
        self.assertEqual(task.actions, [])

    # ---------- 开日历的时机 ----------

    def test_probe_opens_calendar_when_style_unknown(self):
        """接管一局：流派为空 → 训练日主界面点左上角开日历。"""
        texts = [box(30, 20, "第1周", 120, 40), box(200, 20, "星期五", 130, 40),
                 box(380, 16, "120/130", 210, 53),
                 box(1200, 900, "训练", 120, 60), box(1200, 700, "休息", 120, 60)]
        task = FakeTask(texts)
        task.reset_style()
        self.assertIsNone(task._style)
        self.assertTrue(task.handle_style_probe(texts))
        self.assertEqual(task.actions[-1][0], "click")
        self.assertLess(task.actions[-1][1], int(task.width * 0.10))

    def test_probe_skipped_once_style_known(self):
        """流派已经解析过（或全自动流程自己写好了）→ 不再点日历。"""
        texts = [box(30, 20, "第1周", 120, 40), box(200, 20, "星期五", 130, 40),
                 box(1200, 900, "训练", 120, 60), box(1200, 700, "休息", 120, 60)]
        task = FakeTask(texts)
        task._style = "商店流"
        self.assertFalse(task.handle_style_probe(texts))
        self.assertEqual(task.actions, [])

    def test_probe_skipped_on_vacation_page(self):
        """度假/海边合宿页也有「星期+休息」，但左上角不是日历，不能点。"""
        texts = [box(30, 20, "第4周", 120, 40), box(200, 20, "星期三", 130, 40),
                 box(600, 120, "海边合宿", 300, 50),
                 box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]
        task = FakeTask(texts)
        self.assertFalse(task.handle_style_probe(texts))
        self.assertEqual(task.actions, [])
        self.assertEqual(task._style_probe_tries, 0)

    def test_probe_stops_after_three_tries(self):
        """连开 3 次都认不出队长就停手，别一直点左上角。"""
        texts = [box(30, 20, "第1周", 120, 40), box(200, 20, "星期五", 130, 40),
                 box(1200, 900, "训练", 120, 60), box(1200, 700, "休息", 120, 60)]
        task = FakeTask(texts)
        task._style_probe_at = 0.0
        for _ in range(3):
            task._style_probe_at = 0.0
            task.handle_style_probe(texts)
        task._style_probe_at = 0.0
        self.assertFalse(task.handle_style_probe(texts))

    def test_start_training_resets_style(self):
        """点「开始训练」= 新的一局：流派清空，等日历重新认队长。"""
        texts = [box(700, 100, "确认编队", 300, 50),
                 box(900, 800, "开始训练", 240, 60)]
        task = FakeTask(texts)
        task._style = "商店流"
        self.assertTrue(task.handle_confirm_team(texts))
        self.assertIsNone(task._style)
        self.assertEqual(task.style, "训练流")      # 还没认出来时的兜底

    # ---------- 周五休息阈值 ----------

    def test_friday_rest_threshold_by_style(self):
        """周五：训练流不变(40)、其他流派 55、商店流 80；别的日子不变。"""
        base = {"休息体力阈值": 40}

        def limit(style, day):
            task = FakeTask([], config=dict(base))
            task._style = style
            return task.rest_threshold(day)

        self.assertEqual(limit("训练流", "五"), 40)
        self.assertEqual(limit("扭蛋流", "五"), 55)
        self.assertEqual(limit("小游戏流", "五"), 55)
        self.assertEqual(limit("商店流", "五"), 80)
        self.assertEqual(limit("商店流", "四"), 40)

    def test_friday_rest_uses_style_threshold(self):
        """周五 50 体力：扭蛋流休息（门槛 55），不是训练流的 40。"""
        texts = [box(30, 20, "第3周", 120, 40), box(200, 20, "星期五", 130, 40),
                 box(380, 16, "50/130", 210, 53),
                 box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]
        task = FakeTask(texts)
        task._style = "扭蛋流"
        task.try_speed_2x = lambda *a, **k: False   # 零帧永远判不出 2x，跳过那一拍
        self.assertTrue(task.handle_training_day(texts))
        self.assertEqual(task.actions[-1][1], "休息")

    def test_friday_training_style_still_trains(self):
        """训练流周五 50 体力仍然训练（阈值还是配置里的 40）—— 效果不变。"""
        texts = [box(30, 20, "第3周", 120, 40), box(200, 20, "星期五", 130, 40),
                 box(380, 16, "50/130", 210, 53),
                 box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]
        task = FakeTask(texts)
        task._style = "训练流"
        task.try_speed_2x = lambda *a, **k: False
        self.assertTrue(task.handle_training_day(texts))
        self.assertEqual(task.actions[-1][1], "训练")

    # ---------- 餐厅：队友多 → 右边（耗精力）→ 精力不够退回左边 ----------

    def _restaurant(self, stamina=None):
        texts = [box(700, 100, "在餐厅", 300, 50),
                 box(600, 500, "当服务员", 200, 60),
                 box(1000, 500, "桌游GM", 200, 60)]
        if stamina:
            texts.append(box(380, 16, stamina, 210, 53))
        return texts

    def test_restaurant_tie_picks_right_option(self):
        """队友一样多（都数不到）→ 选右边那个消耗精力的选项。"""
        task = FakeTask(self._restaurant())
        task._style = "商店流"
        task.find_teammate_icons = lambda *a, **k: []
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "桌游GM")

    def test_restaurant_picks_option_with_more_teammates(self):
        """左边卡片左上角挂 3 个队友头像 → 选左边（队友多的优先）。"""
        task = FakeTask(self._restaurant())
        task._style = "扭蛋流"
        task.find_teammate_icons = lambda *a, **k: [(450, 400), (500, 400), (550, 400)]
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "当服务员")

    def test_restaurant_low_stamina_takes_left_card(self):
        """精力不够右边那张（默认按 25 算）→ 改选左边不耗精力的（通用规则）。"""
        task = FakeTask(self._restaurant(stamina="10/130"))
        task._style = "商店流"
        task.find_teammate_icons = lambda *a, **k: []
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "当服务员")

    def test_restaurant_enough_stamina_takes_right_card(self):
        """精力够 → 仍然选右边（队友一样多时的默认）。"""
        task = FakeTask(self._restaurant(stamina="90/130"))
        task._style = "商店流"
        task.find_teammate_icons = lambda *a, **k: []
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "桌游GM")

    def test_restaurant_reward_text_is_not_a_cost(self):
        """卡片上的「精力+30」是收益，不能当成消耗（否则好选项会被挡掉）。"""
        texts = self._restaurant(stamina="25/130")
        texts.append(box(1000, 620, "精力+30", 200, 50))
        task = FakeTask(texts)
        task._style = "商店流"
        task.find_teammate_icons = lambda *a, **k: []
        self.assertTrue(task.handle_restaurant(texts))
        self.assertEqual(task.actions[-1][1], "桌游GM")

    def test_restaurant_uses_cost_printed_on_card(self):
        """卡片写明「精力-30」时以卡片为准：精力 25 不够 → 选左边。"""
        texts = self._restaurant(stamina="25/130")
        texts.append(box(1000, 620, "精力-30", 200, 50))
        task = FakeTask(texts)
        task._style = "商店流"
        task.find_teammate_icons = lambda *a, **k: []
        self.assertTrue(task.handle_restaurant(texts))
        self.assertEqual(task.actions[-1][1], "当服务员")


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
        self.assertEqual(task.actions[0], ("click", 1196, 540))   # 80%/+36 = 28.8 > 12，点卡片正中
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
        self.assertEqual(task.actions[0], ("click", 717, 540))    # 100%/+50 = 50 > 24


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


class ShopGrid(unittest.TestCase):
    """商店网格容量：现在 5 件（3 + 2），强化「商品+1」后 6 件（3 + 3）。

    坐标来自 2026-10-27 商店帧实测（1920x1080）：
    列中心 0.223 / 0.357 / 0.490；行名称 0.276 / 0.544；行价格 0.479 / 0.746。

    第二行价格 0.746 距离旧上限 0.75 只有 4px，布局抖一下整行就会消失
    （表现是「第二行商品永远不买」，不报错）。这里把它钉死：
    谁把价格区上限改回 0.75 以下，这条测试直接红。
    """

    COLS = (0.223, 0.357, 0.490)
    ROWS = ((0.276, 0.479), (0.544, 0.746))
    NAMES = ("投射教科书+", "特训牛奶", "精力饮料",
             "器材手册", "弹跳秘籍", "防守教科书")
    PRICES = (150, 50, 30, 80, 60, 100)

    def make(self):
        texts = []
        for i, name in enumerate(self.NAMES):
            cx = self.COLS[i % 3]
            name_y, price_y = self.ROWS[i // 3]
            texts.append(box(int(1920 * cx) - 75, int(1080 * name_y) - 21, name, 150, 42))
            texts.append(box(int(1920 * cx) - 35, int(1080 * price_y) - 18,
                             str(self.PRICES[i]), 70, 36))
        return FakeTask(texts)

    def test_grid_capacity_is_six(self):
        self.assertEqual(FakeTask([]).SHOP_MAX_ITEMS, 6)

    def test_sixth_slot_is_parsed(self):
        """第 6 格（第二行第三列）必须被读出来，而且名称与价格配成对。"""
        task = self.make()
        items = task.read_shop_items(task._texts)
        self.assertEqual(
            sorted((i["name"], i["price"]) for i in items),
            sorted(zip(self.NAMES, self.PRICES)))

    def test_click_point_between_name_and_price(self):
        """点击点要落在名称与价格之间，否则会点到数量或空白。"""
        task = self.make()
        item = next(i for i in task.read_shop_items(task._texts) if i["name"] == "防守教科书")
        self.assertGreater(item["y"], int(1080 * 0.544))
        self.assertLess(item["y"], int(1080 * 0.746))


class PurchaseRules(unittest.TestCase):
    """商店 / 游戏厅的购买控制（用户 2026-10-29 口径）。

    - **不限制购买次数**：羽漱川湊能买两次、卧云真昼能买到升级版、
      白神时久多一件免费商品，一天买几件都可能是正常的；
    - 要防的是「点了没成交还一直点」（2026-10-29 实跑：游戏厅买了 237 次）；
    - 免费商品（0 元）不花零用钱，余额不足 / 读不到钱时也应该拿。
    """

    COLS = (0.223, 0.357, 0.490)

    def _shop(self, items, money=80, quality="金"):
        """items = [(名字, 价格)]，价格写 "免费" 表示 0 元卡。"""
        texts = [box(760, 60, "商店", 160, 40),
                 box(1200, 60, "上新时间", 200, 30)]
        if money is not None:
            texts.append(box(1100, 20, str(money), 80, 40))
        for i, (name, price) in enumerate(items):
            cx = self.COLS[i % 3]
            texts.append(box(int(1920 * cx) - 75, int(1080 * 0.30) - 21, name, 150, 42))
            texts.append(box(int(1920 * cx) - 35, int(1080 * 0.45) - 18,
                             str(price), 70, 36))
        task = FakeTask(texts, config={"购买策略": "只买金和紫"})
        task.read_money = lambda texts=None: money
        task.card_quality = lambda name, pt: (quality, 30, 150)
        return task, texts

    def _arcade(self, money=300, price="64", quality="金"):
        texts = [
            box(600, 115, "请选择一个小游戏", 220, 36),
            box(199, 236, "自动", 64, 30),
            box(308, 238, "弹跳", 70, 42),
            box(251, 370, "弹跳20~70", 155, 31),
            box(355, 484, price, 44, 32),
            box(615, 646, "确定", 71, 39),
        ]
        task = FakeTask(texts, config={"游戏厅付费": True})
        task.read_money = lambda texts=None: money
        task.card_quality_at = lambda cx, y1, y2: (quality, 30, 150)
        return task, texts

    def test_shop_buys_free_item_when_poor(self):
        """白神时久：0 元商品不花零用钱，余额 80 也要买。"""
        task, texts = self._shop([("器材手册+", 150), ("精力饮料", 0)])
        self.assertTrue(task.handle_shop(texts))
        self.assertIn("买「精力饮料」", "".join(task.logs))

    def test_shop_buys_affordable_purple_without_gate(self):
        """2026-10-29 实跑回归：零用钱 120、紫卡 100 —— 钱够就买，不再看 300 门槛。"""
        task, texts = self._shop([("弹跳教科书", 100)], money=120, quality="紫")
        self.assertTrue(task.handle_shop(texts))
        self.assertIn("买「弹跳教科书」", "".join(task.logs))

    def test_shop_closes_when_nothing_affordable(self):
        """买不起任何东西才关店（不是「余额低于门槛」）。"""
        task, texts = self._shop([("器材手册+", 150)], money=80)
        self.assertTrue(task.handle_shop(texts))
        self.assertIn("没有买得起的商品了", "".join(task.logs))

    def test_shop_unknown_money_buys_nothing_paid(self):
        """零用钱读不到时不盲买（旧游戏厅死循环就是这么来的）。"""
        task, texts = self._shop([("器材手册+", 100)], money=None)
        self.assertTrue(task.handle_shop(texts))
        self.assertFalse(any("器材手册" in str(a) for a in task.actions))

    def test_arcade_buys_twice_when_money_changes(self):
        """羽漱川湊能买两次：钱变了就不算「没成交」，不设次数上限。"""
        task, texts = self._arcade(money=300)
        self.assertTrue(task.handle_arcade(texts))
        self.assertIn("买了", task.logs[-1])
        task.read_money = lambda texts=None: 200          # 第一件已扣钱
        self.assertTrue(task.handle_arcade(texts))
        self.assertIn("买了", task.logs[-1])

    def test_arcade_stops_after_three_stalled_clicks(self):
        """零用钱没变 + 同一张卡还在 → 第 3 次判定「点不动」，退出而不是继续买。"""
        task, texts = self._arcade(money=300)
        self.assertTrue(task.handle_arcade(texts))
        self.assertTrue(task.handle_arcade(texts))
        self.assertTrue(task.handle_arcade(texts))
        self.assertIn("没成交", "".join(task.logs))
        self.assertEqual(task.actions[-1][0], "click")     # exit_page 的右上角返回

    def test_arcade_unknown_money_buys_nothing(self):
        task, texts = self._arcade(money=None, price="100")
        self.assertTrue(task.handle_arcade(texts))
        self.assertNotIn("买了", "".join(task.logs))
        self.assertIn("零用钱读不到", "".join(task.logs))

    def test_arcade_free_card_bought_without_money(self):
        task, texts = self._arcade(money=None, price="0")
        self.assertTrue(task.handle_arcade(texts))
        self.assertIn("买了", task.logs[-1])


class OutingOnTrainingDay(unittest.TestCase):
    """野上泷：周五的训练日主界面也会出现「出行」入口（用户 2026-10-29）。"""

    def _texts(self):
        return [
            box(37, 18, "第3周", 120, 33),
            box(163, 18, "星期五", 130, 35),
            box(310, 16, "130/130", 185, 38),
            box(850, 21, "80", 55, 30),
            box(1320, 548, "休息", 105, 59),
            box(1299, 708, "训练", 156, 83),
            box(1378, 748, "出行", 110, 40),
        ]

    def test_goes_out_before_training_once(self):
        task = FakeTask(self._texts())
        task.try_speed_2x = lambda texts: False       # 倍速按钮靠像素判，测试里没有帧
        self.assertTrue(task.handle_training_day(task._texts))
        self.assertEqual(task.actions[-1][1], "出行")
        # 同一天第二次进来：外出已经用过，不能再点出行（否则会打转）
        self.assertTrue(task.handle_training_day(task._texts))
        self.assertNotEqual(task.actions[-1][1], "出行")


class ArcadeAnimationWait(unittest.TestCase):
    """游戏厅小游戏会自己打完，期间只能等（用户 2026-10-29 口径）。"""

    def _task(self):
        task = FakeTask([])
        task._arcade_wait_since = time.time()
        return task

    def test_blank_animation_keeps_waiting(self):
        """只剩顶栏文字（角色站球场上）→ 继续等，不能点也不能算卡死。"""
        texts = [box(37, 18, "第3周", 89, 35), box(161, 18, "星期六", 89, 35),
                 box(305, 13, "38/130", 169, 45), box(862, 22, "20", 34, 24)]
        self.assertTrue(self._task().arcade_still_playing(texts))

    def test_known_page_ends_the_wait(self):
        texts = [box(37, 18, "第3周", 89, 35),
                 box(1320, 548, "休息", 105, 59),
                 box(1299, 708, "训练", 156, 83)]
        self.assertFalse(self._task().arcade_still_playing(texts))

    def test_no_flag_is_not_waiting(self):
        texts = [box(37, 18, "第3周", 89, 35)]
        self.assertFalse(FakeTask([]).arcade_still_playing(texts))


class ArcadeTeammatePriority(unittest.TestCase):
    """游戏厅选卡：有队友的优先（用户 2026-10-29）。

    旧逻辑只按「奖励上限」排序，中间那张带队友的金卡会被上限更高的无队友卡压掉。
    """

    def _texts(self, cards):
        """cards = [(属性, 下限, 上限, 价格)]，三列并排（列中心 0.19/0.50/0.81）。"""
        texts = [box(600, 60, "请选择一个小游戏", 400, 40)]
        for i, (attr, lo, hi, price) in enumerate(cards):
            cx = int(1920 * (0.19 + 0.31 * i))
            texts.append(box(cx - 40, 120, "自动", 64, 30))
            texts.append(box(cx - 35, 180, attr, 70, 42))
            texts.append(box(cx - 80, 300, f"{attr}{lo}~{hi}", 160, 40))
            texts.append(box(cx - 20, 420, str(price), 60, 40))
        texts.append(box(860, 950, "确定", 200, 60))
        return texts

    def _task(self, teammate_col):
        task = FakeTask([])
        task.card_quality_at = lambda cx, y1, y2: ("金", 40, 200)
        task.read_money = lambda texts=None: 300
        def icons(x_from=0.78, x_to=1.0):
            if teammate_col is None:
                return []
            cx = int(1920 * (0.19 + 0.31 * teammate_col))
            return [(cx, 200), (cx + 20, 200)]
        task.find_teammate_icons = icons
        return task

    def test_prefers_card_with_teammate(self):
        texts = self._texts([("弹跳", 20, 70, 64), ("运控", 20, 60, 64),
                             ("投射", 30, 100, 80)])
        task = self._task(teammate_col=1)          # 中间那张有队友
        self.assertTrue(task.handle_arcade(texts))
        self.assertIn("买了「运控」", "".join(task.logs))

    def test_without_teammate_falls_back_to_reward(self):
        texts = self._texts([("弹跳", 20, 70, 64), ("运控", 20, 60, 64),
                             ("投射", 30, 100, 80)])
        task = self._task(teammate_col=None)
        self.assertTrue(task.handle_arcade(texts))
        self.assertIn("买了「投射」", "".join(task.logs))


class TalentStockEvent(unittest.TestCase):
    """2026-10-29 闪堂电天赋「股神!」事件：选项是整句，右上是灰的「跳过」。

    真帧 docs/shots/2026-10-29-event-sim-software.png（1549x871）：
        含只是模拟软件而已，让她们试试 (371,299,714,97)
        不许她们乱来                 (647,440,253,44)
    旧判据认为「选项=短名词、不带句读」，这两条全被筛掉 → dialog_continue
    反复点灰「跳过」，卡在原地（用户当场看到）。
    """

    def _texts(self):
        return [box(371, 299, "含只是模拟软件而已，让她们试试", 714, 97),
                box(647, 440, "不许她们乱来", 253, 44),
                box(1347, 96, "跳过", 110, 40)]

    def test_options_are_recognised(self):
        task = FakeTask(self._texts())
        names = [b.name for b in task.options_not_paragraph(task._texts)]
        self.assertEqual(names, ["含只是模拟软件而已，让她们试试", "不许她们乱来"])

    def test_clicks_the_first_option(self):
        task = FakeTask(self._texts())
        self.assertTrue(task.handle_event_choice(task._texts))
        self.assertEqual(task.actions[-1][0], "click_box")
        self.assertIn("只是模拟软件", task.actions[-1][1])

    def test_dialog_continue_yields_to_choice(self):
        """有整句选项时不能再点「跳过」（点了没反应，每拍重复）。"""
        task = FakeTask(self._texts())
        self.assertFalse(task.handle_dialog_continue(task._texts))
        self.assertEqual(task.actions, [])

    def test_stock_page_picks_left_card(self):
        """天赋「股神!」后续：请选择你要投资的股票 → 选买得起的第一张，再确定。"""
        texts = [box(700, 130, "请选择你要投资的股票", 500, 50),
                 box(1150, 90, "104", 80, 40),
                 box(430, 495, "稳健型", 120, 50),
                 box(815, 495, "平衡型", 120, 50),
                 box(1200, 495, "风险型", 120, 50),
                 box(480, 760, "24", 60, 40),
                 box(870, 760, "120", 60, 40),
                 box(1255, 760, "200", 60, 40),
                 box(540, 960, "放弃", 280, 60),
                 box(1030, 960, "确定", 280, 60)]
        task = FakeTask(texts)
        task.read_money = lambda texts=None: 104
        self.assertTrue(task.handle_stock_pick(texts))
        first = task.actions[0]
        self.assertEqual(first[0], "click")
        self.assertLess(first[1], int(task.width * 0.33))      # 最左那张
        self.assertIn("选「稳健型」", "".join(task.logs))
        self.assertEqual(task.actions[-1][1], "确定")

    def test_stock_page_skips_unaffordable(self):
        """最左那张买不起（24 > 10）→ 往右挑第一张买得起的。"""
        texts = [box(700, 130, "请选择你要投资的股票", 500, 50),
                 box(430, 495, "稳健型", 120, 50),
                 box(815, 495, "平衡型", 120, 50),
                 box(480, 760, "24", 60, 40),
                 box(870, 760, "8", 60, 40),
                 box(1030, 960, "确定", 280, 60)]
        task = FakeTask(texts)
        task.read_money = lambda texts=None: 10
        self.assertTrue(task.handle_stock_pick(texts))
        self.assertIn("选「平衡型」", "".join(task.logs))

    def test_stock_page_prefers_teammate_bubble_then_price(self):
        """151 币、41/120/50：中间和右边都有队友气泡 → 取价格高的中间那张。"""
        texts = [box(700, 130, "请选择你要投资的股票", 500, 50),
                 box(1150, 95, "151", 80, 40),
                 box(430, 495, "稳健型", 120, 50),
                 box(815, 495, "平衡型", 120, 50),
                 box(1200, 495, "稳健型", 120, 50),
                 box(480, 760, "41", 60, 40),
                 box(870, 760, "120", 60, 40),
                 box(1255, 760, "50", 60, 40),
                 box(900, 330, "一定能赚!", 200, 40),
                 box(1290, 330, "我梭哈了", 200, 40),
                 box(1030, 960, "确定", 280, 60)]
        task = FakeTask(texts)
        task.read_money = lambda texts=None: 151
        self.assertTrue(task.handle_stock_pick(texts))
        self.assertIn("选「平衡型」(120)", "".join(task.logs))
        self.assertIn("队友1", "".join(task.logs))

    def test_stock_result_clicks_sell(self):
        """股票走势页只能点「卖出」。"""
        texts = [box(600, 240, "第2周股票走势", 500, 60),
                 box(950, 620, "涨跌幅-20.0%", 260, 50),
                 box(700, 940, "卖出", 300, 60)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_stock_result(texts))
        self.assertEqual(task.actions[-1][1], "卖出")

    def test_stock_ranking_clicks_next(self):
        """炒股日全部结束后的「炒股队收益排名」→ 点下一步。"""
        texts = [box(550, 180, "炒股队收益排名", 500, 60),
                 box(600, 300, "清水 万里", 300, 50),
                 box(600, 400, "亚理纱 贾斯敏", 300, 50),
                 box(650, 810, "下一步", 290, 60)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_stock_result(texts))
        self.assertEqual(task.actions[-1][1], "下一步")

    def test_skill_prefers_item_card(self):
        """技能三选一：左上角有道具宝石（紫/粉）的卡优先。

        2026-10-29 真机：左卡是书图标、中/右卡是道具小卡+宝石；
        机器人当时选了左边那张 → 用户要求优先有道具的。
        """
        from src.tasks.ui import Defs
        texts = [box(600, 200, "请选择1个技能", 400, 50),
                 box(200, 420, "好斗者", 200, 60),
                 box(700, 420, "远投擅长", 200, 60),
                 box(1200, 420, "看破擅长", 200, 60)]
        task = FakeTask(texts)
        task._card_quality = lambda frame, i: 1          # 三张同品质
        task.skill_card_item_gem = lambda cx, cy: cx > 0.4   # 中/右有道具
        self.assertTrue(task.handle_skill_select(texts))
        self.assertAlmostEqual(task.actions[0][1] / task.width_of_screen(1),
                               Defs.SKILL_CARDS[1][0], places=2)
        self.assertIn("道具1", "".join(task.logs))

    def test_no_separate_stock_button_on_training_day(self):
        """炒股**不是**训练日主按钮：入口只有「二选一」选第一个。

        用户 2026-10-29 纠正：主界面没有「炒股」按钮，别在训练日乱点。
        """
        texts = [box(30, 20, "第2周", 120, 40), box(200, 20, "星期四", 130, 40),
                 box(380, 16, "92/130", 210, 53),
                 box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]
        task = FakeTask(texts)
        task.try_speed_2x = lambda *a, **k: False
        self.assertTrue(task.handle_training_day(texts))
        self.assertEqual(task.actions[-1][1], "训练")


class RegisterWatchdog(unittest.TestCase):
    """注册球员看门狗的拍数上限只在**进入流程后**才计（2026-10-29 实跑误报 25 次）。"""

    def test_outside_flow_does_not_count_ticks(self):
        texts = [box(37, 18, "第3周", 120, 33),
                 box(163, 18, "星期三", 130, 35),
                 box(310, 16, "80/130", 185, 38)]
        task = FakeTask(texts)
        for _ in range(30):
            self.assertFalse(task.handle_register_player(texts))
        self.assertEqual(getattr(task, "_reg_ticks", 0), 0)
        self.assertFalse(any("没推进" in m for m in task.logs))


if __name__ == "__main__":
    unittest.main()
