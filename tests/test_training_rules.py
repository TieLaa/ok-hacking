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
        self.assertAlmostEqual(FakeTask.subject_score(r), 2 + 1 + 2.1 + 1.2 + 1.3, places=4)

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
        self.assertAlmostEqual(task.subject_score(r), 1 + 1.3, places=4)

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

    def _home_texts(self, day):
        return [box(30, 20, "第1周", 120, 40),
                box(200, 20, day, 130, 40),
                box(380, 16, "120/130", 210, 53),
                box(950, 560, "商店", 130, 50),
                box(1150, 700, "出行", 160, 70)]

    def test_sunday_enters_shop_too(self):
        """周六、周日首页都有商店入口（用户 2026-10-27 现场确认）。

        旧版判据里写了 `day != "日"`，把周日挡掉，等于每周少逛一次店。
        """
        task = FakeTask(self._home_texts("星期日"), config={"流派": "商店流"})
        self.assertTrue(task.handle_go_out(task._texts))
        self.assertEqual(task.actions[-1][0], "click_box")
        self.assertEqual(task.actions[-1][1], "商店")

    def test_saturday_enters_shop(self):
        task = FakeTask(self._home_texts("星期六"), config={"流派": "商店流"})
        self.assertTrue(task.handle_go_out(task._texts))
        self.assertEqual(task.actions[-1][1], "商店")

    def test_shop_only_once_per_day(self):
        """同一天不重复进店：第二拍直接点「出行」。"""
        task = FakeTask(self._home_texts("星期日"), config={"流派": "商店流"})
        task.handle_go_out(task._texts)
        task.actions.clear()
        self.assertTrue(task.handle_go_out(task._texts))
        self.assertEqual(task.actions[-1][1], "出行")

    def test_training_style_never_enters_shop(self):
        """训练流不逛商店（钱优先换步行街/游戏厅的黄道具）。"""
        task = FakeTask(self._home_texts("星期六"), config={"流派": "训练流"})
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


if __name__ == "__main__":
    unittest.main()
