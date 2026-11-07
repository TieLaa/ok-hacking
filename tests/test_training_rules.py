"""训练规则的纯逻辑测试（不启动游戏、不跑 OCR）。

为什么要这些测试：
    回归图只能验证「这张图命中哪个 handler」，验证不了「选哪张卡 / 点哪里」。
    2026-10-26 的两次事故（72 精力买清空扭蛋、事件二选一点中间空白）
    都属于「handler 命中了但决策错了」，必须用构造数据测。

运行：
    .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_training_rules.py" -v
"""

import glob
import json
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
    CRIT_RING_FACTOR, CRIT_RING_MIN, ring_is_border)
from src.tasks.training.constants import GACHA_WORK_MONEY_GATE, WEEKEND_PLAN  # noqa: E402
from src.tasks.ui import topbar_day  # noqa: E402


def box(x, y, name, w=240, h=60):
    return Box(x=x, y=y, width=w, height=h, name=name)


def tick_boost(task, texts, ticks=2):
    """跑加强训练 handler 到「真正动手」的那一拍，清掉之前的观察拍动作。

    2026-11-02 起这个 handler **头一拍只观察不点**（躲页面淡入，用户反馈
    「第一下点卡片没点下去」），所以只调一次只能看到「头一拍先不点」。
    """
    for _ in range(max(0, ticks - 1)):
        task.handle_boost_training(texts)
    task.actions.clear()
    return task.handle_boost_training(texts)


class FakeTask(DryRunTask):
    """可注入品质/队友的 DryRunTask（取色逻辑被替换）。"""

    def __init__(self, texts, qualities=None, mates=None, config=None):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        super().__init__(frame, texts)
        # config 用**真实的 default_config**（DryRunTask 已经填好了），
        # 只覆盖「无队长不接开局」和测试自己传进来的项。
        #
        # 2026-11-01 之前这里是 `{"无队长不接开局": False, **config}` ——
        # 只留一个键，靠「每个调用处都写 config.get(key, 默认值)」兜底。
        # 去掉调用处的死默认值之后（默认值只留在 config_spec 一处），
        # 缺键会拿到 **None**（falsy）→ 「允许进步行街」被当成 False，
        # 一批测试挂掉。**夹具必须和生产一样**，否则测的就不是真实行为。
        #
        # 「无队长不接开局」关掉：测试测的是各 handler 的判据/顺序，
        # 不是「无队长模式」这道闸门；不关掉的话所有开局页 handler
        # 都会直接停手，测什么都没动作。
        self.config["无队长不接开局"] = False
        self.config.update(config or {})
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
    """cards = [(名字, 效果), ...]，生成扭蛋页的 OCR 框。

    某张卡的 price 给 `("原价", "实付价")` 二元组，就生成「打折」卡：
    价格药丸里两个数字都被 OCR 读到（截图里 100 被划掉、右边 90 才是实付价）。
    """
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
        if isinstance(p, (list, tuple)):
            texts.append(box(xs[i] - 70, 703, p[0], 60, 22))    # 划掉的原价（小字，左边）
            texts.append(box(xs[i], 700, p[1], 80, 55))         # 实付价
        else:
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

    def test_settle_takes_frame_after_slow_wait(self):
        """3 拍都还在变 → 再等一拍慢的，用**定下来那帧**，不是循环里最后一帧。

        2026-10-31 23:41 实跑就是拿快速循环的最后一帧（淡入中间态）去买的。
        """
        def frame(p):
            return gacha_texts([
                ("运控增加", "主力运控+30，队友属性小幅提升"),
                ("防守升级包++", "防守属性+40，防守训练的训练等级+3"),
                ("弹跳专注", "消耗所有精力，训练主力获得弹跳+48"),
            ], stamina="25/130", money="359", price=[("60", p[0]), ("100", p[1]), ("60", p[2])])

        task = FakeTask(frame(("50", "90", "50")), qualities=["蓝", "金", "蓝"],
                        mates=[False, True, False])
        # 前 3 帧价格一直在滚动（每次都不一样），第 4 帧才定在 50/90/50
        task.ocr_queue = [frame(("48", "88", "47")),
                          frame(("49", "87", "49")),
                          frame(("48", "85", "48")),
                          frame(("50", "90", "50"))]
        cards = task.settle_gacha_cards(frame(("100", "100", "100")))
        self.assertEqual([c["price"] for c in cards], [50, 90, 50])
        self.assertEqual([c["mate"] for c in cards], [False, True, False])

    def test_gacha_signature_includes_quality_and_mate(self):
        """名字+价格一致、品质/队友不一致 → 指纹不算一致（画面还没定下来）。"""
        def sig(quality, mate):
            return FakeTask._gacha_signature([{
                "name": "运控增加", "price": 50, "quality": quality, "mate": mate,
            }])

        self.assertNotEqual(sig("金", True), sig("蓝", False))
        self.assertNotEqual(sig("金", True), sig("金", False))
        self.assertEqual(sig("金", True), sig("金", True))

    def test_discount_price_pill_is_one_card(self):
        """打折卡的价格药丸里两个数字（原价 + 实付价）只算一张卡，取实付价。

        2026-10-31 23:41 实跑：左边那张 运控增加 被读成 60 币和 50 币两张，
        日志里 5 张卡、两个 321 分打平 → max() 取了第一张（最左边）。
        """
        texts = gacha_texts([
            ("运控增加", "主力运控+30，队友属性小幅提升"),
            ("防守升级包++", "防守属性+40，防守训练的训练等级+3"),
            ("弹跳专注", "消耗所有精力，训练主力获得弹跳+48"),
        ], stamina="25/130", money="359",
            price=[("60", "50"), ("100", "90"), ("60", "50")])
        task = FakeTask(texts, qualities=["蓝", "金", "蓝"],
                        mates=[False, True, False])
        cards = task.read_gacha_cards(texts)
        self.assertEqual([c["name"] for c in cards],
                         ["运控增加", "防守升级包++", "弹跳专注"])
        self.assertEqual([c["price"] for c in cards], [50, 90, 50])

    def test_discount_page_picks_gold_mate_card(self):
        """2026-10-31 23:41 用户截图：左蓝(60→50) / 中金+队友(100→90) / 右蓝(60→50)。

        精力 25（低于清空阈值 30），右边那张「消耗所有精力」也是候选，
        但金+队友那张分最高 → 必须点中间那张。
        """
        texts = gacha_texts([
            ("运控增加", "主力运控+30，队友属性小幅提升"),
            ("防守升级包++", "防守属性+40，防守训练的训练等级+3"),
            ("弹跳专注", "消耗所有精力，训练主力获得弹跳+48"),
        ], stamina="25/130", money="359",
            price=[("60", "50"), ("100", "90"), ("60", "50")])
        task = FakeTask(texts, qualities=["蓝", "金", "蓝"],
                        mates=[False, True, False])
        self.assertTrue(task.handle_street(texts))
        self.assertEqual(task.actions[0][0], "click")
        self.assertEqual(task.actions[0][1], 1100)          # 中间那张卡的卡名中心

    def test_tie_breaks_towards_cheaper_not_leftmost(self):
        """同分不再「按 OCR 顺序取第一张」：一样有队友时选便宜的那张。"""
        task = FakeTask(gacha_texts([
            ("运控增加++", "主力运控+72，队友属性大幅提升"),
            ("防守升级包++", "防守属性+72，队友属性大幅提升"),
            ("弹跳增加", "主力弹跳+20"),
        ], stamina="25/130", money="359", price=[("100", "90"), ("80", "70"), "50"]),
            qualities=["金", "金", "蓝"], mates=[True, True, False])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][1], 1100)          # 70 币那张，不是左边的 90 币

    def test_colorful_card_beats_gold(self):
        """2026-11-01 实拍（第3周周日 57/130、零用钱 524）：左金 / **中彩色** / 右金。

        彩色卡的卡头是整条彩虹渐变，中位色相落在紫区（H≈121），旧判据判成「紫」→
        200 分，被 300 分的金卡压掉，于是买了左边那张。现在彩色 400 分必须胜出。
        """
        task = FakeTask(gacha_texts([
            ("推荐属性++", "主力两项推荐属性+36，队友属性大幅提升"),
            ("体能增加+++", "主力体能+100，队友属性巨幅提升"),
            ("运控升级包++", "运控属性+40，运控训练的训练等级+3"),
        ], stamina="57/130", money="524",
            price=[("100", "90"), ("120", "110"), ("100", "90")]),
            qualities=["金", "彩", "金"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][0], "click")
        self.assertEqual(task.actions[0][1], 1100)          # 中间那张彩色卡

    def test_same_quality_prefers_enhanced_over_mate(self):
        """同分先看「增强款」，再看队友（用户 2026-11-01：「同分排序 增强款 > 队友」）。

        两者都只做同分比较（不再各自 +30/+20 塞进 score），但增强排在前面。
        """
        task = FakeTask(gacha_texts([
            ("投射增强++", "主力投射+72"),
            ("运控增加++", "主力运控+72，队友属性大幅提升"),
            ("弹跳增加", "主力弹跳+20"),
        ], stamina="57/130", money="524", price="90"),
            qualities=["金", "金", "蓝"], mates=[False, True, False])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][1], 620)           # 增强款那张（左）

    def test_mate_breaks_tie_when_nobody_enhanced(self):
        """都没有增强款时，队友优先（同品质同价 → 选带队友的）。"""
        task = FakeTask(gacha_texts([
            ("投射增加++", "主力投射+72"),
            ("运控增加++", "主力运控+72，队友属性大幅提升"),
        ], stamina="57/130", money="524", price="90"),
            qualities=["金", "金"], mates=[False, True])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][1], 1100)          # 带队友的右边那张

    def test_mate_card_beats_stamina_recovery(self):
        """2026-11-03 10:11 实跑：三张卡全带「增强款」，买错了中间那张恢复卡。

        实拍（第2周星期五，54/130、398 币）：左蓝「运控专注」50 币 /
        中紫「精力恢复+」70 币 / 右紫「意识增加+」70 币（**卡头有队友标记**）。
        日志原文：
            `精力恢复+(紫·增强·恢复110/70币/138)、意识增加+(紫+队友·增强/70币/100) → 选「精力恢复+」`
        识别全对 —— 错在恢复量**直接加进了 score**（+38），而队友只是
        「分数完全相同」时的 tie-break，于是永远轮不到。
        现在顺序是 品质 → 增强款 → 队友 → 恢复价值 → 便宜：右边那张必须胜出。
        """
        texts = gacha_texts([
            ("运控专注", "消耗所有精力，训练主力获得运控+48"),
            ("精力恢复+", "立刻恢复60精力，之后每天恢复20点，持续4天"),
            ("意识增加+", "主力意识+48，队友属性中幅提升"),
        ], stamina="54/130", money="398",
            price=[("60", "50"), ("80", "70"), ("80", "70")])
        # 「增强款」斜标签贴在卡头（y≈0.30），三张都有 —— 实拍帧 OCR 同样读到三份。
        for x in (480, 960, 1440):
            texts.append(box(x - 60, 300, "增强款", 100, 40))
        task = FakeTask(texts, qualities=["蓝", "紫", "紫"],
                        mates=[False, False, True])
        self.assertTrue(task.handle_street(texts))
        self.assertGreaterEqual(task.actions[0][1], 1300)   # 最右边那张（带队友）

    def test_plus_suffix_is_not_score(self):
        """名字里的「+」不算分（用户 2026-11-02）。

        实拍（第6周周日 105/130、零用钱 561）：左紫「运控增加+」80→70 /
        中金「随机加成」100→90（带队友）/ 右金「体能增加++」100→90。
        旧代码给名字带「+」的卡 +1 分 → 右边 301 > 中间 300，买了没队友的那张
        （日志原文 `随机加成(金·+队友/90币/200)、体能增加++(金/90币/201)`）。
        「+」只表示**同名卡片**之间的档位差，没「+」的同名卡本来就是紫/蓝，
        已经由品质体现，不能再算一次分 → 同分时队友胜出，必须点中间那张。
        """
        task = FakeTask(gacha_texts([
            ("运控增加+", "主力运控+48，队友属性中幅提升"),
            ("随机加成", "训练主力随机一项属性+80，队友属性大幅提升"),
            ("体能增加++", "主力体能+72，队友属性大幅提升"),
        ], stamina="105/130", money="561",
            price=[("80", "70"), ("100", "90"), ("100", "90")]),
            qualities=["紫", "金", "金"], mates=[False, True, False])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][1], 1100)          # 中间那张带队友的金卡

    def test_quality_beats_mate_bonus(self):
        """品质是第一权重：金卡没队友，也压过紫卡+队友（用户 2026-11-01）。"""
        task = FakeTask(gacha_texts([
            ("投射增加++", "主力投射+72"),
            ("运控增加++", "主力运控+72，队友属性大幅提升"),
        ], stamina="57/130", money="524", price="90"),
            qualities=["金", "紫"], mates=[False, True])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions[0][1], 620)           # 左边那张金卡

    def test_blue_card_never_bought_even_with_mate(self):
        """蓝卡再带队友也不买（配置「扭蛋品质优先」里没有蓝）。"""
        task = FakeTask(gacha_texts([
            ("训练增强", "训练可以额外获得1点主属性，队友属性大幅提升"),
            ("防守增加", "主力防守+30"),
        ], stamina="57/130", money="524", price="90"),
            qualities=["蓝", "蓝"], mates=[True, True])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions, [("click", 1837, 50)])   # 右上角 ↩ 退出

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

    # ---------- 2026-11-01 15:09 事故：卡面出来了、价格还没出来 ----------

    def _no_price_page(self):
        """卡面已经画出来、但价格数字还没渲染的那一拍。

        实拍 OCR（15:09:19,564）：`请选择1个扭蛋i / 训练增强 / 运控增加+++ /
        防守增加+ / 主力运控+100，队友属性 / 主力防守+48，队友属性 / 确定 / 能力详情`
        —— **没有任何价格数字**。对比 10:22 成功那帧有 `80`/`00`/`70`/`100`。

        版式跟 `gacha_texts` 一致（同样 1920x1080、同样三列），只是少了价格药丸。
        """
        texts = [box(760, 80, "请选择1个扭蛋i", 400, 70),
                 box(380, 16, "130/130", 210, 53),
                 box(1050, 20, "169", 80, 40)]
        xs = [480, 960, 1440]
        names = ["训练增强", "运控增加+++", "防守增加+"]
        effects = ["训练可以额外获得1点主属性", "主力运控+100，队友属性巨幅提升",
                   "主力防守+48，队友属性大幅提升"]
        for i in range(3):
            texts.append(box(xs[i], 330, names[i], 280, 60))
            texts.append(box(xs[i], 520, effects[i], 300, 60))
        texts.append(box(860, 950, "确定", 200, 60))
        return texts

    def test_cards_found_without_price(self):
        """价格没渲染出来时，卡面**照样要认出来**（价格是 None）。

        旧代码靠价格药丸定位卡片 → 没价格 = 没卡 → 白丢一次扭蛋。
        """
        page = self._no_price_page()
        task = FakeTask(page, qualities=["紫", "金", "紫"])
        cards = task.read_gacha_cards(page)
        self.assertEqual(len(cards), 3)
        self.assertEqual([c["name"] for c in cards],
                         ["训练增强", "运控增加+++", "防守增加+"])
        self.assertEqual([c["price"] for c in cards], [None, None, None])

    def test_waits_for_price_to_render(self):
        """卡面先出、价格后出：必须**继续等**，等价格出来再买。

        2026-11-01 15:09 实跑：19.5s 那拍卡面已出、价格未出，旧判据
        「连续两拍读不到卡就退出」正好卡在这个中间态 → 退出 → 白丢一次扭蛋。
        """
        page = self._no_price_page()
        priced = gacha_texts([
            ("训练增强", "训练可以额外获得1点主属性"),
            ("运控增加+++", "主力运控+100，队友属性巨幅提升"),
            ("防守增加+", "主力防守+48，队友属性大幅提升"),
        ], stamina="130/130", money="169", price="80")
        task = FakeTask(page, qualities=["紫", "金", "紫"], mates=[False, True, False])
        task.ocr_queue = [priced, priced, priced, priced]
        self.assertTrue(task.handle_street(page))
        # 买的是中间那张（运控增加+++，金+队友）—— 不是退出
        self.assertEqual(task.actions[0][0], "click")
        self.assertEqual(task.actions[0][1], 1100)     # 中间那张卡的卡名中心
        self.assertEqual(task.actions[-1][1], "确定")

    def test_no_price_never_buys(self):
        """价格一直读不到 → 不买、退出（买不起点「确定」会卡在扭蛋页）。"""
        page = self._no_price_page()
        task = FakeTask(page, qualities=["紫", "金", "紫"], mates=[False, True, False])
        task.ocr_queue = [page] * 8
        self.assertTrue(task.handle_street(page))
        self.assertEqual(task.actions, [("click", 1837, 50)])   # 只有右上角 ↩

    def test_failed_read_marks_tried_not_spent(self):
        """没买成 → 记「试过了」，但**不能**记「已经消费」。

        2026-11-01 15:09：原来 `mark_visited` 写在读卡之前，所以没买到也记上了
        「今天来过」，后面再想重进步行街就不可能了。
        现在：进过 = visited；试过 = gacha_tried；买成 = gacha_spent。
        """
        page = self._no_price_page()
        task = FakeTask(page, qualities=["紫", "金", "紫"])
        task.ocr_queue = [page] * 8
        self.assertTrue(task.handle_street(page))
        self.assertIn("步行街", task._weekend.get("gacha_tried") or set())
        self.assertFalse(task._weekend.get("gacha_spent"))

    def test_street_not_entered_twice_after_failure(self):
        """买失败后回到地图，**不能再点一次步行街**（否则无限循环）。

        2026-11-01 15:09 的衍生问题：如果「没买成」不登记，地图上 todo 里
        还留着步行街 → 又点进去 → 又失败 → 循环。
        """
        page = self._no_price_page()
        task = FakeTask(page, qualities=["紫", "金", "紫"])
        task.ocr_queue = [page] * 8
        self.assertTrue(task.handle_street(page))
        # 现在回到地图
        map_texts = [
            box(35, 16, "第1周", 92, 40), box(163, 18, "星期五", 84, 35),
            box(250, 628, "餐厅", 104, 60), box(1010, 897, "步行街", 148, 58),
            box(258, 748, "餐厅", 104, 60), box(1075, 20, "169", 80, 40),
        ]
        task._texts = map_texts
        task.config["允许进步行街"] = True
        task._style = "扭蛋流"
        task._weekend["date"] = (1, "五")
        task.handle_weekend(map_texts)
        clicked = [a for a in task.actions if a[0] == "click_box"]
        self.assertTrue(clicked)
        self.assertNotEqual(clicked[-1][1], "步行街", "不能再次点步行街")
        self.assertEqual(clicked[-1][1], "餐厅")

    def test_successful_buy_marks_visited(self):
        """真的买成了 → 记「步行街已去过」+「已经消费」。"""
        priced = gacha_texts([
            ("训练增强", "训练可以额外获得1点主属性"),
            ("运控增加+++", "主力运控+100，队友属性巨幅提升"),
            ("防守增加+", "主力防守+48，队友属性大幅提升"),
        ], stamina="130/130", money="169", price="80")
        task = FakeTask(priced, qualities=["紫", "金", "紫"], mates=[False, True, False])
        task.ocr_queue = [priced, priced, priced, priced]
        self.assertTrue(task.handle_street(priced))
        self.assertIn("步行街", task._weekend.get("visited") or set())
        self.assertTrue(task._weekend.get("gacha_spent"))

    def test_stamina_recover_parse(self):
        """2026-10-27 用户截图：精力恢复++ 的两种写法要能算出总量。"""
        self.assertEqual(FakeTask.stamina_recover(
            "立刻恢复90精力，之后每天恢复30点，持续4天，队友属性大幅提升"), 210)
        self.assertEqual(FakeTask.stamina_recover("立刻恢复40精力"), 40)
        self.assertEqual(FakeTask.stamina_recover("每天恢复10点，持续4天"), 40)
        # 2026-11-03 实拍：「增强款」那句额外恢复是**叠加**在正文恢复量上的
        # （「增强 立刻额外恢复30精力」+「立刻恢复60精力，之后每天20×4天」= 170）。
        # 旧版只取第一处 → 110，恢复价值被低估。
        self.assertEqual(FakeTask.stamina_recover(
            "增强立刻额外恢复30精力立刻恢复60精力，之后每天恢复20点，持续4天"), 170)
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
        """精力满时恢复卡不加分：别为了回不上的精力白花钱。"""
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
        """精力 72 时，金卡但清空精力 → 拒绝，退出。"""
        task = FakeTask(gacha_texts([
            ("精力转换", "消耗所有精力，意识训练"),
            ("防守增加", "主力防守+30"),
            ("投射增加", "主力投射+30"),
        ], stamina="72/121"), qualities=["金", "蓝", "蓝"])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions, [("click", 1837, 50)])

    def test_shop_price_with_circled_digit_does_not_crash(self):
        """2026-11-05 实跑（用户：「怎么停住了」）：商店第二行价格「10」被 OCR
        读成「①0」，`'①0'.isdigit()` 是 True 但 `int('①0')` 抛 ValueError，
        `read_shop_items` 每拍异常（被 flow 吞掉）→ 商店页永远推不动，任务原地停。

        修法：价格数字过 `labels.norm_digits`（带圈数字洗成普通数字）。
        测试走 `shop_items`（纯解析），判「①0 → 10」这一条就够；
        handler 级的推进由 expected.json 的真实帧回归兜着。
        """
        texts = [box(310, 220, "防守秘籍", 130, 35),
                 box(310, 600, "剩余：1", 80, 30),
                 box(305, 629, "①0", 70, 40)]          # ← 就是这个 ①0
        task = FakeTask(texts)
        items = task.shop_items(texts)
        self.assertEqual([(i["name"], i["price"]) for i in items],
                         [("防守秘籍", 10)])            # ①0 → 10，不再炸
    def test_blue_clear_card_never_bought_at_low_stamina(self):
        """2026-11-04 23:56 实跑：精力 23（低于清空阈值 30）时买了**蓝卡**，精力清到 0。

        日志原文：`体能增加(蓝/50币/0)、精力转换-体能(蓝+队友·清空/50币/0)、
        全属性增加(蓝/50币/0) → 选「精力转换-体能」（蓝，50币，零用钱 231，精力 23）`
        用户当场问「怎么买了一个蓝色的，不是不买吗」。

        根因：品质白名单写成了 `elif`，只挂在**非清空卡**上 → 清空卡绕过白名单。
        现在「蓝/其他一律不买」对清空卡同样成立：一张都不点，直接退出。
        """
        task = FakeTask(gacha_texts([
            ("体能增加", "主力体能+20"),
            ("精力转换-体能", "消耗所有精力，主力体能+48"),
            ("全属性增加", "主力三项推荐属性+20"),
        ], stamina="23/130", money="231", price="50"),
            qualities=["蓝", "蓝", "蓝"], mates=[False, True, False])
        self.assertTrue(task.handle_street(task._texts))
        self.assertEqual(task.actions, [("click", 1837, 50)])   # 只有右上角 ↩ 退出

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
        """精力 10 < 阈值 30：允许买清空卡（拿精力换属性）。

        2026-11-04 补：清空卡**也要过品质白名单**（当天买了蓝卡清空精力，
        用户：「怎么买了一个蓝色的，不是不买吗」），所以这里用紫卡当夹具，
        蓝清空卡的拒绝交给 `test_blue_clear_card_never_bought_at_low_stamina`。
        """
        task = FakeTask(gacha_texts([
            ("精力转换", "消耗所有精力，意识训练"),
            ("防守增加", "主力防守+30"),
            ("投射增加", "主力投射+30"),
        ], stamina="10/121", money="200"), qualities=["紫", "蓝", "蓝"])
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
        self.assertTrue(tick_boost(task, texts))

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
        self.assertTrue(tick_boost(task, texts))
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


class CritBorderRules(unittest.TestCase):
    """彩框不该在噪声行上点亮（2026-10-31 用户：「只有一个彩框，怎么出现了两个」）。

    实拍帧 logs/debug/1031-195131_subject04.jpg 里只有「投射」带彩虹边，日志却给
    「弹跳」也记了彩虹 1（占比 0.214，旧半窗 ±0.06h + 旧门限 0.20）。
    在存盘帧上重算：半窗 0.050h 后真彩框 0.27~0.39、噪声行 0.00~0.11。
    """

    SUBJECTS = [("投射", 210), ("弹跳", 340), ("防守", 470),
                ("体能", 600), ("运控", 730), ("意识", 860)]

    def _frame(self, rainbow_rows=(), noise_rows=()):
        """1549x871 的科目页：rainbow_rows 画满彩边，noise_rows 画一点杂色。"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        h, w = frame.shape[:2]
        half = int(h * 0.050)
        x1, x2 = int(w * 0.74), int(w * 0.98)
        magenta = (255, 0, 255)                    # BGR
        for name, cy in self.SUBJECTS:
            if name in rainbow_rows:
                # 上下两条边各画 6px（真彩框）
                frame[cy - half + 2:cy - half + 8, x1 + 30:x2 - 30] = magenta
                frame[cy + half - 8:cy + half - 2, x1 + 30:x2 - 30] = magenta
            elif name in noise_rows:
                # 只盖住一小块（≈ 0.09 占比），模拟选中行/图标蹭到的那点杂色
                frame[cy - half + 2:cy - half + 5, x1 + 30:x1 + 180] = magenta
        return frame

    def _texts(self):
        return [box(1500, cy - 24, name, 110, 48) for name, cy in self.SUBJECTS]

    def test_only_real_rainbow_row(self):
        frame = self._frame(rainbow_rows=("投射",), noise_rows=("弹跳",))
        texts = self._texts()
        task = DryRunTask(frame, texts)
        self.assertTrue(task.has_crit_border(texts, subject="投射"))
        self.assertFalse(task.has_crit_border(texts, subject="弹跳"))
        self.assertFalse(task.has_crit_border(texts, subject="体能"))

    def test_weak_rainbow_still_counts(self):
        """0.157 这种「弱彩边」也要认（2026-10-31 20:31 周四实机：满经验 1、真彩边）。"""
        self.assertTrue(ring_is_border(0.157, 0.0,
                                       floor=CRIT_RING_MIN,
                                       factor=CRIT_RING_FACTOR))

    def test_noise_band_is_below_floor(self):
        """噪声带上限 0.109 / 选中行旧口径 0.213 那一类，不能被当成彩框。"""
        self.assertFalse(ring_is_border(0.109, 0.0,
                                        floor=CRIT_RING_MIN,
                                        factor=CRIT_RING_FACTOR))

    def test_all_rows_colorful_is_not_all_rainbow(self):
        """整页都带杂色时，光有绝对门限会把 6 行全判成彩框 —— 相对判据要挡住。"""
        frame = self._frame(rainbow_rows=[n for n, _ in self.SUBJECTS])
        texts = self._texts()
        task = DryRunTask(frame, texts)
        self.assertFalse(task.has_crit_border(texts, subject="投射"))

    def test_missing_row_is_not_crit(self):
        frame = self._frame(rainbow_rows=("投射",))
        texts = self._texts()
        task = DryRunTask(frame, texts)
        self.assertFalse(task.has_crit_border(texts, subject="防守流"))


class RainbowGachaBand(unittest.TestCase):
    """彩色扭蛋卡（2026-11-01 训练等级提升后解锁）：判据见 RAINBOW_BAND_*。

    实拍帧 logs/now.jpg（1549x871）重算：中间那张彩色卡的卡头横带色相摊在
    75~165，30° 一档有 3 档 ≥0.14（最大档 0.51）；同页两张金卡最大档 0.96~1.00。
    """

    def _frame(self, hues):
        """1920x1080 空帧，在中间那张卡的卡头横带（y 0.37~0.42、x 0.45~0.55）画竖条。

        hues = [(h, 占比), ...]，色相按下标均分宽度。
        """
        hsv = np.zeros((1080, 1920, 3), dtype=np.uint8)
        h, w = hsv.shape[:2]
        y1, y2 = int(h * 0.37), int(h * 0.42)
        x1, x2 = int(w * 0.45), int(w * 0.55)
        step = (x2 - x1) // len(hues)
        for i, hue in enumerate(hues):
            hsv[y1:y2, x1 + i * step:x1 + (i + 1) * step] = (hue, 255, 255)
        return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    def test_rainbow_band_is_color(self):
        task = DryRunTask(self._frame([30, 90, 150]), [])
        self.assertEqual(task.card_band_quality(0.5)[0], "彩")

    def test_gold_band_is_gold(self):
        task = DryRunTask(self._frame([22]), [])
        self.assertEqual(task.card_band_quality(0.5)[0], "金")

    def test_purple_band_is_purple(self):
        task = DryRunTask(self._frame([130]), [])
        self.assertEqual(task.card_band_quality(0.5)[0], "紫")

    def test_two_colors_is_not_color(self):
        """只有两档（金卡边缘蹭到背景那种）不能算彩色 —— 要 ≥3 档。"""
        task = DryRunTask(self._frame([22, 130]), [])
        self.assertNotEqual(task.card_band_quality(0.5)[0], "彩")

    def test_arcade_card_band_skips_rainbow(self):
        """游戏厅卡面不判彩色（`card_quality_at` 默认 rainbow=False），
        免得卡面高光/渐变被误判成彩色。"""
        task = DryRunTask(self._frame([30, 90, 150]), [])
        self.assertNotEqual(task.card_quality_at(0.5, 0.37, 0.42)[0], "彩")


class IconNoiseFilter(unittest.TestCase):
    """顶栏图标被 OCR 读成的假汉字要丢掉（用户 2026-11-01）：

    「那不是字，是**历史记录**和**菜单**两个图标」。OCR 硬读成 `早三/昆三/数目三/…`，
    置信度 0.5~0.8，累计上万次，还会污染 `match_text` 的整段拼接判据。
    """

    def _task(self, boxes):
        return DryRunTask(np.zeros((1080, 1920, 3), dtype=np.uint8), boxes)

    def _icon(self, name, conf=0.6):
        # 右上角图标带里（cx≈0.82w, cy≈0.04h）
        return Box(x=1550, y=20, width=120, height=50, name=name, confidence=conf)

    def test_icon_garbage_is_dropped(self):
        names = ("早三", "昆三", "数目三", "战昆三色")
        for n in names:
            task = self._task([self._icon(n)])
            self.assertEqual(task.drop_icon_noise(task._texts), [], n)

    def test_real_text_with_san_is_kept(self):
        """三分威 / 三张卡 / 事件三选一 / 三倍经 / 降序三 是真文本，不能杀。"""
        for n in ("三分威", "三张卡", "事件三选一", "三倍经", "降序三"):
            task = self._task([box(700, 500, n, 200, 60)])
            self.assertEqual([t.name for t in task.drop_icon_noise(task._texts)], [n], n)

    def test_same_string_off_band_is_kept(self):
        """`三色` 这种串在屏幕中间（比如商品名）不能因为长得像就丢。"""
        task = self._task([box(700, 500, "三色", 200, 60)])
        self.assertEqual(len(task.drop_icon_noise(task._texts)), 1)


class StaminaBarFallback(unittest.TestCase):
    """专注条像素兜底：OCR 把 `/` 读成 `1`（571130 / 301130 / 333115）。

    药丸总宽是固定版式 0.2885w（实测 554/1920、447/1549、377/1307），
    填充比例反推专注与 OCR 正读值差 ≤1，所以斜杠读错时不再无解。
    """

    W, H = 1920, 1080
    LEFT, TOTAL = 386, 554          # 1920 宽实拍帧量出来的药丸位置/总宽

    def _frame(self, cur=57, mx=130, **kw):
        frame = np.full((self.H, self.W, 3), (250, 247, 240), dtype=np.uint8)
        left, total = kw.get("left", self.LEFT), kw.get("total", self.TOTAL)
        right = left + total
        fill_end = left + int(round(total * cur / float(mx)))
        y1, y2 = 10, 70
        frame[y1:y2, left:right] = (184, 195, 199)          # 灰色空段
        frame[y1:y2, left:fill_end] = (0, 238, 255)         # 黄色填充（H≈27）
        return frame

    def _pill_box(self, name, y=10):
        return box(self.LEFT, y, name, self.TOTAL, 40)

    def test_slash_read_as_one_is_recovered(self):
        """`571130`（= 57/130）用填充比例还原成 57。"""
        task = DryRunTask(self._frame(57, 130), [self._pill_box("571130")])
        state = task.top_bar(task._texts)
        self.assertEqual(state["stamina"], 57)
        self.assertEqual(state["stamina_max"], 130)
        self.assertTrue(any("专注条" in s for s in task.logs))

    def test_clean_ocr_is_kept(self):
        """OCR 读对了（57/130）就用 OCR 的值，不扰动。"""
        task = DryRunTask(self._frame(57, 130), [self._pill_box("57/130")])
        state = task.top_bar(task._texts)
        self.assertEqual((state["stamina"], state["stamina_max"]), (57, 130))
        self.assertEqual(task.logs, [])

    def test_wrong_digit_is_corrected(self):
        """OCR 某一位读错（87/115，实际 57/115）→ 以像素为准（±1，见 STAMINA_BAR 校准）。"""
        task = DryRunTask(self._frame(57, 115), [self._pill_box("87/115")])
        state = task.top_bar(task._texts)
        self.assertLessEqual(abs(state["stamina"] - 57), 1)
        self.assertTrue(any("按像素值算" in s for s in task.logs))

    def test_no_bar_means_no_guess(self):
        """没有专注条的帧（黑屏/其它页面）不许瞎猜。"""
        frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        task = DryRunTask(frame, [self._pill_box("571130")])
        self.assertIsNone(task.top_bar(task._texts)["stamina"])

    def test_bar_at_odd_position_is_rejected(self):
        """药丸左沿不在 0.2010w（小游戏页那种 7/10 的条）→ 不采信像素值。"""
        task = DryRunTask(self._frame(57, 130, left=700),
                          [self._pill_box("571130", )])
        task._texts = [box(700, 10, "571130", self.TOTAL, 40)]
        self.assertIsNone(task.top_bar(task._texts)["stamina"])

    def test_blob_max_reused_when_digits_unreadable(self):
        """斜杠串整个没读到时，沿用本局读过的上限再按比例还原（±1）。"""
        task = DryRunTask(self._frame(57, 130), [])
        task._stamina_max_seen = 130
        state = task.top_bar([])
        self.assertEqual(state["stamina_max"], 130)
        self.assertLessEqual(abs(state["stamina"] - 57), 1)

    def test_same_frame_resolves_once(self):
        """同一帧里 top_bar 被调两次，结论必须一致（别 57/56 来回跳）。"""
        task = DryRunTask(self._frame(57, 130), [])
        task._stamina_max_seen = 130
        first = task.top_bar([])["stamina"]
        second = task.top_bar([])["stamina"]
        self.assertEqual(first, second)

    def test_candidates_disambiguate_which_digit_to_drop(self):
        """粘连串拆出来的候选里既有 71 也有 57 → 由专注条填充（约 44%）仲裁成 57。"""
        task = DryRunTask(self._frame(57, 130), [self._pill_box("571130")])
        mx, cands = task.stamina_candidates(task._texts, self.LEFT,
                                           self.LEFT + self.TOTAL)
        self.assertEqual(mx, 130)
        self.assertIn(57, cands)
        self.assertIn(71, cands)
        self.assertEqual(task.top_bar(task._texts)["stamina"], 57)

    def test_week_digit_glued_head(self):
        """周数也粘在头上（`333115` = 33/115）：候选仍在，仲裁取 33。"""
        task = DryRunTask(self._frame(33, 115), [self._pill_box("333115")])
        self.assertEqual(task.top_bar(task._texts)["stamina"], 33)


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

    def test_stamina_does_not_buy_attribute_points(self):
        """2026-11-03：混合选项里的精力不能再折成属性分。

        旧实现「属性+3，精力+30」= 3 + 6 = 9 分，会压过「属性+8」= 8 分；
        精力是好事，但它和属性不是同一把尺子 —— 差的那 5 点属性
        不可能靠回 30 精力补回来（用户口径：回精力不如一点属性）。
        精力现在只在属性打平时当次级比较（且要按余量封顶）。
        """
        texts = [
            box(60, 40, "第2周", 90, 34), box(200, 40, "星期四", 90, 34),
            box(380, 16, "54/130", 210, 53),
            box(240, 700, "普通训练", 170, 45),
            box(900, 700, "特训", 170, 45),
            box(240, 760, "属性+3，精力+30", 260, 37),
            box(900, 760, "属性+8", 130, 33),
        ]
        task = FakeTask(texts)
        self.assertTrue(task.handle_event_choice(texts))
        self.assertEqual(task.actions[-1][1], "特训")

    def test_usable_stamina_capped_by_room(self):
        """精力按「这次真能用上」封顶：满精力时回 30 点 = 0 分。"""
        effs = [box(0, 0, "精力+30", 200, 40)]
        task = FakeTask([])
        self.assertEqual(task.usable_stamina(effs, 0), 0)       # 满精力
        self.assertEqual(task.usable_stamina(effs, 12), 12)     # 只剩 12 点余量
        self.assertEqual(task.usable_stamina(effs, None), 30)   # 读不到顶栏 → 原样


class ResumeTrainingNeverWipes(unittest.TestCase):
    """「训练确认」弹窗只许点「继续训练」，永不删档（2026-11-02 用户口径）。

    开训练消耗的专注在删档时**不返还** → 删档是纯亏损操作。
    旧开关「新一局删档」已从 config_spec 删除；即使配置里残留 True，
    handler 也不许再出现任何点「删档」的动作。
    """

    @staticmethod
    def confirm_texts():
        return [box(760, 300, "训练确认", 400, 70),
                box(500, 780, "删档", 200, 70),          # 青色（左）
                box(1300, 780, "继续训练", 280, 70)]     # 黄色（右）

    def test_never_clicks_wipe_even_if_config_lingers(self):
        task = FakeTask(self.confirm_texts(), config={"新一局删档": True})
        self.assertTrue(task.handle_resume_training(task._texts))
        clicked = [a[1] for a in task.actions if a[0] == "click_box"]
        self.assertEqual(clicked, ["继续训练"])
        self.assertNotIn("删档", clicked)

    def test_without_continue_text_clicks_fixed_yellow_spot(self):
        """只 OCR 到「删档」时，宁可点固定坐标（黄按钮）也不点删档。"""
        texts = [box(760, 300, "训练确认", 400, 70),
                 box(500, 780, "删档", 200, 70)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_resume_training(texts))
        self.assertEqual(len(task.actions), 1)
        kind, x, y = task.actions[0]
        self.assertEqual(kind, "click")
        self.assertGreater(x, task.width * 0.5)      # 黄按钮在右半屏
        self.assertNotIn("删档", [a[1] for a in task.actions])

    def test_config_key_is_gone(self):
        from src.tasks.training import config_spec
        keys = {s.key for s in config_spec.ALL}
        self.assertNotIn("新一局删档", keys)


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

    def test_blue_card_is_never_bought(self):
        """蓝色品质不买（用户 2026-11-06 口径：彩 > 金 > 紫，**没有蓝**）。

        2026-11-03 的旧口径是「蓝卡最后兜底」；11-05 实跑 60 块买了蓝卡
        （金 100/紫 80 都买不起 → 轮到蓝 60 成交）。用户明确：所有判断环节
        都没有蓝这一档 —— 只剩蓝卡 → 退出游戏厅，不花钱。
        """
        texts = [
            box(600, 115, "请选择一个小游戏", 220, 36),
            box(199, 236, "自动", 64, 30),
            box(308, 238, "弹跳", 70, 42),
            box(251, 370, "弹跳20~70", 155, 31),
            box(355, 484, "64", 44, 32),
            box(615, 646, "确定", 71, 39),
        ]
        task = self._task(texts, quality="蓝")
        self.assertTrue(task.handle_arcade(texts))
        # 停手原因里写明「没有彩/金/紫」；一张卡都没点，也没点「确定」
        self.assertIn("彩/金/紫", " ".join(task.logs))
        bought = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual(bought, [])

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

    def test_stamina_is_not_attribute_value(self):
        """2026-11-03：精力**不进属性分**（旧口径 ×0.2）。

        旧实现「属性+3，精力+30」= 3 + 6 = 9 分，会压过「属性+8」= 8 分 ——
        满精力时也一样，等于「回精力不如一点属性」这条口径没生效。
        现在 ev 只算六维属性，精力由 `stamina_gain` 单独算，
        只在属性打平、且精力还没满时当次级比较。
        """
        self.assertIsNone(self.ev("精力+10"))
        self.assertAlmostEqual(self.ev("精力+10，投射+5"), 5.0)
        self.assertEqual(
            FakeTask.stamina_gain([box(400, 300, "精力+10", 300, 40)]), 10)
        self.assertEqual(
            FakeTask.stamina_gain([box(400, 300, "精力提升+30", 300, 40)]), 30)
        # 「消耗/清空精力换属性」是开销，不是收益
        self.assertEqual(
            FakeTask.stamina_gain([box(400, 300, "消耗精力，意识+30", 300, 40)]), 0)
        # 「精力上限+10」是永久上限，不是这次能用的精力
        self.assertEqual(
            FakeTask.stamina_gain([box(400, 300, "精力上限+10", 300, 40)]), 0)

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
        """0/121 必须解析成 0，不能是 None（否则公园会误判精力充足）。"""
        task = FakeTask([box(380, 16, "0/121", 210, 53)])
        self.assertEqual(task.top_bar()["stamina"], 0)

    def test_glued_week_prefix(self):
        task = FakeTask([box(380, 16, "369/115", 210, 53)])
        self.assertEqual(task.top_bar()["stamina"], 69)


class TopBarDayDropout(unittest.TestCase):
    """星期几被 OCR **丢字**时的兜底（2026-11-02 实测 1136 帧定案）。

    实测结论：整帧 OCR **从来没读错**星期几，但会丢字 ——
    「一」只有一条横线，检测框把它并进「期」里，整词变成「星期」两字。
    丢字率 9.9%，其中「星期一」丢 67.7%、「星期六」被读成「期大」。

    星期几是**当天状态**的日期键（`daycycle.day_state` 用 (周, 星期)），
    读不到 → 整天状态都不对 → 周末去处、休息阈值全走错。
    所以 `top_bar` 读不出时必须用「裁那块放大重读」兜底。
    """

    def _task(self, boxes, frame=None):
        img = frame if frame is not None else np.zeros((1080, 1920, 3), dtype=np.uint8)
        return DryRunTask(img, boxes)

    def test_reads_day_when_present(self):
        task = self._task([box(160, 14, "星期二", 90, 42)])
        self.assertEqual(task.top_bar()["day"], "二")

    def test_dropped_char_triggers_region_reread(self):
        """框里只剩「星期」（第三字丢了）→ 必须走区域重读，而不是直接 None。"""
        calls = []

        def fake_read_day(ocr, frame):
            calls.append(frame)
            return "一"

        old = topbar_day.read_day
        topbar_day.read_day = fake_read_day
        try:
            task = self._task([box(160, 14, "星期", 70, 40)])
            task._tick_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            self.assertEqual(task.top_bar()["day"], "一")
            self.assertEqual(len(calls), 1)
        finally:
            topbar_day.read_day = old

    def test_region_reread_is_cached_per_frame(self):
        """`top_bar()` 一拍里被调用 20 多次，区域重读必须按帧缓存。"""
        calls = []

        def fake_read_day(ocr, frame):
            calls.append(frame)
            return "一"

        old = topbar_day.read_day
        topbar_day.read_day = fake_read_day
        try:
            task = self._task([box(160, 14, "星期", 70, 40)])
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            task._tick_frame = frame
            for _ in range(5):
                task.top_bar()
            self.assertEqual(len(calls), 1)
        finally:
            topbar_day.read_day = old

    def test_no_reread_when_day_already_read(self):
        """整帧读得出时**不**做区域重读（省一次 OCR）。"""
        calls = []

        def fake_read_day(ocr, frame):
            calls.append(frame)
            return "九"

        old = topbar_day.read_day
        topbar_day.read_day = fake_read_day
        try:
            task = self._task([box(160, 14, "星期三", 90, 42)])
            task._tick_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            self.assertEqual(task.top_bar()["day"], "三")
            self.assertEqual(calls, [])
        finally:
            topbar_day.read_day = old

    def test_day_from_texts_requires_whole_word(self):
        """只认整词「星期X」：正文里的「星期日要训练」不算，「比赛后期」更不算。"""
        self.assertEqual(topbar_day.day_from_texts(["星期三"]), "三")
        self.assertEqual(topbar_day.day_from_texts(["星期天"]), "日")
        self.assertIsNone(topbar_day.day_from_texts(["星期日要训练"]))
        self.assertIsNone(topbar_day.day_from_texts(["比赛后期提升自己两项推荐"]))
        self.assertIsNone(topbar_day.day_from_texts(["星期"]))
        self.assertIsNone(topbar_day.day_from_texts([]))

    def test_week_from_texts_requires_whole_word(self):
        self.assertEqual(topbar_day.week_from_texts(["第4周"]), 4)
        self.assertIsNone(topbar_day.week_from_texts(["第4周星期"]))
        self.assertIsNone(topbar_day.week_from_texts(["4周"]))


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

    def test_refresh_merges_and_keeps_unread(self):
        """refresh_attr_info 是**合并**：这一帧没读到的属性保留旧值，不掉成「—」。

        场景（2026-11-02 实机）：停在训练日主界面时，鼠标停在某一格上会弹
        「提高看破对手意图…」，正好盖住体能那一行 —— 整块替换会把体能写成空。
        """
        texts = [box(214, 830, "投射", 100, 57), box(384, 834, "501/1300", 186, 55)]
        task = FakeTask(texts)
        task.info["attrs"] = {"投射": 400, "弹跳": 300, "体能": 200}
        merged = task.refresh_attr_info(texts)
        self.assertEqual(merged, {"投射": 501, "弹跳": 300, "体能": 200})
        self.assertEqual(task.info["attrs"], merged)


class LoungeSnapshot(unittest.TestCase):
    """进休息室（训练日主界面）时的一次性快照：六维 + 精力 + 零用钱。

    2026-11-03 用户口径：**只在这一页更新**。校门口/地图没有六维面板；
    周末加的是零碎属性（属性+10 / 精力+…），回头累加既对不上面板也没意义。
    """

    TOPBAR = [box(46, 22, "第1周", 108, 42), box(202, 22, "星期三", 130, 44),
              box(384, 20, "115/130", 232, 48), box(1066, 28, "80", 44, 32)]
    PANEL = [box(214, 830, "投射", 100, 57), box(384, 834, "501/1300", 186, 55),
             box(222, 909, "弹跳", 92, 54), box(388, 917, "999/1450", 86, 46)]
    HUB = [box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]

    def _task(self):
        task = FakeTask(self.TOPBAR + self.PANEL + self.HUB)
        task.ensure_speed = lambda *a, **k: False
        return task

    def test_coin_from_top_bar(self):
        """零用钱 = 金币图标右边那个纯数字（cx≈0.567），别把「-100」当成钱。"""
        texts = list(self.TOPBAR)
        task = FakeTask(texts)
        self.assertEqual(task.top_bar(texts)["coin"], 80)
        # 周末购物飘出来的「-100」在 cx=0.649，不在竖带里
        noisy = texts + [box(1226, 32, "-100", 98, 32)]
        self.assertEqual(FakeTask(noisy).top_bar(noisy)["coin"], 80)

    def test_snapshot_written_once_per_entry(self):
        """决策拍拍一次快照，六维 / 精力 / 零用钱一起进 info；同一页不重复拍。"""
        texts = self.TOPBAR + self.PANEL + self.HUB
        task = self._task()
        task.handle_training_day(texts)          # 头一拍只观察（HUB_SETTLE_TICKS）
        self.assertNotIn("attrs", task.info)
        task.handle_training_day(texts)          # 决策拍：先快照，再点训练
        self.assertEqual(task.info["attrs"], {"投射": 501, "弹跳": 999})
        self.assertEqual(task.info["coin"], 80)
        self.assertEqual(task.info["stamina"], 115)
        self.assertEqual(task.actions[-1][1], "训练")
        self.assertTrue(task._lounge_snapshot_done)
        # 同一个页面（点击没生效、又停一拍）不重复拍
        before = dict(task.info["attrs"])
        task.handle_training_day(texts)
        self.assertEqual(task.info["attrs"], before)


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


class DayCycleRules(unittest.TestCase):
    """全局日期 + 两个新一天入口（用户 2026-11-01 的架构）。

    架构要点：
      - **一天只有一个标识**：顶栏「第N周 星期X」。游戏里交互就过天，
        所以日期变了就是新的一天，不需要另外维护「今天过没过」。
      - **新一天有两个入口，同级**：训练日主界面、周末出行首页。
        它们的判断都在 `training/daycycle.py`，handler 只负责照做。
    """

    TOPBAR = [box(30, 20, "第1周", 120, 40), box(200, 20, "星期五", 130, 40),
              box(380, 16, "120/130", 210, 53)]

    def test_current_day_reads_topbar(self):
        task = FakeTask(list(self.TOPBAR))
        self.assertEqual(task.current_day(), (1, "五"))
        self.assertEqual(task.day_name(), "五")
        self.assertFalse(task.is_weekend())

    def test_current_day_cached_per_frame(self):
        """同一帧只读一次顶栏（各模块各读一次会读到不同的值）。"""
        task = FakeTask(list(self.TOPBAR))
        task._tick_frame = task.frame
        task.current_day()
        # 换掉 OCR 结果，同一帧不该重新读
        task._texts = [box(30, 20, "第9周", 120, 40), box(200, 20, "星期日", 130, 40)]
        self.assertEqual(task.current_day(), (1, "五"))
        # 换帧 → 重读
        task._tick_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertEqual(task.current_day(), (9, "日"))

    def test_day_state_survives_topbar_dropout(self):
        """顶栏读不到周/星期 → 沿用旧状态，**不清空**当天行程。

        2026-11-01 的真实 bug：原来 key 一变就整个重建 `_weekend`，
        顶栏抖一帧 → visited/gacha_spent 全丢 → 当天「已经买过扭蛋」的事实没了。
        """
        task = FakeTask(list(self.TOPBAR))
        st = task.day_state()
        st["visited"].add("步行街")
        st["gacha_spent"] = True
        # 顶栏坏掉（只剩专注）
        task._texts = [box(380, 16, "120/130", 210, 53)]
        task._tick_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        st2 = task.day_state()
        self.assertIn("步行街", st2["visited"])
        self.assertTrue(st2["gacha_spent"])

    def test_day_state_resets_on_new_day(self):
        """日期变了 → 新的一天，状态重置。"""
        task = FakeTask(list(self.TOPBAR))
        task.day_state()["visited"].add("步行街")
        # 换到星期六
        task._texts = [box(30, 20, "第1周", 120, 40), box(200, 20, "星期六", 130, 40),
                       box(380, 16, "120/130", 210, 53)]
        task._tick_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        st = task.day_state()
        self.assertNotIn("步行街", st["visited"])
        self.assertEqual(st["date"], (1, "六"))

    def test_training_day_action_rest_when_low_stamina(self):
        """训练日入口①：专注低于阈值 → 休息。"""
        task = FakeTask([box(30, 20, "第1周", 120, 40), box(200, 20, "星期五", 130, 40),
                         box(380, 16, "20/130", 210, 53)])
        action, reason = task.training_day_action(task._texts)
        self.assertEqual(action, "休息")
        self.assertIn("20", reason)

    def test_training_day_action_trains_when_enough(self):
        """训练日入口①：专注够 → 训练。"""
        task = FakeTask([box(30, 20, "第1周", 120, 40), box(200, 20, "星期五", 130, 40),
                         box(380, 16, "120/130", 210, 53)])
        action, _ = task.training_day_action(task._texts)
        self.assertEqual(action, "训练")

    def test_outing_offer_requires_button_not_dialogue(self):
        """出行入口②：只有**按钮**才算，台词不算。"""
        texts = [box(30, 20, "第1周", 120, 40), box(200, 20, "星期五", 130, 40),
                 box(380, 16, "120/130", 210, 53),
                 box(945, 168, "今天可以出行哦！要不", 330, 40)]
        task = FakeTask(texts)
        action, reason = task.outing_offer(texts)
        self.assertEqual(action, "推进对话")
        self.assertIn("对话", reason)

    def test_outing_offer_blocked_after_used(self):
        """今天的额外出行用过了 → 不再点（block_if_used=True）。"""
        texts = [box(30, 20, "第1周", 120, 40), box(200, 20, "星期五", 130, 40),
                 box(380, 16, "120/130", 210, 53),
                 box(1150, 700, "出行", 160, 70)]
        task = FakeTask(texts)
        task.mark_outing_used()
        action, reason = task.outing_offer(texts)
        self.assertIsNone(action)
        self.assertIn("用过", reason)
        # 周末首页不受限制（周末出行是主流程）
        action2, _ = task.outing_offer(texts, block_if_used=False)
        self.assertEqual(action2, "点按钮")

    def test_two_entry_points_are_siblings(self):
        """两个入口同级：训练日和出行首页的判断都在同一个模块里。"""
        from src.tasks.training import daycycle
        self.assertTrue(hasattr(daycycle.DayCycleMixin, "training_day_action"))
        self.assertTrue(hasattr(daycycle.DayCycleMixin, "outing_offer"))
        # handler 只做「照它说的做」
        self.assertTrue(hasattr(FakeTask, "training_day_action"))
        self.assertTrue(hasattr(FakeTask, "outing_offer"))


class ConfigSpecRules(unittest.TestCase):
    """配置项的**唯一声明**（training/config_spec.py）。

    2026-11-01 实测的三个问题，这里逐条锁住：
      ① 默认值写在两处（调用处的那个是死代码）；
      ② 有配置项用户在 GUI 里配不到（没在 spec 里声明）；
      ③ 有配置项声明了但没人读（GUI 上的无效项）。
    """

    def test_spec_declares_all_used_keys(self):
        """training/ 里读的每个 config 键都在 spec 里声明了。

        缺键时 `config.get(key)` 返回 **None**（falsy）→
        「允许进步行街」被当成 False —— 2026-11-01 去掉调用处默认值后，
        25 条测试就是这么挂的（FakeTask 的 config 只有 1 个键）。
        """
        import ast
        from src.tasks.training import config_spec
        declared = {s.key for s in config_spec.ALL}
        used = set()
        for path in glob.glob(r"src\tasks\training\**\*.py", recursive=True):
            if path.endswith("config_spec.py"):
                continue
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get"):
                    continue
                fn = node.func
                if not (isinstance(fn.value, ast.Attribute)
                        and fn.value.attr == "config"):
                    continue
                if not node.args:
                    continue
                kn = node.args[0]
                if isinstance(kn, ast.Constant) and isinstance(kn.value, str):
                    used.add(kn.value)
        missing = used - declared
        self.assertEqual(missing, set(),
                         "这些键在 src/ 里被读但没在 config_spec 里声明：%s" % missing)

    def test_no_default_value_at_call_site(self):
        """调用处不许再写默认值（默认值只许在 spec 里）。"""
        import ast
        offenders = []
        for path in glob.glob(r"src\tasks\training\**\*.py", recursive=True):
            if path.endswith("config_spec.py"):
                continue
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get"):
                    continue
                fn = node.func
                if not (isinstance(fn.value, ast.Attribute)
                        and fn.value.attr == "config"):
                    continue
                if len(node.args) >= 2:
                    offenders.append("%s:%d" % (path, node.lineno))
        self.assertEqual(offenders, [],
                         "调用处还写着默认值（死代码）：%s" % offenders)

    def test_dead_config_removed(self):
        """失效的「扭蛋优先属性」已从 spec 里删掉。

        10-26 选卡策略重写（金紫优先/队友优先）之后就没有读取点了，
        但声明和 GUI 说明一直留着 —— 而且说明写的是老口径
        「会消耗所有精力」，与现行规则（精力 < 30 才买清空卡）**相反**。
        """
        from src.tasks.training import config_spec
        keys = {s.key for s in config_spec.ALL}
        self.assertNotIn("扭蛋优先属性", keys)

    def test_gacha_clear_threshold_is_configurable(self):
        """「扭蛋清空精力阈值」在 spec 里（以前用户配不到）。"""
        from src.tasks.training import config_spec
        spec = {s.key: s for s in config_spec.ALL}
        self.assertIn("扭蛋清空精力阈值", spec)
        self.assertEqual(spec["扭蛋清空精力阈值"].default, 30)

    def test_fixture_uses_real_defaults(self):
        """测试夹具必须用**真实的 default_config**（和生产一样）。

        夹具只有 1 个键 → 缺键拿到 None → 一批测试挂掉。
        """
        from src.tasks.training import config_spec
        task = FakeTask([])
        self.assertEqual(task.config["允许进步行街"], True)
        self.assertEqual(task.config["休息精力阈值"], 40)
        self.assertEqual(task.config["扭蛋清空精力阈值"], 30)
        self.assertEqual(len(task.config), len(config_spec.ALL))

    def test_json_and_spec_agree(self):
        """configs/TrainingTask.json 的键集合与 spec 一致。

        不一致的话，`ok.util.config.Config.verify_config` 会把 json 里
        多出来的键**删掉**（用户改过的值就丢了）。
        """
        import json
        from src.tasks.training import config_spec
        spec = {s.key for s in config_spec.ALL}
        cfg = json.load(open(r"configs\TrainingTask.json", encoding="utf-8"))
        extra = set(cfg) - spec
        self.assertEqual(extra, set(),
                         "json 里多出来的键会被 verify_config 删掉：%s" % extra)
        self.assertIn("扭蛋清空精力阈值", cfg,
                      "json 里要有它（GUI 才能配上）")

    def test_legacy_stamina_keys_are_migrated(self):
        """2026-11-02 规范用词：配置键里不许再有「体力」/「专注」，旧值要能搬过来。

        顶栏那个数字在训练里叫 **精力**（打工/公园/扭蛋清空消耗它）。
        旧配置存过「体力」和「专注」，改名后 `Config.verify_config` 会把旧键
        连同值一起丢掉，所以 `migrate_saved_config()` 必须在 `load_config()` 之前搬。
        """
        import json
        import os
        import tempfile
        from src.tasks.training import config_spec

        keys = {s.key for s in config_spec.ALL}
        self.assertEqual([k for k in keys if "体力" in k or "专注" in k], [],
                         "配置键里不许再出现「体力」/「专注」")

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "TrainingTask.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"休息体力阈值": 35, "扭蛋清空体力阈值": 12,
                           "允许进步行街": False}, f, ensure_ascii=False)
            self.assertTrue(config_spec.migrate_saved_config("TrainingTask", folder))
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            # 再跑一次不该有任何改动（幂等）
            self.assertFalse(config_spec.migrate_saved_config("TrainingTask", folder))

            # 新键已经有值（用户配过）→ 保留新值，旧键只负责清掉
            path2 = os.path.join(folder, "Other.json")
            with open(path2, "w", encoding="utf-8") as f:
                json.dump({"休息精力阈值": 55, "休息专注阈值": 45,
                           "休息体力阈值": 35}, f, ensure_ascii=False)
            self.assertTrue(config_spec.migrate_saved_config("Other", folder))
            with open(path2, encoding="utf-8") as f:
                other = json.load(f)

        self.assertEqual(saved["休息精力阈值"], 35)
        self.assertEqual(saved["扭蛋清空精力阈值"], 12)
        self.assertNotIn("休息体力阈值", saved)
        self.assertNotIn("扭蛋清空体力阈值", saved)
        self.assertEqual(saved["允许进步行街"], False)
        self.assertEqual(other, {"休息精力阈值": 55})


class PlaceModulesRules(unittest.TestCase):
    """地点模块：每个地点只管自己的判断（2026-11-01 架构第 3 步）。

    地点模块**只给判断，不点击**；互相之间不许知道对方
    （「今天该去哪」是地图模块的职责）。
    """

    def test_all_five_places_have_modules(self):
        """5 个地点 + 1 个决策模块（地图）都有独立模块。"""
        from src.tasks.training import places
        for name in ("StreetPlace", "RestaurantPlace", "ParkPlace",
                     "ArcadePlace", "ShopPlace", "MapPlace"):
            self.assertTrue(hasattr(places, name), name)

    def test_each_place_declares_its_own_name(self):
        """每个地点模块声明自己的地名（门禁靠它识别归属）。"""
        from src.tasks.training.places import (arcade, park, restaurant, shop,
                                              street)
        self.assertEqual(street.PLACE, "步行街")
        self.assertEqual(restaurant.PLACE, "餐厅")
        self.assertEqual(park.PLACE, "公园")
        self.assertEqual(arcade.PLACE, "游戏厅")
        self.assertEqual(shop.PLACE, "商店")

    def test_park_order_by_style_and_day(self):
        """公园：训练流周日看精力，其它流派用配置项。"""
        task = FakeTask([box(30, 20, "第1周", 120, 40), box(200, 20, "星期日", 130, 40),
                         box(380, 16, "20/130", 210, 53)])
        task._style = "训练流"
        task.weekend_state()
        order = task.park_order(task._texts, "训练流", day="日")
        self.assertEqual(order[0], "野餐")      # 精力 20 < 60 → 野餐补精力

        task2 = FakeTask([box(30, 20, "第1周", 120, 40), box(200, 20, "星期日", 130, 40),
                          box(380, 16, "120/130", 210, 53)])
        task2._style = "训练流"
        task2.weekend_state()
        order2 = task2.park_order(task2._texts, "训练流", day="日")
        self.assertEqual(order2[0], "划船")      # 精力够 → 划船（属性+）

        task3 = FakeTask([box(30, 20, "第1周", 120, 40), box(200, 20, "星期日", 130, 40)])
        task3._style = "扭蛋流"
        order3 = task3.park_order(task3._texts, "扭蛋流", day="日")
        self.assertEqual(order3[0], "野餐")      # 其它流派用配置项（默认野餐）

    def test_arcade_picks_free_card_first(self):
        """游戏厅：免费（0 元）的卡优先拿（钱读不到也能买）。"""
        from src.tasks.training.places.arcade import ArcadePlace
        cards = [
            {"attr": "投射", "hi": 100, "box": None, "auto": True, "price": 80,
             "quality": "金", "hue": 0, "sat": 0, "mate": False},
            {"attr": "弹跳", "hi": 70, "box": None, "auto": True, "price": 0,
             "quality": "紫", "hue": 0, "sat": 0, "mate": False},
        ]
        task = FakeTask([])
        pick, reason = task.pick_arcade_card(cards, money=None)
        self.assertEqual(pick["attr"], "弹跳")
        self.assertIn("免费", reason)

    def test_arcade_blue_is_never_bought(self):
        """游戏厅：品质优先 彩>金>紫（用户 2026-11-03）。

        2026-11-06 用户口径修订：**没有蓝** —— 蓝/白不是「最后兜底」，
        是根本不买。11-05 实跑 60 块买了蓝卡（金 100/紫 80 都买不起 →
        轮到蓝 60 成交），就是旧口径「蓝是最后兜底」的直接后果。
        现在只剩蓝卡 → 退出游戏厅，钱留着。
        """
        blue = {"attr": "投射", "hi": 100, "box": None, "auto": True, "price": 80,
                "quality": "蓝", "hue": 0, "sat": 0, "mate": False}
        gold = {"attr": "弹跳", "hi": 40, "box": None, "auto": True, "price": 80,
                "quality": "金", "hue": 0, "sat": 0, "mate": False}
        task = FakeTask([])
        pick, _ = task.pick_arcade_card([blue, gold], money=500)
        self.assertEqual(pick["attr"], "弹跳")             # 金卡奖励更低也先买金
        pick2, reason2 = task.pick_arcade_card([blue], money=500)
        self.assertIsNone(pick2)                           # 只剩蓝卡 → 不买
        self.assertIn("彩/金/紫", reason2)

    def test_arcade_quality_beats_two_teammates(self):
        """游戏厅：品质永远第一档（用户 2026-11-03）。

        紫卡挂 2 个队友、金卡没队友 → 买金卡。2026-10-29 的「有队友优先」
        是在**同品质**内部比的，不能把品质翻过去。
        """
        cards = [
            {"attr": "投射", "hi": 40, "box": None, "auto": True, "price": 80,
             "quality": "金", "hue": 0, "sat": 0, "mate": False},
            {"attr": "弹跳", "hi": 100, "box": None, "auto": True, "price": 80,
             "quality": "紫", "hue": 0, "sat": 0, "mate": True, "mates": 2},
        ]
        task = FakeTask([])
        pick, _ = task.pick_arcade_card(cards, money=500)
        self.assertEqual(pick["attr"], "投射")

    def test_arcade_more_teammates_wins(self):
        """游戏厅：同品质都带队友时，队友多的优先（用户 2026-11-03）。"""
        cards = [
            {"attr": "投射", "hi": 100, "box": None, "auto": True, "price": 80,
             "quality": "金", "hue": 0, "sat": 0, "mate": True, "mates": 1},
            {"attr": "弹跳", "hi": 70, "box": None, "auto": True, "price": 80,
             "quality": "金", "hue": 0, "sat": 0, "mate": True, "mates": 2},
        ]
        task = FakeTask([])
        pick, _ = task.pick_arcade_card(cards, money=500)
        self.assertEqual(pick["attr"], "弹跳")

    def test_arcade_prefers_teammate_card(self):
        """游戏厅：同品质时有队友的优先（2026-10-29 口径）。"""
        cards = [
            {"attr": "投射", "hi": 100, "box": None, "auto": True, "price": 80,
             "quality": "金", "hue": 0, "sat": 0, "mate": False},
            {"attr": "弹跳", "hi": 70, "box": None, "auto": True, "price": 60,
             "quality": "金", "hue": 0, "sat": 0, "mate": True},
        ]
        task = FakeTask([])
        pick, _ = task.pick_arcade_card(cards, money=500)
        self.assertEqual(pick["attr"], "弹跳")     # 队友优先（尽管奖励上限低）

    def test_shop_pick_by_policy(self):
        """商店：按「购买策略」选。"""
        items = [
            {"name": "器材手册", "price": 60, "quality": "紫", "x": 100, "y": 100},
            {"name": "训练手册", "price": 100, "quality": "金", "x": 100, "y": 200},
            {"name": "旧货", "price": 45, "quality": "蓝", "x": 100, "y": 300},
        ]
        task = FakeTask([])
        pick, _ = task.pick_shop_item(items, 500, "只买彩金紫")
        self.assertEqual(pick["quality"], "金")
        pick2, _ = task.pick_shop_item(items, 500, "只买器材手册+")
        self.assertEqual(pick2["name"], "器材手册")
        pick3, reason3 = task.pick_shop_item(items, 500, "不买")
        self.assertIsNone(pick3)
        self.assertIn("不买", reason3)

    def test_shop_free_item_first(self):
        """商店：免费商品优先拿（钱读不到也能拿）。"""
        items = [
            {"name": "训练手册", "price": 100, "quality": "金", "x": 100, "y": 100},
            {"name": "赠品", "price": 0, "quality": "蓝", "x": 100, "y": 200},
        ]
        task = FakeTask([])
        pick, _ = task.pick_shop_item(items, None, "只买彩金紫")
        self.assertEqual(pick["name"], "赠品")

    def test_shop_never_spends_all(self):
        """2026-11-05：**任何日子都不清空**（比赛日也不清空）。

        只有蓝卡时「只买彩金紫」必须停手 —— 旧版这条会被「赛前清空余额」
        变成「金紫买完继续买别的」，钱被花光。现在没有那个分支了。
        2026-11-06：策略改名「只买金和紫」→「只买彩金紫」，白名单补上彩
        （商店目前没有彩卡，留缺口，解锁后自动生效）。
        """
        only_blue = [
            {"name": "旧货", "price": 45, "quality": "蓝", "x": 100, "y": 100},
            {"name": "废纸", "price": 30, "quality": "白", "x": 100, "y": 200},
        ]
        task = FakeTask([])
        pick, reason = task.pick_shop_item(only_blue, 500, "只买彩金紫")
        self.assertIsNone(pick)
        self.assertIn("彩/金/紫", reason)
        # 签名里不许再出现「把钱花光」的那个开关
        import inspect
        params = inspect.signature(task.pick_shop_item).parameters
        self.assertEqual(list(params), ["items", "money", "policy"])

    def test_shop_buys_crit_card_when_policy_enabled(self):
        """2026-11-06：策略白名单 = 彩/金/紫。彩卡买（商店暂无彩卡，留缺口）。"""
        items = [
            {"name": "传说手册", "price": 120, "quality": "彩", "x": 100, "y": 50},
            {"name": "训练手册", "price": 100, "quality": "金", "x": 100, "y": 200},
        ]
        task = FakeTask([])
        pick, _ = task.pick_shop_item(items, 500, "只买彩金紫")
        self.assertEqual(pick["quality"], "彩")            # 彩 > 金

    def test_map_knows_all_places(self):
        """地图模块是**决策层** —— 它知道所有地点（地点模块不许知道别的）。"""
        from src.tasks.training.places.map import MapPlace
        self.assertTrue(hasattr(MapPlace, "today_places"))
        self.assertTrue(hasattr(MapPlace, "prefer_mate_venue"))
        self.assertTrue(hasattr(MapPlace, "weekend_map_page"))

    def test_handlers_are_now_thin(self):
        """handler 只做「认页面 → 问模块 → 照它说的做」。

        判据：handler 里不该再有「买哪张卡」「选哪个选项」的判断逻辑，
        那些应该在 places/ 的模块里。
        """
        import os
        import re
        path = os.path.join("src", "tasks", "training", "handlers", "weekend.py")
        src = open(path, encoding="utf-8").read()
        # 这些判断逻辑应该已经搬进 places/
        for moved in ("def restaurant_order(", "def option_selectable(",
                      "def card_stamina_cost(", "def prefer_mate_venue("):
            self.assertNotIn(moved, src, "%s 还在 handler 里" % moved)


class InteractionsRules(unittest.TestCase):
    """通用交互层：跨地点复用的动作，**零地点知识**。

    2026-11-01 收口时列的 5 个问题，这里逐条锁住。
    """

    def test_confirm_is_region_limited(self):
        """「确定」**不再全屏找** —— 正文里的「确定」不能被点。

        `labels.py` 记过两次同类事故：「点休息确认点到对话框标题」
        「找决定命中正文『…决定通过…』」。全屏找「确定」是同一类问题的
        新入口 —— 地点内页正文里出现「确定」两个字就会点到正文。
        """
        # 正文里有个「确定」，底部条里也有个「确定」→ 必须点底部的那个
        texts = [box(200, 300, "确定要不要休息？", 400, 50),   # 正文
                 box(860, 950, "确定", 200, 60)]              # 底部条
        task = FakeTask(texts)
        task.confirm_after_choice()
        self.assertEqual(task.actions[-1][0], "click_box")
        self.assertEqual(task.actions[-1][1], "确定")
        # 点的是**底部条**那个（y 中心 980），不是正文那个（y 中心 325）
        self.assertEqual(task.actions[-1][3], 980)

    def test_confirm_not_found_does_not_click(self):
        """找不到「确定」就**不点**（原来会全屏乱找）。"""
        texts = [box(200, 300, "确定要不要休息？", 400, 50)]
        task = FakeTask(texts)
        self.assertFalse(task.confirm_after_choice())
        self.assertEqual(task.actions, [])

    def test_buy_counter_is_per_scene(self):
        """每个地点一份购买计数，互不干扰。

        2026-11-01 之前是共享的一对 `self._buy_key/_buy_tries`，
        商店和游戏厅各自手工把对方的状态清零 —— 漏一处就串。
        """
        task = FakeTask([])
        shop = task.buy_counter("商店")
        arcade = task.buy_counter("游戏厅")
        self.assertIsNot(shop, arcade)
        # 商店点不动 3 次
        for _ in range(2):
            self.assertFalse(task.buy_stalled("商店", 100, ("a", 60), counter=shop))
        self.assertTrue(task.buy_stalled("商店", 100, ("a", 60), counter=shop))
        # 游戏厅的计数**不受影响**
        self.assertFalse(task.buy_stalled("游戏厅", 100, ("b", 60), counter=arcade))

    def test_buy_counter_resets_after_reset(self):
        """清掉计数后重新计数。"""
        task = FakeTask([])
        counter = task.buy_counter("商店")
        for _ in range(2):
            task.buy_stalled("商店", 100, ("a", 60), counter=counter)
        task.reset_buy_counter(counter)
        self.assertFalse(task.buy_stalled("商店", 100, ("a", 60), counter=counter))

    def test_exit_with_coords_uses_given_coords(self):
        """退出兜底按**给定**坐标点（通用层不认识页面长什么样）。"""
        task = FakeTask([])
        task.exit_with_coords((0.5, 0.5))
        self.assertEqual(task.actions[-1][0], "click")
        self.assertEqual(task.actions[-1][1], int(task.width * 0.5))

    def test_find_go_button_takes_pattern_from_caller(self):
        """「前往XXX」的正则由**调用方**给（通用层不认识地点）。"""
        task = FakeTask([box(300, 600, "前往划船", 200, 50)])
        found = task.find_go_button(task._texts, r"^前往(划船|野餐|散步)")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "前往划船")
        # 换个正则就匹配不到（通用层自己不知道有哪些地点）
        self.assertIsNone(task.find_go_button(task._texts, r"^前往(当服务员)"))


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

    def test_sunday_never_enters_shop(self):
        """2026-10-29：周末首页不再进店，训练流/商店流也只点「出行」。"""
        for style in ("商店流", "训练流"):
            task = self.styled(style, self._home_texts("星期日"))
            self.assertTrue(task.handle_go_out(task._texts))
            self.assertEqual(task.actions[-1][1], "出行", style)

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

    def test_dialogue_line_is_not_the_go_out_button(self):
        """对话框台词「今天可以出行哦！要不…」不是「出行」按钮（2026-10-30 实跑）。"""
        texts = self._home_texts("星期五") + [
            box(945, 168, "今天可以出行哦！要不", 330, 40)]
        task = self.styled("扭蛋流", texts)
        self.assertTrue(task.handle_go_out(texts))
        self.assertEqual(task.actions[-1][1], "出行")
        # 只有台词、右侧按钮还没出来 → 这一拍什么都不点（等对话推进）
        only_line = [t for t in texts if t.name != "出行"]
        task2 = self.styled("扭蛋流", only_line)
        self.assertFalse(task2.handle_go_out(only_line))
        self.assertEqual(task2.actions, [])

    def test_map_with_gacha_hint_is_not_gacha_page(self):
        """2026-10-31 实跑（野上泷额外周末 · 第3周星期五）：

        地图上步行街的奖励提示写的是「扭蛋增强」（平时是「零用钱+」），
        `STREET_TITLE` 里的「扭蛋」包含匹配把**地图**判成扭蛋页 →
        三张卡读不出来 → 点返回 → 回到训练日主界面，当天的出行白跑。
        用户反馈：「进了出行地图，然后就退回来了，又点了训练」。
        """
        texts = self._map("星期五") + [box(1150, 470, "扭蛋增强", 130, 40)]
        task = self.styled("扭蛋流", texts)
        # 地图不是扭蛋页，而且不能留下「步行街已去过」的错误记忆
        self.assertFalse(task.handle_street(texts))
        self.assertNotIn("步行街", task.weekend_state().get("visited") or set())
        # 交给 handle_weekend：扭蛋流周五去步行街
        self.assertTrue(task.handle_weekend(texts))
        self.assertEqual(task.actions[-1][1], "步行街")

    # ---------- 「扭蛋增强！」标签必须**贴在步行街上**（2026-11-01） ----------

    # 实测真实地图帧（logs/events/1031-194957_handle_weekend.jpg，1549x871）：
    #   标签「扭蛋增强」box=(597,669,225,71)  cx=0.457 cy=0.809
    #   步行街          box=(810,722,126,50)  cx=0.564 cy=0.858
    #   → Δcx=0.106（标签在左边）、Δcy=0.049（标签在上方）
    # 10 张真实帧（1031-173714 / 1031-180755 / 1031-181353 / 1031-193340 /
    # 1031-194957 / 1031-195001 / 1031-234532 / 1101-122549 / 1101-150917 /
    # 1101-150921）几何一致，Δcx=0.105~0.107、Δcy=0.048~0.049。
    # 换算到 1920x1080 的测试版式：标签 (710,829,279,88)、步行街 (1004,894,157,62)。
    BOOST = box(710, 829, "扭蛋增强", 279, 88)
    STREET = box(1004, 894, "步行街", 157, 62)

    def _map_with_boost(self, boost_box=None, day="星期五"):
        """地图 + 贴对位置的「扭蛋增强」标签。"""
        texts = [box(30, 20, "第1周", 120, 40), box(200, 20, day, 130, 40),
                 box(380, 16, "120/130", 210, 53),
                 box(814, 461, "游戏厅", 148, 57), box(1538, 606, "公园", 102, 51),
                 box(258, 748, "餐厅", 104, 60), self.STREET]
        if boost_box is not None:
            texts.append(boost_box)
        return texts

    def test_boost_label_next_to_street_is_detected(self):
        """标签贴在步行街左上 → 认得出来。"""
        task = self.styled("扭蛋流", self._map_with_boost(self.BOOST))
        vb = task._venue_box(task._texts, "步行街")
        self.assertTrue(task.has_boost_label(task._texts, vb))

    def test_boost_label_elsewhere_is_ignored(self):
        """标签出现在**别处** → 不算数（原来只 match_text，全屏命中就误判）。

        位置取地图右上角（远离步行街），模拟队友台词/别的说明文字。
        """
        far = box(1400, 200, "扭蛋增强", 279, 88)
        task = self.styled("扭蛋流", self._map_with_boost(far))
        vb = task._venue_box(task._texts, "步行街")
        self.assertFalse(task.has_boost_label(task._texts, vb))

    def test_boost_label_on_right_of_street_is_ignored(self):
        """标签在步行街**右边** → 不算数（判据要求在左上方）。"""
        right = box(1200, 829, "扭蛋增强", 279, 88)
        task = self.styled("扭蛋流", self._map_with_boost(right))
        vb = task._venue_box(task._texts, "步行街")
        self.assertFalse(task.has_boost_label(task._texts, vb))

    def test_boost_label_wrong_text_is_ignored(self):
        """只有「扭蛋增强」/「扭蛋加强」算数；「零用钱+」不算。"""
        task = self.styled("扭蛋流", self._map_with_boost(
            box(710, 829, "零用钱+", 279, 88)))
        vb = task._venue_box(task._texts, "步行街")
        self.assertFalse(task.has_boost_label(task._texts, vb))

    def test_boost_label_still_forces_street_first(self):
        """钱够（>门槛 270）+ 标签贴对 → 走「扭蛋增强」直进步行街。"""
        texts = (self._map_with_boost(self.BOOST)
                 + [box(1000, 20, str(GACHA_WORK_MONEY_GATE + 50), 120, 45)])
        task = self.styled("扭蛋流", texts)
        task.weekend_state()
        self.assertTrue(task.handle_weekend(texts))
        self.assertEqual(task.actions[-1][1], "步行街")
        self.assertTrue(any("有「扭蛋增强！」" in m for m in task.logs),
                        "应走「扭蛋增强 → 直进步行街」这条路")

    def test_boost_label_elsewhere_does_not_force_street(self):
        """贴错了 → **不**走「扭蛋增强」这条路。

        注意：干跑任务里 frame 是全黑，`venue_has_mate` 会把两个地点都判成
        「有队友」（黑色像素也满足 g<=b），所以这里不能断言最后点了哪里 ——
        要断言的是**有没有走 boost 那条分支**（看日志）。
        """
        texts = self._map_with_boost(box(1400, 200, "扭蛋增强", 279, 88))
        task = self.styled("扭蛋流", texts)
        task.weekend_state()
        self.assertTrue(task.handle_weekend(texts))
        self.assertFalse(any("有「扭蛋增强！」" in m for m in task.logs),
                         "标签不在步行街旁，不该走 boost 分支")

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
        """周六：商店流/小游戏流去餐厅打工（不进步行街/游戏厅）。

        扭蛋流单独一条（见下一个用例）：2026-10-30 起扭蛋流周六先去步行街抽扭蛋，
        没消费才接着去餐厅（constants.WEEKEND_PLAN 的 ("六","扭蛋流")）。
        """
        for style in ("商店流", "小游戏流"):
            task = self.styled(style, self._map("星期六"))
            task.weekend_state()
            self.assertTrue(task.handle_weekend(task._texts), style)
            self.assertEqual(task.actions[-1][1], "餐厅", style)

    def test_saturday_gacha_style_goes_street_then_restaurant(self):
        """周六扭蛋流：先进步行街；走完没消费才去餐厅打工。"""
        task = self.styled("扭蛋流", self._map("星期六"))
        task.weekend_state()
        self.assertTrue(task.handle_weekend(task._texts))
        self.assertEqual(task.actions[-1][1], "步行街")
        task.actions.clear()
        task._weekend["visited"] = {"步行街"}          # 步行街走完，但没买东西
        self.assertTrue(task.handle_weekend(task._texts))
        self.assertEqual(task.actions[-1][1], "餐厅")

    def test_friday_row_is_explicit_for_every_style(self):
        """周五（野上泷「额外周末」）在 WEEKEND_PLAN 里**显式**有行。

        2026-11-03：以前表里只有 ("五","扭蛋流")，其它流派靠 `today_places`
        的「读不到星期 → 按周六兜底」隐式拿到周六那行 —— 改周六会顺手改掉周五，
        从表里也看不出周五到底去哪。现在周五三行与周六一致（行为不变）。
        """
        for style in ("训练流", "商店流", "扭蛋流", "小游戏流"):
            self.assertIn(("五", style), WEEKEND_PLAN, style)
            task = self.styled(style, self._map("星期五"))
            st = task.weekend_state()
            self.assertEqual(task.today_places("五", st),
                             task.today_places("六", st), style)

    def test_map_entry_marks_outing_used(self):
        """**进了地图**才记「今天的出行机会用掉了」（记账点在 handle_weekend）。"""
        for day in ("星期五", "星期六", "星期日"):
            task = self.styled("扭蛋流", self._map(day))
            task.weekend_state()
            self.assertFalse(task.weekend_state().get("outing_done"), day)
            self.assertTrue(task.handle_weekend(task._texts), day)
            self.assertTrue(task.weekend_state().get("outing_done"), day)

    def _gacha_map(self, day, money=None):
        """扭蛋流地图：可带顶栏零用钱（不传 = OCR 读不到）。"""
        texts = self._map(day)
        if money is not None:
            texts.append(box(1000, 20, str(money), 120, 45))
        return texts

    def test_gacha_low_money_goes_restaurant_even_with_mate(self):
        """2026-11-03 用户口径：钱 ≤ 门槛直接打工，**不看队友**（周日也一样）。

        门槛 = GACHA_WORK_MONEY_GATE（2026-11-07 从 300 改 270）：
        270 整也算「≤门槛」，271 不算。
        """
        gate = GACHA_WORK_MONEY_GATE
        for day in ("星期五", "星期六", "星期日"):
            for money in (0, gate - 50, gate):   # gate 整也算「≤gate」
                texts = self._gacha_map(day, money)
                task = self.styled("扭蛋流", texts)
                task.venue_has_mate = lambda b: True    # 两边都有队友也不算数
                task.weekend_state()
                self.assertTrue(task.handle_weekend(texts), (day, money))
                self.assertEqual(task.actions[-1][1], "餐厅", (day, money))

    def test_gacha_money_unknown_goes_street(self):
        """钱读不到 → 去步行街（不拿打工赌今天的消费）。"""
        texts = self._gacha_map("星期六")
        task = self.styled("扭蛋流", texts)
        task.venue_has_mate = lambda b: False
        task.weekend_state()
        self.assertTrue(task.handle_weekend(texts))
        self.assertEqual(task.actions[-1][1], "步行街")

    def test_gacha_over_300_no_mate_weekday_work_sunday_street(self):
        """钱 >门槛（270）都没队友：周五/六打工，周日步行街。"""
        want = {"星期五": "餐厅", "星期六": "餐厅", "星期日": "步行街"}
        for day, place in want.items():
            texts = self._gacha_map(day, GACHA_WORK_MONEY_GATE + 50)
            task = self.styled("扭蛋流", texts)
            task.venue_has_mate = lambda b: False
            task.weekend_state()
            self.assertTrue(task.handle_weekend(texts), day)
            self.assertEqual(task.actions[-1][1], place, day)

    def test_gacha_over_300_only_one_mate_goes_there(self):
        """钱 >门槛（270）：只有一边有队友 → 去有队友的那边。"""
        for mate, place in (("餐厅", "餐厅"), ("步行街", "步行街")):
            texts = self._gacha_map("星期六", GACHA_WORK_MONEY_GATE + 50)
            task = self.styled("扭蛋流", texts)
            task.venue_has_mate = (lambda name: lambda b: b.name == name)(mate)
            task.weekend_state()
            self.assertTrue(task.handle_weekend(texts), mate)
            self.assertEqual(task.actions[-1][1], place, mate)

    # ---------- 步行街：增强 / 打折都优先（用户 2026-11-03） ----------

    # 名字左边同一行的「打折」标签（venue_on_discount 判据：tcx < cx、Δcy ≤ 0.12h）
    STREET_DISCOUNT = box(900, 895, "打折", 70, 55)
    RESTAURANT_DISCOUNT = box(150, 750, "打折", 70, 55)

    def test_gacha_street_discount_pulls_to_street(self):
        """钱 >门槛（270）、都没队友、周六（原口径去餐厅）：**步行街在打折 → 改去步行街**。

        用户 2026-11-03 口径：「扭蛋增强或者打折都优先步行街，其他逻辑不变」。
        """
        texts = (self._gacha_map("星期六", GACHA_WORK_MONEY_GATE + 50)
                 + [self.STREET_DISCOUNT])
        task = self.styled("扭蛋流", texts)
        task.venue_has_mate = lambda b: False
        task.weekend_state()
        self.assertTrue(task.handle_weekend(texts))
        self.assertEqual(task.actions[-1][1], "步行街")
        self.assertTrue(any("步行街在打折" in m for m in task.logs), task.logs)

    def test_gacha_restaurant_discount_does_not_pull(self):
        """打折的是**餐厅** → 不改道（只有步行街的折扣才算）。"""
        want = {"星期五": "餐厅", "星期六": "餐厅", "星期日": "步行街"}
        for day, place in want.items():
            texts = (self._gacha_map(day, GACHA_WORK_MONEY_GATE + 50)
                     + [self.RESTAURANT_DISCOUNT])
            task = self.styled("扭蛋流", texts)
            task.venue_has_mate = lambda b: False
            task.weekend_state()
            self.assertTrue(task.handle_weekend(texts), day)
            self.assertEqual(task.actions[-1][1], place, day)
            self.assertFalse(any("步行街在打折" in m for m in task.logs), day)

    def test_gacha_low_money_ignores_street_discount(self):
        """钱 ≤门槛（270）：步行街在打折也照样去餐厅打工（打折不豁免钱门槛）。"""
        for money in (0, GACHA_WORK_MONEY_GATE - 50, GACHA_WORK_MONEY_GATE):
            texts = self._gacha_map("星期六", money) + [self.STREET_DISCOUNT]
            task = self.styled("扭蛋流", texts)
            task.venue_has_mate = lambda b: True        # 有队友也不算数
            task.weekend_state()
            self.assertTrue(task.handle_weekend(texts), money)
            self.assertEqual(task.actions[-1][1], "餐厅", money)

    def test_gacha_boost_and_discount_both_reported(self):
        """增强标签 + 打折同时命中 → 同一档，日志把两个标签都写出来。"""
        texts = (self._map_with_boost(self.BOOST, day="星期六")
                 + [box(1000, 20, str(GACHA_WORK_MONEY_GATE + 50), 120, 45),
                    self.STREET_DISCOUNT])
        task = self.styled("扭蛋流", texts)
        task.venue_has_mate = lambda b: False
        task.weekend_state()
        self.assertTrue(task.handle_weekend(texts))
        self.assertEqual(task.actions[-1][1], "步行街")
        self.assertTrue(any("有「扭蛋增强！」" in m and "步行街在打折" in m
                            for m in task.logs), task.logs)

    # ---------- 第 7 周以后：看到「新品上市」提示才进店（用户 2026-11-05） ----------
    # 判据本体在 `daycycle.late_shop_step`（一天的入口 ③），调用点是 flow.run 主循环里、
    # `_handle_any` 之前 —— 所以这里直接测那个方法，不再另立 handler。
    # 旧口径（2026-11-03「每天都进店」）已作废：没有提示就一下都不点。

    # 训练日主界面实测「商店」框 (1724,216,76,38)（logs/debug/1026-112852_nochange3/ocr.txt），
    # 周末首页是 (1389,170,66,37)（logs/debug/1029-140118_stall5s/ocr.txt）—— 都在 shop_entry 里。
    SHOP_BTN = box(1724, 216, "商店", 76, 38)
    # 提示 = 橙色标签「新品上市」，挂在「商店」按钮**正上方**同一列
    # （用户 2026-11-05 实机截图：第 3 周 星期一 比赛日主界面，1552×871 里
    #   标签中心 ≈(0.920,0.156)、按钮中心 ≈(0.920,0.230)，Δcy ≈ 0.07 屏高/75px@1080）。
    SHOP_BADGE = box(1702, 142, "新品上市", 120, 36)

    def _late_hub(self, week, day="星期三", with_shop=True, badge=None, extra=()):
        texts = [box(30, 20, f"第{week}周", 120, 40), box(200, 20, day, 130, 40),
                 box(380, 16, "100/130", 210, 53),
                 box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]
        if with_shop:
            texts.append(self.SHOP_BTN)
        if badge is not None:
            texts.append(badge)
        return texts + list(extra)

    def test_late_shop_enters_when_fresh_badge(self):
        """第 7 周起：商店上方有「新品上市」→ 进店（先于训练/休息）。"""
        for week in (7, 8, 12):
            task = self.styled("扭蛋流",
                               self._late_hub(week, badge=self.SHOP_BADGE))
            self.assertTrue(task.late_shop_step(task._texts), week)
            self.assertEqual(task.actions[-1][1], "商店", week)

    def test_late_shop_skips_without_fresh_badge(self):
        """没有「新品上市」提示 → 一天都不进店（2026-11-05 取代「每天都进」）。"""
        for week in (7, 8, 12):
            task = self.styled("扭蛋流", self._late_hub(week))
            self.assertFalse(task.late_shop_step(task._texts), week)
            self.assertEqual(task.actions, [], week)

    def test_late_shop_badge_may_be_read_as_single_word(self):
        """OCR 只读出「新品」（或「上新」）也认。"""
        for name in ("新品", "上新"):
            task = self.styled("扭蛋流",
                               self._late_hub(7, badge=box(1724, 168, name, 60, 30)))
            self.assertTrue(task.late_shop_step(task._texts), name)
            self.assertEqual(task.actions[-1][1], "商店", name)

    def test_late_shop_badge_merged_into_button(self):
        """提示和按钮被 OCR 读成一整块（框名带「新品上市」）→ 也算有提示。"""
        merged = box(1702, 142, "新品上市 商店", 120, 110)
        task = self.styled("扭蛋流", self._late_hub(7, with_shop=False,
                                                    badge=merged))
        self.assertTrue(task.late_shop_step(task._texts))
        self.assertEqual(task.actions[-1][1], "新品上市 商店")

    def test_late_shop_covers_weekend_home(self):
        """周末首页（同时有「出行」）同样只在有上新提示时进店。"""
        home = self._home_texts("星期六")
        home[0] = box(30, 20, "第8周", 120, 40)
        task = self.styled("扭蛋流", home + [self.SHOP_BTN, self.SHOP_BADGE])
        self.assertTrue(task.late_shop_step(task._texts))
        self.assertEqual(task.actions[-1][1], "商店")

    def test_late_shop_not_before_week7(self):
        """第 7 周之前这条不生效：就算有上新提示也不进店。"""
        for week in (1, 5, 6):
            task = self.styled("扭蛋流",
                               self._late_hub(week, badge=self.SHOP_BADGE))
            self.assertFalse(task.late_shop_step(task._texts), week)
            self.assertEqual(task.actions, [], week)

    def test_late_shop_badge_must_sit_above_button(self):
        """提示必须挂在按钮**头上**：同一行的左边、太高、太远都不算。"""
        cases = [
            box(1580, 216, "新品上市", 120, 38),      # 同一行的左边（不是头上）
            box(1702, 40, "新品上市", 120, 40),       # 太高（贴顶栏）
            box(400, 170, "新品上市", 200, 40),       # 离太远（正文台词）
        ]
        for badge in cases:
            task = self.styled("扭蛋流", self._late_hub(7, badge=badge))
            self.assertFalse(task.late_shop_step(task._texts), badge.name)
            self.assertEqual(task.actions, [], badge.name)

    def test_fresh_badge_ignores_shop_page_timestamp(self):
        """商店**内页**底部的「上新时间」不是入口提示（`SHOP_FRESH_EXCLUDE`）。"""
        task = self.styled("扭蛋流", self._late_hub(
            7, badge=box(1724, 160, "上新时间", 76, 34)))
        self.assertFalse(task.late_shop_step(task._texts))
        badge, _ = task.shop_fresh_badge(task._texts, self.SHOP_BTN)
        self.assertIsNone(badge)

    def test_late_shop_once_per_day_but_retries_empty_click(self):
        """点空要重试（最多 SHOP_ENTRY_TRIES 次）；真进过店当天就不点了。"""
        task = self.styled("扭蛋流", self._late_hub(7, badge=self.SHOP_BADGE))
        self.assertTrue(task.late_shop_step(task._texts))
        self.assertTrue(task.late_shop_step(task._texts))     # 点空 → 允许再点一次
        task.actions.clear()
        self.assertFalse(task.late_shop_step(task._texts))    # 第 3 次停手
        self.assertEqual(task.actions, [])
        # handle_shop 确认看到商店页之后记的账
        task2 = self.styled("扭蛋流", self._late_hub(7, badge=self.SHOP_BADGE))
        task2._shop_day = (7, "三")
        self.assertFalse(task2.late_shop_step(task2._texts))

    def test_late_shop_ignores_store_word_in_dialogue(self):
        """台词里有「商店」但右上角没有入口 → 一下都不点。"""
        texts = self._late_hub(
            7, with_shop=False,
            extra=[box(600, 800, "那家商店早上六点会卖打折商品", 520, 40)])
        task = self.styled("扭蛋流", texts)
        self.assertFalse(task.late_shop_step(texts))
        self.assertEqual(task.actions, [])

    def test_late_shop_steps_aside_on_shop_page(self):
        """已经在商店内页 → 交给 handle_shop 买/关，别再点入口。"""
        texts = self._late_hub(7, badge=self.SHOP_BADGE,
                               extra=[box(200, 300, "能力详情", 150, 40),
                                      box(300, 300, "剩余", 100, 40)])
        task = self.styled("扭蛋流", texts)
        self.assertFalse(task.late_shop_step(texts))
        self.assertEqual(task.actions, [])

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

    def test_pre_match_shop_for_training_and_shop_style(self):
        """比赛日主界面：训练流/商店流先点一次商店，再进比赛；其他流派不点。"""
        def hub(style, action="比赛", rest=True):
            texts = [box(30, 20, "第3周", 120, 40),
                     box(200, 20, "星期一", 130, 40),
                     box(380, 16, "120/130", 210, 53),
                     box(700, 110, "VS", 60, 40),        # 比赛日才有（校验见 handle_pre_match_shop）
                     box(1420, 190, "商店", 70, 38),
                     box(1450, 900, action, 120, 60)]
            if rest:
                texts.append(box(1600, 900, "休息", 120, 60))
            task = self.styled(style, texts)
            task._shop_day = None
            return task

        task = hub("训练流")
        self.assertTrue(task.handle_pre_match_shop(task._texts))
        self.assertEqual(task.actions[-1][1], "商店")
        task.actions.clear()
        # 2026-10-30：点完不等于进店，`_shop_day` 改由 handle_shop 确认商店页真的出现
        # 之后才记账。所以这里允许再点一次（第 2 次），第 3 次就停手，别整天空点。
        self.assertTrue(task.handle_pre_match_shop(task._texts))
        task.actions.clear()
        self.assertFalse(task.handle_pre_match_shop(task._texts))
        # 真进过店（_shop_day 记上）当天就不再点了
        task._shop_day = (3, "一")
        self.assertFalse(task.handle_pre_match_shop(task._texts))

        # 比赛日主界面没有「休息」也要进（2026-10-29 实机第 5/6 周漏进）
        task = hub("训练流", rest=False)
        self.assertTrue(task.handle_pre_match_shop(task._texts))
        self.assertEqual(task.actions[-1][1], "商店")

        task = hub("商店流")
        self.assertTrue(task.handle_pre_match_shop(task._texts))
        self.assertEqual(task.actions[-1][1], "商店")

        for style in ("扭蛋流", "小游戏流"):
            task = hub(style)
            self.assertFalse(task.handle_pre_match_shop(task._texts), style)

        # 普通训练日（主按钮是训练）不进店
        task = hub("训练流", action="训练")
        self.assertFalse(task.handle_pre_match_shop(task._texts))

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

    # ---------- 识别队长（旧 handle_style_probe 已删除）----------
    # 2026-11-01：识别改成**主动**做（daycycle._resolve_style_step）：
    # 接手别人的局 → 点返回回到「带日历的页面」→ 点日历 → 抓名字 → 查表定流派；
    # 认不出 → 弹窗「未能识别到队长」，**不许保底**。
    # 旧的 handle_style_probe 只在训练日触发（要求「星期」+「休息」），
    # 接手的局在周末永远点不了日历 → 扭蛋流跑成训练流（19:19 事故）。
    # 覆盖见 StyleResolution 里的 test_path_b_*。

    def test_start_training_resets_style(self):
        """点「开始训练」= 新的一局：流派清空，等日历重新认队长。"""
        texts = [box(700, 100, "确认编队", 300, 50),
                 box(900, 800, "开始训练", 240, 60)]
        task = FakeTask(texts)
        task._style = "商店流"
        self.assertTrue(task.handle_confirm_team(texts))
        self.assertIsNone(task._style)
        self.assertEqual(task.style, "训练流")      # 还没认出来时的兜底

    # ---------- 周四休息阈值 ----------

    def test_thursday_rest_threshold_by_style(self):
        """周四硬阈值只对**扭蛋流 / 小游戏流**生效（60）；其它流派周四没有特殊限制。

        用户 2026-11-04 口径（constants.THURSDAY_REST_THRESHOLD_BY_STYLE）：
        「周二的 65 改成周四精力低于 60 就休息」，流派白名单不变
        （扭蛋流 / 小游戏流）。**周二现在没有任何特殊阈值。**
        """
        base = {"休息精力阈值": 40}

        def limit(style, day):
            task = FakeTask([], config=dict(base))
            task._style = style
            return task.rest_threshold(day)

        for style in ("扭蛋流", "小游戏流"):
            self.assertEqual(limit(style, "四"), 60, style)
        for style in ("训练流", "商店流"):
            self.assertEqual(limit(style, "四"), 40, style)
        for style in ("训练流", "商店流", "扭蛋流", "小游戏流"):
            self.assertEqual(limit(style, "二"), 40, style)
            self.assertEqual(limit(style, "五"), 40, style)
            self.assertEqual(limit(style, "一"), 40, style)
        # 配置里的阈值更高时以配置为准（取 max，不能因为周四就下调）
        for style in ("扭蛋流", "小游戏流", "训练流"):
            task = FakeTask([], config={"休息精力阈值": 80})
            task._style = style
            self.assertEqual(task.rest_threshold("四"), 80, style)

    def test_thursday_low_stamina_rests(self):
        """周四 50 精力：扭蛋流直接休息（门槛 60）。"""
        texts = [box(30, 20, "第3周", 120, 40), box(200, 20, "星期四", 130, 40),
                 box(380, 16, "50/130", 210, 53),
                 box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]
        task = FakeTask(texts)
        task._style = "扭蛋流"
        task.ensure_speed = lambda *a, **k: False   # 零帧永远判不出 2x，跳过那一拍
        task._hub_ticks = task.HUB_SETTLE_TICKS     # 模拟「出行」按钮已淡入
        self.assertTrue(task.handle_training_day(texts))
        self.assertEqual(task.actions[-1][1], "休息")

    def test_thursday_gacha_rests_10_points_earlier(self):
        """2026-11-04 用户口径：扭蛋流 / 小游戏流周四门槛 60，其它流派周四没有特殊限制。

        精力 59：扭蛋流、小游戏流（< 60）→ 休息；训练流/商店流（只守全局 40）→ 训练。
        """
        def run(style, stamina, day="星期四"):
            texts = [box(30, 20, "第3周", 120, 40), box(200, 20, day, 130, 40),
                     box(380, 16, f"{stamina}/130", 210, 53),
                     box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]
            task = FakeTask(texts)
            task._style = style
            task.ensure_speed = lambda *a, **k: False
            task._hub_ticks = task.HUB_SETTLE_TICKS
            self.assertTrue(task.handle_training_day(texts))
            return task.actions[-1][1]

        self.assertEqual(run("扭蛋流", 59), "休息")
        self.assertEqual(run("小游戏流", 59), "休息")
        self.assertEqual(run("训练流", 59), "训练")
        self.assertEqual(run("商店流", 59), "训练")
        # 60 是下限（不含）：精力 60 仍照常训练
        self.assertEqual(run("扭蛋流", 60), "训练")
        self.assertEqual(run("小游戏流", 60), "训练")
        # 周二不再有任何硬阈值：精力 59（< 旧的 65）也照常训练
        self.assertEqual(run("扭蛋流", 59, day="星期二"), "训练")
        self.assertEqual(run("小游戏流", 59, day="星期二"), "训练")

    def test_training_and_shop_styles_thursday_below_60_train(self):
        """周四 50 精力：训练流 / 商店流回落到配置的 40 → 照常训练。"""
        texts = [box(30, 20, "第3周", 120, 40), box(200, 20, "星期四", 130, 40),
                 box(380, 16, "50/130", 210, 53),
                 box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]
        for style in ("训练流", "商店流"):
            task = FakeTask(texts)
            task._style = style
            task.ensure_speed = lambda *a, **k: False
            task._hub_ticks = task.HUB_SETTLE_TICKS
            self.assertTrue(task.handle_training_day(texts), style)
            self.assertEqual(task.actions[-1][1], "训练", style)

    # ---------- 餐厅：默认当服务员，桌游GM 队友多 2 人才改选左边 ----------

    def _restaurant(self, stamina=None):
        # 真实布局（docs/shots/restaurant_cards.png）：桌游GM 在左、当服务员在右。
        texts = [box(700, 100, "在餐厅", 300, 50),
                 box(600, 500, "桌游GM", 200, 60),
                 box(1000, 500, "当服务员", 200, 60)]
        if stamina:
            texts.append(box(380, 16, stamina, 210, 53))
        return texts

    def _restaurant_task(self, stamina=None, mates=None):
        """mates 按选项名给队友个数（覆盖数脸判据，帧是黑的数不出人）。"""
        task = FakeTask(self._restaurant(stamina))
        task._style = "商店流"
        counts = dict(mates or {})
        task.mate_face_count = lambda b, **k: counts.get(b.name.strip(), 0)
        return task

    def test_restaurant_tie_picks_servant(self):
        """队友一样多（都数不到）→ 默认选当服务员（右边、耗精力）。"""
        task = self._restaurant_task()
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "当服务员")

    def test_restaurant_gm_with_two_more_teammates_wins(self):
        """桌游GM 队友 2 个、当服务员 0 个（多两个）→ 选左边的桌游GM。"""
        task = self._restaurant_task(mates={"桌游GM": 2, "当服务员": 0})
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "桌游GM")

    def test_restaurant_gm_with_three_more_teammates_wins(self):
        """多三个也比多两个更该选（口径是「多两个及以上」）。"""
        task = self._restaurant_task(mates={"桌游GM": 3, "当服务员": 1})
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "桌游GM")

    def test_restaurant_gm_with_one_more_teammate_still_servant(self):
        """只多一个不够 —— 仍然选当服务员（2026-11-03 用户口径）。"""
        task = self._restaurant_task(mates={"桌游GM": 1, "当服务员": 0})
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "当服务员")

    def test_restaurant_gm_two_but_servant_one_still_servant(self):
        """2 个 vs 1 个只差 1 个 → 仍然当服务员。"""
        task = self._restaurant_task(mates={"桌游GM": 2, "当服务员": 1})
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "当服务员")

    def test_restaurant_low_stamina_takes_gm(self):
        """精力不够当服务员（默认按 25 算）→ 改选不耗精力的桌游GM。"""
        task = self._restaurant_task(stamina="10/130")
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "桌游GM")

    def test_restaurant_enough_stamina_takes_servant(self):
        """精力够 → 选当服务员（默认那张）。"""
        task = self._restaurant_task(stamina="90/130")
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "当服务员")

    def test_restaurant_reward_text_is_not_a_cost(self):
        """卡片上的「精力+30」是收益，不能当成消耗（否则好选项会被挡掉）。"""
        texts = self._restaurant(stamina="25/130")
        texts.append(box(1000, 620, "精力+30", 200, 50))
        task = FakeTask(texts)
        task._style = "商店流"
        task.mate_face_count = lambda b, **k: 0
        self.assertTrue(task.handle_restaurant(texts))
        self.assertEqual(task.actions[-1][1], "当服务员")

    def test_restaurant_uses_cost_printed_on_card(self):
        """卡片写明「精力-30」时以卡片为准：精力 25 不够 → 选桌游GM。"""
        texts = self._restaurant(stamina="25/130")
        texts.append(box(1000, 620, "精力-30", 200, 50))
        task = FakeTask(texts)
        task._style = "商店流"
        task.mate_face_count = lambda b, **k: 0
        self.assertTrue(task.handle_restaurant(texts))
        self.assertEqual(task.actions[-1][1], "桌游GM")

    def test_restaurant_counts_faces_on_real_frame(self):
        """真帧（2026-10-31 1549x871）：桌游GM 头顶 2 个队友、当服务员 1 个。

        旧判据（深蓝胶囊「有/没有」）两张都读成 1 → 平局 → 用户要的
        「多两个选左边」永远不生效。
        """
        path = os.path.join(ROOT, "docs", "shots", "restaurant_cards.png")
        if cv2 is None or not os.path.exists(path):
            self.skipTest("缺少样本 %s" % path)
        frame = cv2.imread(path)
        task = DryRunTask(frame, self._restaurant())
        got = {
            b.name: task.mate_face_count(b)
            for b in [box(544, 450, "桌游GM", 132, 50),
                      box(873, 453, "当服务员", 139, 43)]
        }
        self.assertEqual(got, {"桌游GM": 2, "当服务员": 1})

    # ---------- 餐厅：数脸准了，但「数不到就退回深蓝兜底」会把人数加脏 ----------

    @staticmethod
    def _restaurant_frame(faces_on_left=0, navy_on_right=True):
        """合成一帧：左卡头顶画 N 张 chibi 脸，右卡兜底带里画一块深蓝（= 背心图标）。

        颜色抄生产判据：脸 = 亮暖肤色（r240/g215/b205），深蓝 = (b150,g40,r30)。
        """
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for i in range(faces_on_left):
            x = 560 + i * 80
            frame[260:292, x:x + 32] = (205, 215, 240)      # BGR
        if navy_on_right:
            frame[270:330, 1040:1100] = (150, 40, 30)       # BGR：深蓝（r<120）
        return frame

    def test_restaurant_face_count_wins_over_deep_fallback(self):
        """2026-11-03 实跑（logs/events/1103-203745）：左卡 2 张脸、右卡 0 张。

        右卡自己的深蓝背心图标让 `mates_on_card` 的深蓝占比 0.107 > 0.08 →
        白送 1 个队友 → 「2 人 vs 1 人」只多 1 个 → 规则不生效，点到当服务员。
        数到脸就只认脸：桌游GM（2）比当服务员（0）多 2 个 → 选左边。
        """
        if cv2 is None:
            self.skipTest("没有 cv2")
        task = FakeTask(self._restaurant())
        task._style = "商店流"
        task._executor.frame = self._restaurant_frame(faces_on_left=2)
        called = []
        task.mates_on_card = lambda b, icons: called.append(b.name) or 1
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(task.actions[-1][1], "桌游GM")
        self.assertEqual(called, [], "数到脸就不该再走深蓝兜底")

    def test_restaurant_deep_fallback_still_used_without_any_face(self):
        """整页一个脸都没数到（旧版深蓝圆标记）→ 仍然用深蓝兜底，规则不失效。"""
        if cv2 is None:
            self.skipTest("没有 cv2")
        task = FakeTask(self._restaurant())
        task._style = "商店流"
        task._executor.frame = self._restaurant_frame(faces_on_left=0)
        seen = []
        task.mates_on_card = lambda b, icons: seen.append(b.name) or 0
        self.assertTrue(task.handle_restaurant(task._texts))
        self.assertEqual(sorted(seen), ["当服务员", "桌游GM"])


class EmergencyStopSmoke(unittest.TestCase):
    """F8 轮询兜底：注册失败也要能启动，ready 必须置位（旧版有竞态）。"""

    def test_ready_is_set_after_start(self):
        from src.util.emergency_stop import EmergencyStop
        stop = EmergencyStop()
        stop.ensure_started(wait=2.0)
        self.assertTrue(stop.ready.is_set())
        self.assertIsNotNone(stop._poll_thread)


class CaptainRoster(unittest.TestCase):
    """队长列表（监控页下拉）的数据源：一个角色只能出现一次。

    2026-11-03 用户发现下拉里有两个「羽?川湊」——采集脚本把「濑」认成「漱」，
    旧表为了兼容把两种写法都收进 STYLE_BY_NAME，而那张表同时是下拉的数据源。
    现在错字走 STYLE_ALIASES，只做查询兼容、不进列表。
    """

    def test_no_duplicate_names_in_dropdown(self):
        from src.ui.MonitorTab import MonitorTab
        from src.tasks.ui.labels import norm_name
        groups = MonitorTab._captains()
        names = [n for names in groups.values() for n in names]
        norms = [norm_name(n) for n in names]
        self.assertEqual(len(norms), len(set(norms)), f"下拉里有重复角色: {names}")

    def test_wrong_spelling_still_resolves_style(self):
        from src.tasks.training.parsers import TrainingParsersMixin
        self.assertEqual(TrainingParsersMixin.style_for_name("羽漱川湊"), "扭蛋流")
        self.assertEqual(TrainingParsersMixin.style_for_name("羽濑川湊"), "扭蛋流")

    def test_every_roster_character_has_a_style(self):
        """docs/roster 里的 SSR / SR 全部要能查到流派，否则当队长会掉回训练流。"""
        from src.tasks.training.constants import STYLE_ALIASES, STYLE_BY_NAME
        known = set(STYLE_BY_NAME) | set(STYLE_ALIASES)
        missing = []
        for f in ("ssr_roster.json", "sr_roster.json"):
            with open(os.path.join(ROOT, "docs", "roster", f),
                      encoding="utf-8") as fh:
                data = json.load(fh)
            missing += [c["name"] for c in data if c["name"] not in known]
        self.assertEqual(missing, [], f"角色表缺: {missing}")

    def test_monitor_style_from_config_handles_alias(self):
        from src.ui.MonitorTab import MonitorTab
        self.assertEqual(MonitorTab._style_from_config({"指定队长": "羽漱川湊"}), "扭蛋流")
        self.assertEqual(MonitorTab._style_from_config({"指定队长": "澪奈·贾妮丝"}), "训练流")


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

    # --- 指纹归一（2026-11-03 限时比赛活动实机）---
    # 比赛画面每拍都有倒计时（05:47→05:44）和比分（8:1→10:1）在变，
    # 旧指纹取整帧文字 → 同一页永远算「新画面」→ 限流一次都没触发，
    # handle_event_choice 在同一页连点了 20 次。

    def test_timer_and_score_do_not_change_sig(self):
        from src.tasks.QuickMatchTask import QuickMatchTask
        first = [box(60, 30, "05:47", 90, 40),
                 box(1120, 20, "8", 40, 30),
                 box(1258, 762, "佐久间", 90, 40)]
        later = [box(60, 30, "05:44", 90, 40),
                 box(1120, 20, "10", 40, 30),
                 box(1258, 762, "佐久间", 90, 40)]
        self.assertEqual(QuickMatchTask._screen_sig(first),
                         QuickMatchTask._screen_sig(later))

    def test_sig_still_separates_different_pages(self):
        from src.tasks.QuickMatchTask import QuickMatchTask
        a = [box(884, 644, "继续匹配", 144, 43)]
        b = [box(516, 644, "暂时退出", 141, 44)]
        self.assertNotEqual(QuickMatchTask._screen_sig(a),
                            QuickMatchTask._screen_sig(b))

    def test_repeat_guard_fires_while_timer_ticks(self):
        """端到端：倒计时在走也要能数到第 3 拍、存证停手。"""
        from src.tasks.QuickMatchTask import QuickMatchTask, REPEAT_LIMIT
        t = self._bare()
        texts = [box(60, 30, "05:47", 90, 40),
                 box(1258, 762, "佐久间", 90, 40)]
        for i in range(REPEAT_LIMIT):
            sig = QuickMatchTask._screen_sig(texts)
            QuickMatchTask._note_hit(t, texts, sig, "handle_event_choice")
            texts[0] = box(60, 30, f"05:{44 - i:02d}", 90, 40)     # 倒计时在走
        self.assertEqual(len(t.dumps), 1, "同一页第 3 拍必须存证停手")
        self.assertIsNotNone(t._mute_sig)


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

    def test_system_ui_methods_available(self):
        """▶▶ 快进这类系统级控件搬到 SystemUIHandlers 后，装配必须跟上。

        2026-11-03 实机报错：`'QuickMatchTask' object has no attribute
        'click_match_fast_forward'` —— handlers/match.py 的 handle_in_match
        已经改成调 SystemUIHandlers.click_match_fast_forward，但 QuickMatchTask
        只继承了 MatchHandlers，没继承 SystemUIHandlers（TrainingTask 补了，它漏了）。
        单元测试当时只断言 handler 可调用，没跑进 handle_in_match 的那条分支。
        """
        from src.tasks.QuickMatchTask import QuickMatchTask
        from src.tasks.training.systemui import SystemUIHandlers
        missing = [name for name in dir(SystemUIHandlers)
                   if not name.startswith("_") and not hasattr(QuickMatchTask, name)]
        self.assertEqual(missing, [])
        self.assertIs(QuickMatchTask.click_match_fast_forward,
                      SystemUIHandlers.click_match_fast_forward)

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

    # --- 左下角 ▶▶ 的像素判据 ---
    # 2026-10-31 事故：`_match_intro_at` 只在「前往比赛」分支置位，
    # 实战里点完「跳过比赛」游戏会自己进场 → 整段开场一次没点，
    # 跳球后 HUD 出来才开始点。所以「在比赛里」必须能直接看按钮。

    @staticmethod
    def frame_with_pill(w=1920, h=1080):
        """造一帧「左下角有 ▶▶」：深藏青药丸 + 白双三角 + 浅灰描边。"""
        frame = np.full((h, w, 3), 200, dtype=np.uint8)          # 亮球场
        x1, y1 = int(w * 0.020), int(h * 0.911)
        x2, y2 = int(w * 0.094), int(h * 0.969)
        frame[y1:y2, x1:x2] = (36, 30, 26)
        cv2.rectangle(frame, (x1, y1), (x2 - 1, y2 - 1), (150, 150, 150), 2)
        for k in (0.30, 0.56):                                   # 两个 ▶
            cx = x1 + int((x2 - x1) * k)
            cy = (y1 + y2) // 2
            r = int((y2 - y1) * 0.22)
            cv2.fillPoly(frame, [np.array([(cx, cy - r), (cx, cy + r),
                                           (cx + int(r * 1.5), cy)])], (255, 255, 255))
        return frame

    def test_ff_pill_detected_on_intro_frame(self):
        task = FakeTask(list(self.LINEUP))
        self.assertTrue(task.match_ff_visible(self.frame_with_pill()))

    def test_intro_fast_forward_without_timestamp(self):
        """没有 `_match_intro_at`（游戏自己进场）也要点 ▶▶。"""
        task = FakeTask(list(self.LINEUP))
        self.assertFalse(task.handle_in_match(task._texts))      # 黑帧：认不出按钮
        task._tick_frame = self.frame_with_pill()
        self.assertTrue(task.handle_in_match(task._texts))
        self.assertEqual(task.actions[-1], ("click", 119, 1002))

    def test_letterbox_bar_is_not_pill(self):
        """信箱黑边（整条贴边黑）不是按钮。"""
        frame = np.full((1080, 1920, 3), 200, dtype=np.uint8)
        frame[int(1080 * 0.90):, :] = 10
        task = FakeTask(list(self.LINEUP))
        self.assertFalse(task.match_ff_visible(frame))

    def test_plain_screen_is_not_pill(self):
        task = FakeTask(list(self.LINEUP))
        blank = np.full((1080, 1920, 3), 200, dtype=np.uint8)
        self.assertFalse(task.match_ff_visible(blank))

    def test_intro_with_pill_is_not_eaten_by_transition(self):
        """VS/转场页同时写着 VS 又有 ▶▶ 时，让给 handle_in_match，别在这里干等。"""
        texts = [box(800, 400, "VS", 200, 100)]
        task = FakeTask(texts)
        task._tick_frame = self.frame_with_pill()
        self.assertFalse(task.handle_match_intro(texts))
        self.assertEqual(task.actions, [])


class ActivityMatchResume(unittest.TestCase):
    """限时比赛活动赛程页（2026-11-03 用户截图）。

    页面文字：`第1轮进行中 / 已晋级A组 / 胜利·失败 / 暂时退出 / 继续匹配`，
    **没有「下一步」**。旧代码会被 RESULT_TITLE_RE 的「胜利/失败」认成结算页，
    再掉进「点屏幕中央」的兜底（点不动）；而事件 handler 更会把「暂时退出」
    当竖向选项点掉 = 直接退出活动。
    """

    TEXTS = [box(324, 138, "第1轮进行中", 186, 43),
             box(705, 235, "已晋级A组", 137, 34),
             box(450, 479, "胜利", 65, 37),
             box(595, 479, "失败", 67, 37),
             box(516, 644, "暂时退出", 141, 44),
             box(884, 644, "继续匹配", 144, 43)]

    def test_result_handler_clicks_continue_match(self):
        task = FakeTask(list(self.TEXTS))
        self.assertTrue(task.handle_match_result(task._texts))
        self.assertEqual(task.actions[-1][1], "继续匹配")

    def test_event_handler_does_not_quit_activity(self):
        task = FakeTask(list(self.TEXTS))
        self.assertFalse(task.handle_event_choice(task._texts))
        self.assertEqual(task.actions, [])

    def test_quit_button_alone_is_still_excluded(self):
        """只剩「暂时退出」时也不能被当成事件选项点掉。"""
        texts = [box(516, 644, "暂时退出", 141, 44)]
        task = FakeTask(texts)
        self.assertFalse(task.handle_event_choice(texts))
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
        self.assertTrue(tick_boost(task, list(self.CARDS)))
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
        self.assertTrue(tick_boost(task, texts))
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


class StuckTakeover(unittest.TestCase):
    """卡住 → **立刻接管**（2026-11-04 用户口径，两条判据都要）。

    ① `点击无响应判定(秒)`（默认 3）：界面指纹超过 3 秒没变、而且这段时间里点过；
    ② `单日最大点击数`（默认 100）：同一天点击超过 100 下、日期还没更新。
    两条都走 `flow.stuck_takeover`：存证 + 清当天拦路标记 + 点屏幕中央继续，
    同一天接管 3 次才停。**没有 2000 步的整局上限了**（用户明确去掉）。
    """

    # 校门口（休息日首页）：顶栏日期 + 右下「出行」
    GATE = [box(40, 12, "第3周", 120, 40),
            box(190, 12, "星期六", 130, 40),
            box(1600, 950, "出行", 160, 60)]
    # 休息室（训练日主界面）：顶栏日期 + 右下「训练 / 休息」
    LOUNGE = [box(40, 12, "第2周", 120, 40),
              box(190, 12, "星期二", 130, 40),
              box(1450, 950, "训练", 160, 60),
              box(1650, 950, "休息", 160, 60)]
    # 没有日期的页面（注册球员 / 比赛）
    NO_DATE = [box(700, 950, "关闭", 160, 60), box(900, 300, "注册球员", 200, 40)]

    def make(self, texts=None, limit=3, noresp=3):
        task = FakeTask(list(self.LOUNGE if texts is None else texts))
        task.config["单日最大点击数"] = limit
        task.config["点击无响应判定(秒)"] = noresp
        task.dump_debug = lambda *a, **k: None      # 不在测试里写真图
        return task

    def day_key(self, task, texts=None):
        """按指定页面更新日期键（清掉「同一帧只读一次」的缓存，模拟新的一帧）。"""
        task._day_cache = None
        task._day_retry_cache = None
        return task.day_counter_step(task._texts if texts is None else texts)

    def clicks(self, task, n):
        task._day_clicks = getattr(task, "_day_clicks", 0) + n

    # ---------- 判据②：同一天点击数 ----------

    def test_click_count_resets_on_new_day(self):
        """日期更新以后计数器归零（用户口径）：第二天从头数。"""
        task = self.make()
        self.assertEqual(self.day_key(task), (2, "二"))
        self.clicks(task, 90)
        texts = [box(40, 12, "第2周", 120, 40),
                 box(190, 12, "星期三", 130, 40),
                 box(1450, 950, "训练", 160, 60)]
        self.assertEqual(self.day_key(task, texts), (2, "三"))
        self.assertEqual(task._day_clicks, 0)
        self.assertTrue(any("日期更新" in str(x) for x in task.logs))

    def test_under_click_limit_does_nothing(self):
        task = self.make()
        self.day_key(task)
        self.clicks(task, 3)
        task._sig_changed_at = time.time()          # 刚变过界面，3 秒判据不触发
        self.assertFalse(task.stuck_takeover(task._texts))
        self.assertEqual(task.actions, [])

    def test_over_click_limit_takes_over(self):
        task = self.make()
        self.day_key(task)
        self.clicks(task, 4)                        # 上限 3，「超过」= 第 4 下
        task._sig_changed_at = time.time()
        self.assertTrue(task.stuck_takeover(task._texts))
        self.assertEqual(task.actions[-1], ("click", 960, 540))   # 点中央继续
        self.assertEqual(task._day_fixes, 1)
        self.assertEqual(task._day_clicks, 0)
        self.assertTrue(any("这一天已经点了" in str(x) for x in task.logs))

    def test_click_limit_counts_pages_without_date(self):
        """看不到日期的页面也算点击数（不许留「切个画面就绕过」的漏子）。"""
        task = self.make(self.NO_DATE)
        self.clicks(task, 4)
        task._sig_changed_at = time.time()
        self.assertTrue(task.stuck_takeover(task._texts))
        self.assertTrue(any("看不到日期" in str(x) for x in task.logs))

    # ---------- 判据①：点击后 3 秒无响应 ----------

    def test_no_response_three_seconds_takes_over(self):
        """界面 3 秒没变化 + 这段时间点过 → 立刻接管。"""
        task = self.make()
        self.day_key(task)
        now = time.time()
        task._sig_changed_at = now - 3.5            # 3.5 秒没变
        task._last_click_at = now - 1.0             # 中间点过
        self.assertTrue(task.stuck_takeover(task._texts))
        self.assertTrue(any("秒没变化" in str(x) for x in task.logs))

    def test_no_response_without_click_is_not_stuck(self):
        """只是干等（没点过）不算无响应 —— 等动画不能误判。"""
        task = self.make()
        self.day_key(task)
        now = time.time()
        task._sig_changed_at = now - 10.0
        task._last_click_at = now - 20.0            # 最后一次点击在界面变化之前
        self.assertFalse(task.stuck_takeover(task._texts))
        self.assertEqual(task.actions, [])

    def test_screen_changed_is_not_stuck(self):
        task = self.make()
        self.day_key(task)
        task._sig_changed_at = time.time()          # 刚变过
        task._last_click_at = time.time()
        self.assertFalse(task.stuck_takeover(task._texts))

    # ---------- 共同：3 次接管才停 / 声明表豁免 ----------

    def test_three_takeovers_then_abort(self):
        task = self.make()
        for _ in range(2):
            self.day_key(task)
            self.clicks(task, 4)
            task._sig_changed_at = time.time()
            self.assertTrue(task.stuck_takeover(task._texts))
            self.assertIsNone(getattr(task, "_abort_reason", None))
        self.day_key(task)
        self.clicks(task, 4)
        task._sig_changed_at = time.time()
        self.assertTrue(task.stuck_takeover(task._texts))
        self.assertIn("接管 3 次", task._abort_reason or "")

    def test_declared_long_runner_is_exempt(self):
        """比赛快进 / 注册球员（实测连跑 542 拍）声明了豁免 → 两条判据都不算。"""
        task = self.make()
        task._last_state = "handle_register_player"
        task._sig_changed_at = time.time() - 30
        task._last_click_at = time.time() - 1
        self.clicks(task, 500)
        self.assertFalse(task.stuck_takeover(task._texts))
        self.assertEqual(task.actions, [])
        # 换个普通页（科目页）就必须管
        task._last_state = "handle_subject_select"
        self.assertTrue(task.stuck_takeover(task._texts))

    def test_click_point_uses_box_center(self):
        task = self.make()
        self.assertEqual(task._click_point(box(100, 200, "x", 200, 60), -1), (200, 230))

    def test_clear_day_home_brakes_resets_marks(self):
        task = self.make()
        task._shop_try_key, task._shop_try_count = (2, "二"), 2
        task._no_change = 3
        task._loop_handler, task._loop_count = "handle_x", 9
        task.day_state()["visited"].add("公园")
        task._clear_day_home_brakes()
        self.assertIsNone(task._shop_try_key)
        self.assertEqual(task._shop_try_count, 0)
        self.assertEqual(task._no_change, 0)
        self.assertIsNone(task._loop_handler)
        self.assertEqual(task.day_state()["visited"], set())


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

    def _shop(self, items, money=80, quality="金", week=None):
        """items = [(名字, 价格)]，价格写 "免费" 表示 0 元卡。

        `week` 给了就把「第N周 星期一」写进顶栏（测第 7 周以后不买精力饮料）。
        """
        texts = [box(760, 60, "商店", 160, 40),
                 box(1200, 60, "上新时间", 200, 30)]
        if week is not None:
            texts.append(box(30, 20, f"第{week}周", 120, 40))
            texts.append(box(200, 20, "星期一", 130, 40))
        if money is not None:
            texts.append(box(1100, 20, str(money), 80, 40))
        for i, (name, price) in enumerate(items):
            cx = self.COLS[i % 3]
            texts.append(box(int(1920 * cx) - 75, int(1080 * 0.30) - 21, name, 150, 42))
            texts.append(box(int(1920 * cx) - 35, int(1080 * 0.45) - 18,
                             str(price), 70, 36))
        task = FakeTask(texts, config={"购买策略": "只买彩金紫"})
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

    def test_shop_buys_stamina_drink_before_week7(self):
        """第 7 周之前：精力饮料照常买（这条口径只管第 7 周以后）。"""
        task, texts = self._shop([("精力饮料", 30)], money=120, week=6)
        self.assertTrue(task.handle_shop(texts))
        self.assertIn("买「精力饮料」", "".join(task.logs))

    def test_shop_skips_stamina_drink_from_week7(self):
        """2026-11-05 用户口径：第 7 周以后不买「精力饮料」，关店。"""
        task, texts = self._shop([("精力饮料", 30)], money=120, week=7)
        self.assertTrue(task.handle_shop(texts))
        self.assertIn("没有买得起的商品了", "".join(task.logs))
        self.assertNotIn("买「精力饮料」", "".join(task.logs))

    def test_shop_still_buys_free_drink_from_week7(self):
        """第 7 周以后：免费的精力饮料照样拿（0 元不花零用钱）。"""
        task, texts = self._shop([("器材手册+", 150), ("精力饮料", "免费")],
                                 money=80, week=7)
        self.assertTrue(task.handle_shop(texts))
        self.assertIn("买「精力饮料」", "".join(task.logs))

    def test_shop_drink_filter_survives_missing_week(self):
        """顶栏 OCR 抖一下、周数读不到：不过滤，按老规则正常买（不整拍罢工）。"""
        task, texts = self._shop([("精力饮料", 30)], money=120)
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

    # 同一张户外地图（1549x871 实测版式，见 logs/events/*_handle_weekend.jpg）
    MAP = [box(814, 461, "游戏厅", 148, 57), box(1538, 606, "公园", 102, 51),
           box(258, 748, "餐厅", 104, 60), box(1010, 897, "步行街", 148, 58)]

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

    def _map(self):
        return list(self.MAP) + [box(37, 18, "第3周", 120, 33),
                                 box(163, 18, "星期五", 130, 35)]

    def test_goes_out_before_training_once(self):
        """周五先外出：点「出行」→ **真的看到地图**才算「今天出过行」。

        2026-11-03 用户：「星期5没进出行地图」。实跑日志（logs/clicks.jsonl）：
            14:48:50 left_click 出行 (1373,465)   ← 点了，页面没翻
            14:48:51 left_click 训练 (1377,750)   ← 下一拍直接去训练
        旧写法点完就记 `outing_done`，于是 `handle_go_out` 对周五（非周末）
        整天都直接返回 —— 那天的额外周末白丢。现在只在进地图那一拍记账。
        """
        task = FakeTask(self._texts())
        task.ensure_speed = lambda texts: False
        task._hub_ticks = task.HUB_SETTLE_TICKS        # 整页已淡入
        self.assertTrue(task.handle_training_day(task._texts))
        self.assertEqual(task.actions[-1][1], "出行")
        # 点完**不算**用过：还没进地图（否则点空一次就锁死这一天）
        self.assertFalse(task.weekend_state().get("outing_done"))
        # 进了地图（handle_weekend 认到地图）→ 才记账
        self.assertTrue(task.handle_weekend(self._map()))
        self.assertTrue(task.weekend_state().get("outing_done"))

    def test_first_tick_does_not_click_go_out(self):
        """整页刚出现的第一拍不点「出行」（躲淡入，与训练/休息的决策拍同理）。"""
        task = FakeTask(self._texts())
        task.ensure_speed = lambda texts: False
        self.assertTrue(task.handle_training_day(task._texts))   # hub_seen = 0
        self.assertEqual(task.actions, [])
        # 第二拍按钮已经画稳 → 正常外出
        self.assertTrue(task.handle_training_day(task._texts))
        self.assertEqual(task.actions[-1][1], "出行")

    def test_go_out_click_is_retried_then_given_up(self):
        """点了「出行」页面没翻 → 下一拍重试；点够上限才放弃（放弃时记账+告警）。"""
        task = FakeTask(self._texts())
        task.ensure_speed = lambda texts: False
        for _ in range(task.OUTING_CLICK_LIMIT + 3):
            task.handle_training_day(task._texts)
        clicks = [a for a in task.actions if a[0] == "click_box" and a[1] == "出行"]
        self.assertEqual(len(clicks), task.OUTING_CLICK_LIMIT)
        # 放弃之后不能死盯这一页：记账（这一天按「出过行」处理）+ 告警 + 回去训练/休息
        self.assertTrue(task.weekend_state().get("outing_done"))
        self.assertTrue(any("今天不再试" in m for m in task.logs), task.logs)

    def test_outing_used_home_page_does_not_click_again(self):
        """出过行以后停回「有出行」的首页：直接返回，不点第二次（防打转）。"""
        task = FakeTask(self._texts())
        task.weekend_state()                            # 先固定 date
        task.mark_outing_used()
        self.assertTrue(task.handle_go_out(task._texts))
        self.assertFalse([a for a in task.actions
                          if a[0] == "click_box" and a[1] == "出行"])
        self.assertEqual(task.actions[-1][0], "click")   # exit_page 的返回箭头

    def _dialog_texts(self):
        """同一帧里只有对话框台词、右侧按钮还没出来（2026-10-31 实跑那一拍）。"""
        return ([t for t in self._texts() if t.name != "出行"]
                + [box(945, 168, "今天可以出行哦！要不", 330, 40)])

    def test_dialogue_line_is_not_the_button(self):
        """台词里有「出行」不算入口，**也不点它**，更不烧掉当天唯一的外出机会。

        2026-10-31 实跑日志：`left_click 今天可以出行哦！要不 (950, 182)` ——
        旧版 click_text("出行") 是包含匹配，把台词当按钮点了，而 outing_done
        已经置 True，下一拍就掉进 else 点「训练」。用户：「为什么没有点出行，
        反而默认进了训练」。

        2026-11-02 用户再反馈：按钮淡入前，每局都会在队长的对话框里空点一下。
        所以这一步改成**只等不点**，下一拍按钮自己出来。
        """
        texts = self._dialog_texts()
        task = FakeTask(texts)
        task.ensure_speed = lambda texts: False
        self.assertTrue(task.handle_training_day(texts))
        self.assertEqual(task.actions, [])          # 台词不是按钮，一个都不点
        self.assertFalse(task.weekend_state().get("outing_done"))
        # 下一拍按钮出来了 → 正常外出
        self.assertTrue(task.handle_training_day(self._texts()))
        self.assertEqual(task.actions[-1][1], "出行")

    def test_dialogue_clicked_only_if_button_never_shows(self):
        """等了两拍还没有按钮 → 才退回「点台词推进」（老行为，不能卡死）。"""
        texts = self._dialog_texts()
        task = FakeTask(texts)
        task.ensure_speed = lambda texts: False
        for _ in range(task.GO_OUT_WAIT_TICKS + 1):
            self.assertTrue(task.handle_training_day(texts))
        self.assertEqual(task.actions[-1][1], "今天可以出行哦！要不")
        self.assertFalse(task.weekend_state().get("outing_done"))

    def test_split_button_still_found(self):
        """OCR 把按钮拆成「出」「行」两个单字框时也要认出来（商店 商/店 同款坑）。"""
        texts = ([t for t in self._texts() if t.name != "出行"]
                 + [box(1380, 750, "出", 52, 40), box(1440, 750, "行", 52, 40)])
        task = FakeTask(texts)
        task.ensure_speed = lambda texts: False
        task._hub_ticks = task.HUB_SETTLE_TICKS      # 头一拍不点（躲淡入），先跳过
        self.assertTrue(task.handle_training_day(texts))
        self.assertEqual(task.actions[-1][1], "出行")

    def test_short_button_with_glyph_still_found(self):
        """按钮上带箭头（OCR 读成「出行▶」）也算按钮；长台词仍然不算。"""
        texts = ([t for t in self._texts() if t.name != "出行"]
                 + [box(1378, 748, "出行▶", 110, 40)])
        task = FakeTask(texts)
        self.assertEqual(task.find_go_out_button(texts).name, "出行▶")
        # 同一帧再加一句台词，长度差就是判据：台词永远不是按钮
        long_line = box(945, 168, "今天可以出行哦！要不", 330, 40)
        self.assertIsNone(task.find_go_out_button([long_line]))


class GachaMateBadge(unittest.TestCase):
    """金卡的橙黄卡头不能当成队友角标（2026-10-31 20:35 实跑）。

    用户现场：三张金卡只有右边那张有队友角标，日志却是
    `随机加成(金+队友/100币/320)、意识升级包++(金+队友/100币/321)、运控升级包++(金+队友/100币/321)`
    —— 每张金卡都被加了 20 分队友分，最后按顺序选了中间那张。

    2026-11-07 重写夹具（用户：「识别区域有问题」——复现证实旧判据在徽章
    完全可见的帧上也返回 False，1408 帧回归见 `tools/check_mate_badge.py`）：
    - 徽章胶囊色值按**实测**重定：BGR≈(137,91,74)（b-r≈63、S≈117），
      旧夹具的 (82,60,55) 是 10-30 的估算值（b-r=27），新判据按实测色定阈值；
    - 旧版橙色「!」气泡兜底分支**已删除**——它就是 10-31 金卡全误报的根源，
      且 1408 帧回归证明当前版本徽章一律是深蓝胶囊，橙色分支从未正确命中过。
    """

    W, H = 1549, 871

    def _frame(self):
        return np.zeros((self.H, self.W, 3), dtype=np.uint8)

    def _gold_card(self, frame, cx):
        x1, x2 = int(self.W * (cx - 0.17)), int(self.W * (cx + 0.17))
        frame[int(self.H * 0.30):int(self.H * 0.78), x1:x2] = (35, 160, 245)

    def _gold_ornament(self, frame, cx):
        """卡头装饰圆/高光：橙黄一块，形状和大小都像角标，落在 ROI 内。"""
        x = int(self.W * (cx - 0.09))
        frame[int(self.H * 0.305):int(self.H * 0.362), x:x + int(self.W * 0.055)] = (40, 170, 250)

    def _navy_chip(self, frame, cx):
        """当前版本的队友角标：深蓝胶囊 + 头像（色值 2026-11-07 实测帧采样）。"""
        x = int(self.W * (cx - 0.10))
        frame[int(self.H * 0.265):int(self.H * 0.325), x:x + int(self.W * 0.060)] = (137, 91, 74)

    def _old_orange_bubble(self, frame, cx):
        """旧版橙色「!」气泡：浮在卡片上沿之上（y < 0.30h）。"""
        x = int(self.W * (cx - 0.09))
        frame[int(self.H * 0.235):int(self.H * 0.275), x:x + int(self.W * 0.045)] = (40, 170, 250)

    def test_gold_card_head_is_not_mate(self):
        frame = self._frame()
        self._gold_card(frame, 0.50)
        self._gold_ornament(frame, 0.50)
        self.assertFalse(DryRunTask(frame, []).has_mate_badge(0.50))

    def test_navy_chip_is_mate(self):
        frame = self._frame()
        self._gold_card(frame, 0.74)
        self._gold_ornament(frame, 0.74)
        self._navy_chip(frame, 0.74)
        self.assertTrue(DryRunTask(frame, []).has_mate_badge(0.74))

    def test_old_orange_bubble_no_longer_counts(self):
        """橙色气泡兜底已删（10-31 金卡全误报的根源）：单独一块橙色不再是队友。"""
        frame = self._frame()
        self._old_orange_bubble(frame, 0.26)
        self.assertFalse(DryRunTask(frame, []).has_mate_badge(0.26))


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


class WeekendHomeDialogNotChoice(unittest.TestCase):
    """2026-11-02 实跑（第3周 星期六 校门口）：队长气泡被当成「整句选项」。

    真帧 OCR（1549x871，日志 01:25:22）：
        和名雪同学约好了在游戏厅见 / 面，快迟到了一    ← 同一个气泡的两行
        北空之丘女子高校                                ← 学校名
        今天餐厅缺人，快去看看吧！                      ← 另一条提示
        出行                                            ← 主界面按钮
    气泡是**居中**文本，左边缘和宽度都不齐 → 骗过「非段落式整句」判据，
    于是 `left_click 面，快迟到了一 (1014, 245)`，在对话里空点一下。
    """

    def _texts(self):
        return [box(804, 236, "和名雪同学约好了在游戏厅见", 296, 27),
                box(949, 231, "面，快迟到了一", 130, 28),
                box(847, 254, "北空之丘女子高校", 138, 24),
                box(850, 300, "今天餐厅缺人，快去看看吧！", 300, 30),
                box(1379, 749, "出行", 138, 83)]

    def test_dialog_bubble_is_not_options(self):
        task = FakeTask(self._texts())
        self.assertEqual(task.options_not_paragraph(task._texts), [])

    def test_no_click_in_the_bubble(self):
        task = FakeTask(self._texts())
        self.assertFalse(task.handle_event_choice(task._texts))
        self.assertEqual(task.actions, [])


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

    # ---------- 2026-11-02：默认选「队友多」的那张（数头像，不数气泡） ----------

    # 实拍取色（logs/debug/1102-234130_stock03.jpg），BGR：
    # 皮肤 (163,192,236) → r-g=44；张开的嘴 (160,172,238) → r-g=66。差 22。
    SKIN_BGR = (163, 192, 236)
    MOUTH_BGR = (160, 172, 238)

    def _stock_frame(self, mates, card_xs=(490, 875, 1260), mouth_y=425):
        """画一张炒股选股页的帧：第 i 张卡头顶画 `mates[i]` 个队友头像。

        只画「脸 + 嘴」两块，因为判据就是靠这两块分人的：脸 48x48（又高又方，
        宽高比 1.0）、嘴 18x8（横窄条，宽高比 2.25 且更红）。
        头像一排的**右端固定在卡心 +175**，往左每 75 一个 —— 实拍是 +160 / 间距 67
        （1549 宽），按 1920 折算过来的。
        """
        frame = np.full((1080, 1920, 3), 210, np.uint8)
        for cx, n in zip(card_xs, mates):
            for dx in sorted(175 - 75 * i for i in range(n)):
                mx = int(cx + dx)
                frame[mouth_y - 62:mouth_y - 14, mx - 24:mx + 24] = self.SKIN_BGR  # 脸
                frame[mouth_y:mouth_y + 8, mx - 9:mx + 9] = self.MOUTH_BGR         # 嘴
        return frame

    def test_stock_teammate_counts_count_mouths_not_faces(self):
        """数的是头像的嘴：脸/头发那两块不能算成人头。"""
        cards = [box(430, 495, "风险型", 120, 50),
                 box(815, 495, "风险型", 120, 50),
                 box(1200, 495, "稳健型", 120, 50)]
        task = FakeTask(cards)
        task._executor.frame = self._stock_frame([1, 0, 2])
        self.assertEqual(task.stock_teammate_counts(cards), [1, 0, 2])

    def test_stock_page_prefers_card_with_more_teammates(self):
        """用户 2026-11-02：左边 1 个队友、右边 2 个 → 选右边（哪怕它更便宜）。

        实拍那张：中间 200 买不起（零用钱 90），能买的只有左（1 人/40）和右（2 人/12）。
        旧规则「气泡数 → 价格取高」会挑左边（40 > 12）；新规则按队友数挑右边。
        """
        texts = [box(700, 130, "请选择你要投资的股票", 500, 50),
                 box(1150, 90, "90", 80, 40),
                 box(430, 495, "风险型", 120, 50),
                 box(815, 495, "风险型", 120, 50),
                 box(1200, 495, "稳健型", 120, 50),
                 box(480, 760, "40", 60, 40),
                 box(870, 760, "200", 60, 40),
                 box(1255, 760, "12", 60, 40),
                 box(1030, 960, "确定", 280, 60)]
        task = FakeTask(texts)
        task.read_money = lambda texts=None: 90
        task.dump_stock_frame = lambda texts: None       # 测试不许往 logs/debug 写图
        task._executor.frame = self._stock_frame([1, 0, 2])
        self.assertTrue(task.handle_stock_pick(texts))
        self.assertEqual(task.actions[0][0], "click")
        self.assertGreater(task.actions[0][1], int(task.width * 0.6))   # 最右那张
        self.assertIn("队友2", "".join(task.logs))

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

    def test_skill_quality_beats_item_badge(self):
        """2026-11-03：品质是第一档，道具图标只做同品质比较。

        旧版 `score = gem*100 + quality*10 + has_pref`：白卡带道具 = 110 > 彩卡 = 30。
        道具判据只是「左上角金橙像素占比 > 0.03」的猜测，2026-10-29 就误判过
        （压过两张真金边卡），和扭蛋「恢复量压掉有队友的卡」是同一类错误。
        """
        from src.tasks.ui import Defs
        texts = [box(600, 200, "请选择1个技能", 400, 50),
                 box(200, 420, "好斗者", 200, 60),
                 box(700, 420, "远投擅长", 200, 60),
                 box(1200, 420, "看破擅长", 200, 60)]
        task = FakeTask(texts)
        task._card_quality = lambda frame, i: 3 if i == 0 else 1   # 左彩，其余白
        task.skill_card_item_gem = lambda cx, cy: cx > 0.4          # 中/右有道具
        self.assertTrue(task.handle_skill_select(texts))
        self.assertAlmostEqual(task.actions[0][1] / task.width_of_screen(1),
                               Defs.SKILL_CARDS[0][0], places=2)    # 仍选彩色那张

    def test_skill_select_ignores_flyout_frame(self):
        """点完卡后的「退场帧」不能再记一个技能。

        2026-10-31 用户核对：监控页「分数预测 7240」vs 注册球员页「7376」，
        白技能记成 6 个（注册球员页真值 2 个），金 4 个是对的。
        实拍 logs/events/1031-162657 / _162658_handle_skill_select.jpg：
        第 1 张被点走后飞出、剩两张左移变暗，但「请选择1个技能」还在 →
        handler 又认领一拍，空槽位读成「白 0.000」、变暗的两张也判白，
        于是白送一个「技能+1（白）」。一局 5 次三选一 = 虚增 5 个白（+125 分）。
        """
        texts = [box(775, 51, "请选择1个技能", 270, 40),
                 box(865, 450, "快速盯防", 145, 32),      # 名字行只剩中间/右边两个槽位
                 box(1483, 450, "后期加持", 143, 32),
                 box(493, 934, "得分手", 84, 34),          # 底部信息栏：不是技能名
                 box(497, 985, "控卫", 64, 35),
                 box(1264, 978, "确定", 82, 47)]
        task = FakeTask(texts)
        task._card_quality = lambda frame, i: 1
        task.skill_card_item_gem = lambda cx, cy: False
        self.assertTrue(task.handle_skill_select(texts))
        self.assertEqual(task.actions, [])                 # 不点卡、不点确定
        self.assertNotIn("skill_gained", task.info)        # 不计数
        self.assertIn("退场帧", "".join(task.logs))

    def test_quick_match_has_is_label(self):
        """2026-10-29 实机：QuickMatchTask 报 AttributeError: 'is_label'。

        match.py 的选卡判据调用 self.is_label，但 QuickMatch 不继承 TrainingFlowMixin，
        所以这个方法必须在基类上。
        """
        from src.tasks.QuickMatchTask import QuickMatchTask
        self.assertTrue(hasattr(QuickMatchTask, "is_label"))
        self.assertTrue(QuickMatchTask.is_label("一定能赚!"))
        self.assertFalse(QuickMatchTask.is_label("防守"))

    def test_reset_run_state_clears_register_stage(self):
        """连开第二把秒退：_register_stage 停在上次 "back" 必须先清掉。"""
        task = FakeTask([])
        task._register_stage = "back"
        task._shop_day = (3, "一")
        task._no_change = 5
        task._day_key, task._day_clicks = (2, "二"), 90
        task.reset_run_state()
        self.assertIsNone(task._register_stage)
        self.assertIsNone(task._shop_day)
        self.assertEqual(task._no_change, 0)
        # 上一局攒到 90 多次的点击计数不能带进新的一局（否则开局就误判卡住）
        self.assertIsNone(task._day_key)
        self.assertEqual(task._day_clicks, 0)

    def test_unknown_effect_falls_back_to_first_option(self):
        """收益读不出来但**另一个是纯回精力** → 必须选这个未知的（2026-10-30 口径）。

        实机：`以逸待劳(2·回精力)、紧张准备(?)` —— 旧规则「未知→选第一个」又选回了回精力。
        """
        infos = [{"name": "以逸待劳", "ev": 2, "flat": True, "x": 100, "y": 300},
                 {"name": "紧张准备", "ev": None, "flat": False, "x": 800, "y": 300}]
        pick, why = FakeTask._pick_choice(infos, "h")
        self.assertEqual(pick["name"], "紧张准备")
        self.assertIn("未知", why)

    def test_all_flat_takes_biggest(self):
        """两个都是回精力时退回到「取数值最高的」。"""
        infos = [{"name": "休息", "ev": 2, "flat": True, "x": 100, "y": 300},
                 {"name": "睡觉", "ev": 3, "flat": True, "x": 800, "y": 300}]
        pick, _why = FakeTask._pick_choice(infos, "h")
        self.assertEqual(pick["name"], "睡觉")

    def test_short_pair_without_skip_is_a_choice(self):
        """器材事件：两行短选项、没有「跳过」也要认，选上面那个。

        2026-10-29 实机：`精力恢复 / 资金援助` 被放弃 → 兜底点中央正好压中
        下面那个（用户看到「选了下面那个」）。
        """
        texts = [box(69, 165, "器材事件", 115, 34),
                 box(70, 206, "器材加成-70熟练度", 231, 36),
                 box(686, 322, "精力恢复", 176, 54),
                 box(684, 434, "资金援助", 179, 56)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_event_choice(texts))
        self.assertEqual(task.actions[-1][1], "精力恢复")

    def test_note_select_auto_then_next(self):
        """「选择笔记」：先自动选择，再点下一步（2026-10-30 口径，旧口径是不选直接过）。"""
        texts = [box(150, 80, "选择笔记", 300, 50),
                 box(560, 780, "重置", 160, 50),
                 box(700, 780, "自动选择", 200, 50),
                 box(1150, 900, "下一步", 300, 60)]
        task = FakeTask(texts)
        self.assertTrue(task.handle_note_select(texts))
        self.assertEqual(task.actions[0][1], "自动选择")
        # 下一步走 click_and_expect（x, y 在前），坐标 = 按钮中心
        self.assertEqual(task.actions[-1][:2], ("click_and_expect", 1300))
        self.assertIn("选择笔记：自动选择", "".join(task.logs))

    def test_note_select_ignores_confirm_team_page(self):
        """确认编队页有「未选择笔记」，不能被当成「选择笔记」页（2026-10-29 实机）。"""
        texts = [box(71, 72, "确认编队", 136, 43),
                 box(1295, 157, "训练笔记", 132, 30),
                 box(1171, 213, "未选择笔记", 134, 32),
                 box(1113, 251, "请携带笔记一起训练吧", 254, 28)]
        task = FakeTask(texts)
        self.assertFalse(task.handle_note_select(texts))
        self.assertEqual(task.actions, [])

    def test_pick_choice_generic_for_three_options(self):
        """二选一/三选一共用一套：按期望取最高，纯回精力仍被排除。"""
        infos = [{"name": "甲", "ev": 12, "flat": False, "x": 200, "y": 400},
                 {"name": "乙", "ev": 30, "flat": False, "x": 600, "y": 400},
                 {"name": "丙", "ev": 40, "flat": True, "x": 1000, "y": 400}]
        pick, why = FakeTask._pick_choice(infos, "h")
        self.assertEqual(pick["name"], "乙")

    def test_boost_three_cards_takes_best_ev(self):
        """加强训练从 2 张变 3 张：按列聚类算期望，取最高那列。"""
        texts = [box(600, 200, "加强训练", 400, 50),
                 box(200, 380, "100%", 120, 60), box(200, 520, "+12", 120, 60),
                 box(900, 380, "80%", 120, 60), box(900, 520, "+36", 120, 60),
                 box(1500, 380, "55%", 120, 60), box(1500, 520, "+66", 120, 60),
                 box(1200, 900, "确定", 200, 60)]
        task = FakeTask(texts)
        self.assertTrue(tick_boost(task, texts))
        self.assertIn("选 55%/+66", "".join(task.logs))

    def test_boost_text_cards_skip_stamina_card(self):
        """文字效果版加强训练：左边纯「精力提升+30」→ 选右边的等级提升。"""
        texts = [box(615, 263, "球队经理的加强训练", 314, 36),
                 box(250, 319, "失败", 65, 41),
                 box(200, 560, "精力提升+30", 200, 40),
                 box(1100, 460, "训练等级提升", 220, 45),
                 box(1100, 560, "意识训练的等级增加至2", 260, 40),
                 box(1200, 900, "确定", 200, 60)]
        task = FakeTask(texts)
        self.assertTrue(tick_boost(task, texts))
        self.assertIn("加强训练（文字效果）", "".join(task.logs))
        self.assertGreater(task.actions[0][1], int(task.width * 0.5))   # 点了右边

    def test_effect_value_shared_rule(self):
        """统一口径：纯回精力=flat；等级/属性按 100；属性 +N 按真实总量。

        2026-11-03：精力不再折成属性分（`精力提升+30` 的 ev 是 0，不是 6）。
        """
        def val(*names):
            return FakeTask.effect_value([box(0, 0, n, 100, 40) for n in names])

        self.assertEqual(val("精力提升+30"), (0.0, True))
        self.assertEqual(val("意识训练的等级增加至2"), (100.0, False))
        self.assertEqual(val("投射+10"), (10.0, False))
        # 同一张卡既给等级又回精力：不算纯回精力
        ev, flat = val("意识训练的等级增加至2", "精力提升+30")
        self.assertFalse(flat)
        self.assertEqual(ev, 100.0)

    def test_no_separate_stock_button_on_training_day(self):
        """炒股**不是**训练日主按钮：入口只有「二选一」选第一个。

        用户 2026-10-29 纠正：主界面没有「炒股」按钮，别在训练日乱点。
        """
        texts = [box(30, 20, "第2周", 120, 40), box(200, 20, "星期四", 130, 40),
                 box(380, 16, "92/130", 210, 53),
                 box(1450, 900, "训练", 120, 60), box(1600, 900, "休息", 120, 60)]
        task = FakeTask(texts)
        task.ensure_speed = lambda *a, **k: False
        task._hub_ticks = task.HUB_SETTLE_TICKS     # 模拟「出行」按钮已淡入
        self.assertTrue(task.handle_training_day(texts))
        self.assertEqual(task.actions[-1][1], "训练")


class CaptainNameMatch(unittest.TestCase):
    """队长名字匹配（2026-10-31 用户反馈「列表的亚理纱和游戏的亚理纱没匹配上」）。

    实测差异来自 logs/mcp/sento_summary.txt：
        「队长卡 1（第1屏）：SR-亚理纱贾斯敏」
    而角色表 / 监控页列表里写的是「亚理纱·贾斯敏」：
    间隔点被 OCR 吃掉、底部名字行前面还带稀有度（SR-）。
    旧判据 `want in name` 直接比字符串，于是 16 张卡全被当成「不是她」。
    """

    def test_middle_dot_and_rarity_prefix_ignored(self):
        from src.tasks.ui.labels import name_matches
        self.assertTrue(name_matches("亚理纱·贾斯敏", "SR-亚理纱贾斯敏"))
        self.assertTrue(name_matches("亚理纱·贾斯敏", "亚理纱贾斯敏"))
        self.assertTrue(name_matches("亚理纱", "SSR 亚理纱·贾斯敏"))
        self.assertTrue(name_matches("克拉丽丝·波洛", "克拉丽丝波洛"))

    def test_different_people_do_not_match(self):
        from src.tasks.ui.labels import name_matches
        self.assertFalse(name_matches("近江奏", "SR-亚理纱贾斯敏"))
        self.assertFalse(name_matches("", "亚理纱"))
        self.assertFalse(name_matches("亚理纱", ""))

    def test_style_lookup_accepts_game_spelling(self):
        task = FakeTask([])
        self.assertEqual(task.style_for_name("亚理纱贾斯敏"), "训练流")
        self.assertEqual(task.style_for_name("SR-亚理纱贾斯敏"), "训练流")
        self.assertEqual(task.style_for_name("亚理纱·贾斯敏"), "训练流")
        self.assertEqual(task.style_for_name("克拉丽丝波洛"), "训练流")
        self.assertEqual(task.style_for_name("毒岛黛也"), "小游戏流")

    def test_pick_captain_matches_without_middle_dot(self):
        """选队长页：配置「亚理纱·贾斯敏」，游戏卡上读到「SR-亚理纱贾斯敏」也要选中。"""
        task = FakeTask([box(30, 20, "第1周", 120, 40),
                         box(200, 20, "星期六", 130, 40),
                         box(380, 16, "130/130", 210, 53),
                         box(1480, 900, "下一步", 180, 60)])
        task.config = {"指定队长": "亚理纱·贾斯敏"}
        task.read_captain_name = lambda: "SR-亚理纱贾斯敏"
        task._captain_scroll_to_top = lambda *a, **k: None
        task._captain_scroll_page = lambda *a, **k: None
        self.assertTrue(task.pick_captain_by_name("亚理纱·贾斯敏"))
        self.assertTrue(any("选定队长「SR-亚理纱贾斯敏」" in m for m in task.logs))
        # 选中后要顺着点「下一步」（回归桩里 click_text_and_expect 只记录坐标）
        self.assertTrue(any(act[0] == "click_and_expect" for act in task.actions))


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


class MonitorTimeline(unittest.TestCase):
    """监控页「最近动作」详情列 = 本拍识别到的文字（2026-11-01 用户口径）。

    旧版详情列写「第 N 步 · handler 名」，只能看出流程走到哪一步，看不出
    当时屏幕上是什么；步数/全名改成另外两个键存着，不占列。
    """

    def test_detail_is_recognized_text(self):
        task = FakeTask([box(30, 20, "第1周", 120, 40),
                         box(200, 20, "星期六", 130, 40)])
        task._steps = 7
        task._ui_event(task.handle_story_arrow, task._texts)
        ev = task.info["timeline"][0]
        self.assertEqual(ev["detail"], "第1周 / 星期六")
        self.assertEqual(ev["step"], 7)
        self.assertEqual(ev["handler"], "story_arrow")

    def test_summary_texts_dedupes_and_skips_blank(self):
        from src.tasks.training.flow import summary_texts
        texts = [box(0, 0, "训练"), box(0, 0, "训练"), box(0, 0, "   "),
                 box(0, 0, "第 2 周")]
        self.assertEqual(summary_texts(texts), "训练 / 第 2 周")
        self.assertEqual(summary_texts([]), "")

    def test_summary_texts_is_bounded(self):
        """一帧几十条文字也不能把一行撑爆：条数和总长都要限。"""
        from src.tasks.training.flow import summary_texts
        texts = [box(0, 0, f"文字{i}") for i in range(30)]
        out = summary_texts(texts)
        self.assertLessEqual(len(out), 50)
        self.assertEqual(len(out.split(" / ")), 8)


class StyleResolution(unittest.TestCase):
    """流派来源：配置里的「指定队长」说了算（用户 2026-11-01 口径）。

    事故（2026-11-01 19:19 实跑）：扭蛋流跑成了训练流。
    根因：旧逻辑只在 `is_lobby()` 时才套用配置队长，而**接手的局不是大厅**，
    于是 `_style` 一直是空的 → `style` 兜底成「训练流」；
    而日历只在训练日弹，周末（非训练日）永远读不到实际队长。

    修法（2026-11-01 用户口径，**两条路径都不许保底**）：
      A. 配置里有队长 → 查表定流派；查不到 → **弹窗「未能识别到队长」**。
      B. 配置为空（快速接手）→ 点返回回到「带日历的页面」→ 点日历 →
         抓队长名字 → 查表定流派；认不到 → **弹窗**。
    """

    def _task(self, config):
        task = FakeTask([])
        task.config = dict(config)
        task.is_lobby = lambda *a, **k: False     # 接手别人的局
        return task

    def test_configured_captain_wins_even_when_not_lobby(self):
        """路径 A：配置里有队长 → 查表定流派（不管是不是大厅开局）。"""
        task = self._task({"指定队长": "野上泷"})
        self.assertTrue(task.resolve_style_by_config())
        self.assertEqual(task.style, "扭蛋流")
        self.assertTrue(task.style_known)

    def test_configured_train_captain(self):
        task = self._task({"指定队长": "真崎辉世"})
        self.assertTrue(task.resolve_style_by_config())
        self.assertEqual(task.style, "训练流")
        self.assertTrue(task.style_known)

    def test_no_captain_waits_for_calendar(self):
        """路径 B 的入口：配置为空 → 不定流派，走「点日历识别」那条路。

        **不再保底「训练流」** —— 那是 19:19 事故的根因（扭蛋流跑成训练流）。
        """
        task = self._task({"指定队长": ""})
        self.assertFalse(task.resolve_style_by_config())
        self.assertFalse(task.style_known)

    def test_unknown_captain_falls_back_to_calendar(self):
        """配置的名字不在角色表 → 不能瞎定流派，走路径 B。"""
        task = self._task({"指定队长": "查无此人"})
        self.assertFalse(task.resolve_style_by_config())
        self.assertFalse(task.style_known)

    def test_calendar_path_resolves_captain(self):
        """路径 B：从日历读到队长名 → 查表定流派。"""
        task = self._task({"指定队长": ""})
        self.assertTrue(task.resolve_style_by_calendar_name("野上泷"))
        self.assertEqual(task.style, "扭蛋流")
        self.assertTrue(task.style_known)

    def test_alert_when_captain_not_resolvable(self):
        """认不出 → **弹窗**，不许保底。"""
        task = self._task({"指定队长": ""})
        calls = []
        task.alert = lambda title, text: calls.append((title, text))
        self.assertFalse(task.captain_alert())
        self.assertEqual(calls[0][0], "认不出队长")
        self.assertEqual(calls[0][1], "未能识别到队长")

    # ---------- 路径 B：接手 → 点返回 → 点日历 → 抓名字 ----------

    GATE = [box(30, 20, "第1周", 120, 40), box(200, 20, "星期五", 130, 40),
            box(380, 16, "130/130", 210, 53),
            box(721, 54, "北空之丘女子高校", 200, 40),
            box(950, 560, "商店", 130, 50), box(1150, 700, "出行", 160, 70)]
    MAP = [box(814, 461, "游戏厅", 148, 57), box(1538, 606, "公园", 102, 51),
           box(258, 748, "餐厅", 104, 60), box(1010, 897, "步行街", 148, 58)]

    def test_path_b_returns_until_calendar_page(self):
        """路径 B 第一步：**点返回直到回到带日历的页面**。

        「带日历的页面」= 训练室/校门口（有日期、不是地点内页/地图）。
        判据不能靠「有休息」—— 校门口没有「休息」，
        那样接手的局在周末永远点不了日历（2026-11-01 实测）。
        """
        task = self._task({"指定队长": ""})
        task._style_pending = True
        # 周末地图：有地点名 → 还不是「带日历的页面」→ 该点返回
        self.assertFalse(task._looks_like_calendar_host(self.MAP))
        # 校门口：有日期、没有地点名 → 就是它
        self.assertTrue(task._looks_like_calendar_host(self.GATE))
        # 日历页本身不该再点日历（再点就把它关了）
        plan = [box(173, 60, "先斗", 184, 117),
                box(334, 91, "的训练计划", 300, 74),
                box(182, 171, "星期", 66, 36),
                box(361, 164, "星期二", 93, 45),
                box(200, 400, "弹跳训练", 120, 50),
                box(400, 400, "意识训练", 120, 50)]
        self.assertFalse(task._looks_like_calendar_host(plan))

    def test_path_b_full_flow(self):
        """路径 B 全流程：地图 → 返回 → 校门口 → 点日历 → 抓名字 → 定流派。

        用**真实日历帧的 OCR 坐标**（logs/events/1101-122630_handle_training_plan.jpg：
        「先斗」(173,60,184,117) + 「的训练计划」(334,91,300,74)）。
        """
        task = self._task({"指定队长": ""})
        task._style_pending = True
        plan = [box(173, 60, "先斗", 184, 117),
                box(334, 91, "的训练计划", 300, 74),
                box(182, 171, "星期", 66, 36),
                box(361, 164, "星期二", 93, 45),
                box(547, 171, "星期三", 90, 34),
                box(200, 400, "弹跳训练", 120, 50),
                box(400, 400, "意识训练", 120, 50),
                box(600, 400, "体能训练", 120, 50)]

        # 1) 在周末地图 → 点返回
        task._resolve_style_step(self.MAP)
        self.assertNotEqual(getattr(task, "_style_probe_state", None), "点日历")
        self.assertEqual(task._style_return_tries, 1)

        # 2) 到校门口 → 进「点日历」
        task._resolve_style_step(self.GATE)
        self.assertEqual(task._style_probe_state, "点日历")

        # 3) 点日历 → 进「抓名字」
        task._resolve_style_step(self.GATE)
        self.assertEqual(task._style_probe_state, "抓名字")

        # 4) 日历页 → 读到「先斗」→ 定流派「扭蛋流」
        task._resolve_style_step(plan)
        self.assertTrue(task.style_known)
        self.assertEqual(task.style, "扭蛋流")
        self.assertFalse(task.style_pending())

    def test_path_b_alerts_when_calendar_unreachable(self):
        """点满返回次数还没到带日历的页面 → 弹窗「未能识别到队长」。"""
        task = self._task({"指定队长": ""})
        calls = []
        task.alert = lambda title, text: calls.append((title, text))
        task._style_pending = True
        task._style_return_tries = task.CAPTAIN_RETURN_MAX
        task._resolve_style_step(self.MAP)
        self.assertEqual(calls[0][1], "未能识别到队长")
        self.assertFalse(task.style_pending())


class MonitorCaptainLogic(unittest.TestCase):
    """监控页「指定队长」勾选框 = 队长的唯一来源（用户 2026-11-01 口径）。

    两条坑：
    1. 勾选框**默认是勾上的** → 用户一进页面就等于「指定了一个队长」，
       而下拉第 0 项是「── 训练流 ──」分组标题 → 流派被写成训练流。
    2. 取消勾选必须**清空变量**（config 里的名字 **和** 运行期 `_style`），
       否则旧的流派一直留着（界面显示的流派和实际跑的不一致）。
    """

    def _tab(self, captain=""):
        from src.ui.MonitorTab import MonitorTab

        class _FakeTask:
            def __init__(self):
                self.config = {"指定队长": captain}
                self.enabled = False
                self.running = False
                self.reset_calls = 0

            def reset_style(self):
                self.reset_calls += 1

            def enable(self):
                self.enabled = True

            def disable(self):
                self.enabled = False

            def unpause(self):
                pass

        tab = MonitorTab.__new__(MonitorTab)
        task = _FakeTask()
        tab._task = lambda: task
        tab._update_data = lambda: None
        return tab, task

    def test_uncheck_clears_config_and_style(self):
        tab, task = self._tab("野上泷")
        tab.set_captain("")
        self.assertEqual(task.config["指定队长"], "")
        self.assertEqual(task.reset_calls, 1)

    def test_uncheck_clears_style_even_if_config_already_empty(self):
        """配置本来就是空时也要清 `_style` —— 否则旧流派一直留着。"""
        tab, task = self._tab("")
        tab.set_captain("")
        self.assertEqual(task.reset_calls, 1)

    def test_select_captain_keeps_style(self):
        tab, task = self._tab("")
        tab.set_captain("真崎辉世")
        self.assertEqual(task.config["指定队长"], "真崎辉世")
        self.assertEqual(task.reset_calls, 0)


class MonitorButtonActions(unittest.TestCase):
    """监控页两栏按钮的**动作**（不只是文案）—— 用户 2026-11-01/02 口径。

    这里测的是「点了之后到底调了什么」，因为根因就在调用序列里：
    旧版第二栏点【暂停】调了 `task.unpause()`，它的实体是 `executor.start()`，
    会把整个执行器从暂停里放出来 —— 于是「停自动训练」顺手撤销了
    「断开游戏」，第一栏自己跳回【停止】。
    """

    def _tab(self, connected=True, running=False):
        from src.ui.MonitorTab import MonitorTab

        class _FakeTask:
            def __init__(self):
                self.config = {"指定队长": ""}
                self.enabled = False
                self.running = running
                self.calls = []

            def enable(self):
                self.calls.append("enable")
                self.enabled = True

            def disable(self):
                self.calls.append("disable")
                self.enabled = False
                self.running = False

            def unpause(self):
                # 旧版会调它；新逻辑**不应该**再调
                self.calls.append("unpause")

        class _FakeExecutor:
            def __init__(self):
                self.paused = True
                self.calls = []

            def start(self):
                self.calls.append("executor.start()")
                self.paused = False

            def pause(self):
                self.calls.append("executor.pause()")
                self.paused = True

        class _FakeCapture:
            def __init__(self):
                self.closed = False

            def connected(self):
                return not self.closed

            def close(self):
                self.closed = True

        class _FakeDeviceManager:
            def __init__(self, capture):
                self.capture_method = capture

        tab = MonitorTab.__new__(MonitorTab)
        task = _FakeTask()
        executor = _FakeExecutor()
        capture = _FakeCapture()
        device_manager = _FakeDeviceManager(capture)
        tab._task = lambda: task
        tab.executor = executor
        tab._update_data = lambda: None
        tab._connected = lambda: (device_manager.capture_method is not None
                                  and device_manager.capture_method.connected())
        tab._close_capture_async = lambda: MonitorTab._close_capture_async()
        # 让 _close_capture_async 里的 `og.device_manager` 指到我们的假设备
        import ok
        old = getattr(ok.og, "device_manager", None)
        ok.og.device_manager = device_manager
        self.addCleanup(lambda: setattr(ok.og, "device_manager", old))
        return tab, task, executor, capture

    def test_pause_auto_training_does_not_unpause_task(self):
        """第二栏【暂停】只调 disable —— **不调 unpause**。

        `unpause()` = `executor.start()`：会把执行器从暂停里放出来，
        于是「停自动训练」顺手撤销了「断开游戏」。
        """
        tab, task, executor, _ = self._tab()
        task.enabled = True
        task.running = True
        tab.toggle_auto_training()
        self.assertIn("disable", task.calls)
        self.assertNotIn("unpause", task.calls)
        # 执行器没被碰
        self.assertEqual(executor.calls, [])

    def test_pause_auto_training_does_not_touch_executor(self):
        """第二栏【暂停】不能唤醒执行器（那是第一栏的语义）。"""
        tab, task, executor, _ = self._tab()
        executor.paused = True
        task.enabled = True
        task.running = True
        tab.toggle_auto_training()
        self.assertTrue(executor.paused)
        self.assertEqual(executor.calls, [])

    def test_disconnect_clears_connection_immediately(self):
        """断开后连接态要**立刻**为假（不等后台线程）。

        旧版把 `capture_method = None` 也放在后台线程里，于是断开后的
        几秒内 `_connected()` 仍为真 —— 第一栏还显示【停止】、
        第二栏也以为还能开始。置 None 只是属性赋值，先做掉它。
        """
        tab, task, executor, capture = self._tab()
        self.assertTrue(tab._connected())
        tab.toggle_connection()          # 断开
        self.assertFalse(tab._connected())

    def _fake_app(self, starting=False):
        """给 `og.app.start_controller` 换成一个只记账的假货。"""
        import ok

        calls = []

        class _Controller:
            def __init__(self):
                self.starting = starting

            def start(self, task=None):
                calls.append(("start", task))

        class _App:
            def __init__(self):
                self.start_controller = _Controller()

        app = _App()
        old = getattr(ok.og, "app", None)
        ok.og.app = app
        self.addCleanup(lambda: setattr(ok.og, "app", old))
        app.calls = calls
        return app

    def test_start_auto_training_starts_game_when_not_connected(self):
        """没接上游戏时点第二栏【开始】→ **主动接入游戏**（用户 2026-11-02 口径）。

        旧版这里只打一条 `logger.warning` 就 return，界面上一点变化都没有
        —— 用户看到的就是「点了毫无反应」。现在改走第一栏【启动】的同一条链路：
        `start_controller.start(task)`，它会自己刷新设备、必要时拉起游戏、
        enable 这个任务并唤醒执行器。

        注意这里**不自己 `task.enable()`**：还没连上就 enable，
        任务只会躺在队列里，一帧都不跑。
        """
        tab, task, executor, capture = self._tab(connected=False)
        capture.close()          # 模拟已断开
        app = self._fake_app()
        tab.toggle_auto_training()
        self.assertEqual(len(app.calls), 1)
        self.assertIs(app.calls[0][1], task)
        self.assertNotIn("enable", task.calls)
        self.assertFalse(task.enabled)

    def test_start_auto_training_ignores_repeat_click_while_starting(self):
        """接入进行中再点【开始】不重复发起 —— 否则队列里堆好几个 `do_start`。"""
        tab, task, executor, capture = self._tab(connected=False)
        capture.close()
        app = self._fake_app(starting=True)
        tab.toggle_auto_training()
        tab.toggle_auto_training()
        self.assertEqual(app.calls, [])

    def test_start_auto_training_when_connected_does_not_start_game(self):
        """已经接上了就别再碰 `start_controller`（那是第一栏的职责）。"""
        tab, task, executor, _ = self._tab(connected=True)
        app = self._fake_app()
        tab.toggle_auto_training()
        self.assertEqual(app.calls, [])
        self.assertIn("enable", task.calls)

    def test_start_auto_training_enables_when_connected(self):
        """接上游戏时点第二栏【开始】→ enable + 唤醒执行器。"""
        tab, task, executor, _ = self._tab(connected=True)
        tab.toggle_auto_training()
        self.assertIn("enable", task.calls)
        self.assertTrue(task.enabled)

    def test_disconnect_resets_second_bar_state(self):
        """第一栏【停止】（断开游戏）→ 训练任务被 disable、按钮复位成【开始】。

        用户 2026-11-02 口径：断开连接的链路要收尾干净。
        """
        tab, task, executor, _ = self._tab()
        task.enabled = True
        task.running = True
        tab.toggle_connection()       # 已连接 → 走 _connection_stop
        self.assertIn("disable", task.calls)
        self.assertFalse(task.enabled)
        self.assertFalse(task.running)

    def test_disconnect_second_bar_text_resets_immediately(self):
        """断开后第二栏**立刻**回到【开始】，不等任务线程收尾。

        `disable()` 是同步的（`_enabled` 立刻变 False），但 `running`
        要等任务线程跑完当前拍才置 False —— 有几秒滞后。
        判据必须用 `enabled and running`，否则断开后界面还挂着【暂停】。
        """
        from src.ui.MonitorTab import MonitorTab

        class _Task:
            enabled = False
            running = False

        tab = MonitorTab.__new__(MonitorTab)
        # 模拟「刚 disable、任务线程还没收尾」的中间态
        task = _Task()
        task.enabled = False
        task.running = True
        self.assertFalse(MonitorTab._active_of(task))
        # 真在跑：两个都为真
        task.enabled = True
        task.running = True
        self.assertTrue(MonitorTab._active_of(task))
        # 只排队没跑
        task.enabled = True
        task.running = False
        self.assertFalse(MonitorTab._active_of(task))

    def test_connect_does_not_disable_training(self):
        """第一栏【停止】是唯一会停训练的动作；别的地方不许停它。"""
        tab, task, executor, _ = self._tab()
        task.enabled = True
        task.running = True
        tab.toggle_auto_training()    # 第二栏暂停
        self.assertIn("disable", task.calls)
        self.assertEqual(task.calls.count("disable"), 1)


class MonitorQuickMatchSwitch(unittest.TestCase):
    """「快速比赛」开关只管快速比赛模块（用户 2026-11-02 口径）。

    分工：
    - **执行器**是唯一运行基础，**只有第一栏【启动/停止】能动它**（接入：刷设备 /
      拉游戏 / `executor.start()`；断开：两个开关复位 + `executor.pause()`）；
    - **第二栏【开始/暂停】**只管自动训练；执行器没起来时**顺手**唤醒，不是必须；
    - **快速比赛开关**同理：只管 `QuickMatchTask` 的 enable/disable + 顺手唤醒。
      它**不接入**（不碰 `start_controller`），也**不关执行器**；
    - 两个模块无关：自动训练自己处理养成里的比赛，快速比赛管养成之外的
      全国大赛 / 圣山战 / 竞技场。

    `QuickMatchTask` 是常驻 `TriggerTask`，**只有执行器没被暂停时才会被调度**
    （`TaskExecutor.execute()` 里 `if self.paused: 只跑 job`）—— 所以旧版只
    `enable()` 时开关拨了什么都不动，看着就像「跟第二栏的开始绑在一起」。
    """

    def _tab(self, connected=True):
        from src.ui.MonitorTab import MonitorTab
        import ok

        class _FakeTask:
            def __init__(self):
                self.enabled = False
                self.running = False
                self.calls = []

            def enable(self):
                self.calls.append("enable")
                self.enabled = True

            def disable(self):
                self.calls.append("disable")
                self.enabled = False
                self.running = False

        class _FakeExecutor:
            def __init__(self, paused=True):
                self.paused = paused
                self.calls = []

            def start(self):
                self.calls.append("executor.start()")
                self.paused = False

            def pause(self):
                self.calls.append("executor.pause()")
                self.paused = True

        tab = MonitorTab.__new__(MonitorTab)
        quick, train = _FakeTask(), _FakeTask()
        executor = _FakeExecutor(paused=not connected)
        tab._task = lambda: train
        tab._task_by_name = lambda name: quick if name == "QuickMatchTask" else None
        tab._connected = lambda: not executor.paused
        tab._update_data = lambda: None
        tab._close_capture_async = lambda: None
        tab.executor = executor
        return tab, quick, train, executor

    def _forbid_start_controller(self):
        """快速比赛**不许**接入（那是第一栏【启动】的活）—— 谁调谁炸。"""
        import ok

        class _App:
            @property
            def start_controller(self):
                raise AssertionError("快速比赛开关不该碰 start_controller（接入是【启动】的职责）")

        old = getattr(ok.og, "app", None)
        ok.og.app = _App()
        self.addCleanup(lambda: setattr(ok.og, "app", old))

    def test_switch_on_enables_quick_match_only(self):
        """拨到「开」：只 enable 快速比赛，不碰自动训练。"""
        tab, quick, train, executor = self._tab(connected=True)
        self._forbid_start_controller()
        tab.set_quick_match(True)
        self.assertIn("enable", quick.calls)
        self.assertEqual(train.calls, [])
        self.assertEqual(executor.calls, [])   # 执行器在跑，不用动

    def test_switch_on_wakes_paused_executor_incidentally(self):
        """执行器还停着就**顺手**唤醒一下 —— 只 `executor.start()`，不接入。"""
        tab, quick, train, executor = self._tab(connected=False)
        self._forbid_start_controller()
        tab.set_quick_match(True)
        self.assertIn("enable", quick.calls)
        self.assertEqual(executor.calls, ["executor.start()"])
        self.assertFalse(executor.paused)

    def test_switch_off_only_disables_quick_match(self):
        """拨到「关」：只 disable 快速比赛，不碰自动训练、不动执行器。"""
        tab, quick, train, executor = self._tab(connected=True)
        quick.enabled = True
        tab.set_quick_match(False)
        self.assertIn("disable", quick.calls)
        self.assertFalse(quick.enabled)
        self.assertEqual(train.calls, [])
        self.assertEqual(executor.calls, [])

    def test_switch_does_not_enable_auto_training(self):
        """开关拨「开」不该替用户把自动训练也开起来（两个模块无关）。"""
        tab, quick, train, executor = self._tab(connected=False)
        tab.set_quick_match(True)
        self.assertNotIn("enable", train.calls)
        self.assertFalse(train.enabled)

    def test_disconnect_resets_quick_match_switch(self):
        """执行器被关掉（第一栏【停止】）→ 快速比赛开关复位成「关」。

        「拨关」是用户自己关；「断开」是运行基础没了 —— 两种都要 disable，
        但只有断开时由第一栏**代劳**（见 `MonitorExecutorLifecycle`）。
        """
        tab, quick, train, executor = self._tab(connected=True)
        quick.enabled = True
        tab._connection_stop()
        self.assertIn("disable", quick.calls)
        self.assertFalse(quick.enabled)


class MonitorExecutorLifecycle(unittest.TestCase):
    """执行器（`og.executor`）= 唯一运行基础，只有第一栏能动（用户 2026-11-02 口径）。

    - 【启动】/【停止】= 拉起 / 关掉执行器；**关掉时两个模块开关一起复位**
      （运行基础没了，挂着「开」就是撒谎）；
    - 第二栏【开始/暂停】、快速比赛开关：开的时候**顺手**唤醒执行器
      （`executor.start()`），关的时候**只 disable 自己**，绝不 pause 执行器。
    """

    def _tab(self, paused=True):
        from src.ui.MonitorTab import MonitorTab

        class _FakeTask:
            def __init__(self, name):
                self.name = name
                self.enabled = False
                self.running = False
                self.calls = []

            def enable(self):
                self.calls.append("enable")
                self.enabled = True

            def disable(self):
                self.calls.append("disable")
                self.enabled = False
                self.running = False

        class _FakeExecutor:
            def __init__(self):
                self.paused = paused
                self.calls = []

            def start(self):
                self.calls.append("start")
                self.paused = False

            def pause(self):
                self.calls.append("pause")
                self.paused = True

        tab = MonitorTab.__new__(MonitorTab)
        quick, train = _FakeTask("快速比赛"), _FakeTask("自动训练")
        executor = _FakeExecutor()
        tab._task = lambda: train
        tab._task_by_name = lambda name: quick if name == "QuickMatchTask" else None
        tab._connected = lambda: not executor.paused
        tab._update_data = lambda: None
        tab._close_capture_async = lambda: None
        tab.executor = executor
        return tab, quick, train, executor

    def test_disconnect_closes_both_module_switches(self):
        """执行器被关掉 → 【开始/暂停】和【快速比赛】**都要关掉**。"""
        tab, quick, train, executor = self._tab(paused=False)
        train.enabled, train.running = True, True
        quick.enabled = True
        tab._connection_stop()
        self.assertIn("disable", train.calls)
        self.assertIn("disable", quick.calls)
        self.assertFalse(quick.enabled)
        self.assertEqual(executor.calls, ["pause"])

    def test_pause_auto_training_never_pauses_executor(self):
        """第二栏【暂停】只停自己的任务 —— 关执行器不是它该管的事。"""
        tab, quick, train, executor = self._tab(paused=False)
        train.enabled, train.running = True, True
        tab.toggle_auto_training()
        self.assertIn("disable", train.calls)
        self.assertEqual(executor.calls, [])
        self.assertFalse(executor.paused)

    def test_quick_match_off_never_pauses_executor(self):
        """快速比赛拨「关」只停自己 —— 同样不许关执行器。"""
        tab, quick, train, executor = self._tab(paused=False)
        quick.enabled = True
        tab.set_quick_match(False)
        self.assertIn("disable", quick.calls)
        self.assertEqual(executor.calls, [])
        self.assertFalse(executor.paused)

    def test_quick_match_switch_wakes_paused_executor(self):
        """快速比赛拨「开」时执行器还停着 → **顺手** `executor.start()`。"""
        tab, quick, train, executor = self._tab(paused=True)
        tab.set_quick_match(True)
        self.assertEqual(executor.calls, ["start"])
        self.assertIn("enable", quick.calls)

    def test_auto_training_wakes_paused_executor(self):
        """第二栏【开始】同理：任务入队后顺手唤醒执行器（兜底，不是必须）。

        `_connected()` 本身含「执行器没暂停」，所以真实场景里这条分支几乎走不到；
        这里直接把「连上了但执行器停着」造出来，验证兜底确实会唤醒。
        """
        tab, quick, train, executor = self._tab(paused=True)
        tab._connected = lambda: True
        tab.toggle_auto_training()
        self.assertEqual(executor.calls, ["start"])
        self.assertIn("enable", train.calls)


class MonitorButtons(unittest.TestCase):
    """监控页两栏按钮的职责（用户 2026-11-01/02 口径）。

    - 第一栏 = 总开关【启动 / 停止】：启动 = 接入游戏；停止 = 断开游戏
      **并且把第二栏复位成「开始」**；
    - 第二栏 = 【开始 / 暂停】：只管自动训练脚本自己，不碰「接入游戏」；
    - **两者互不带动**：点第二栏不能改第一栏的文案，反之亦然。

    2026-11-02 修正的两处根因（回归测的就是这两条）：
    1. 第一栏旧判据是 `not executor.paused`（执行器运行态），第二栏点【暂停】
       里的 `task.unpause()` → `executor.start()` 会把它顶回去 = 第二栏带动第一栏。
       现在第一栏判据是**连接态**（`connected`）。
    2. 第二栏旧判据是 `running or enabled`（enabled 含「在队列里等」），
       点完【开始】立刻变【暂停】但其实一帧没跑。现在只认 `training_active`。
    """

    def _view(self):
        import json
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from src.ui.monitor_view import MonitorActions, MonitorView
        from src.tasks.training.constants import STYLE_BY_NAME

        app = QApplication.instance() or QApplication([])
        groups = {}
        for name, style in STYLE_BY_NAME.items():
            groups.setdefault(style, []).append(name)
        order = ["训练流", "商店流", "扭蛋流", "小游戏流"]
        caps = {k: sorted(groups[k]) for k in order if k in groups}
        with open("configs/monitor_layout.json", encoding="utf-8") as f:
            layout = json.load(f)

        class _Actions(MonitorActions):
            def __init__(self):
                self.log = []

            def toggle_connection(self):
                self.log.append("connection")

            def toggle_auto_training(self):
                self.log.append("training")

            def set_captain(self, name):
                self.log.append(("captain", name))

        actions = _Actions()
        return MonitorView(layout, actions, captains=caps), actions

    @staticmethod
    def _data(**over):
        """一份最小可用数据（键名 = MonitorTab._collect 的键）。"""
        data = {"captain": "", "connected": False,
                "training_running": False, "training_active": False}
        data.update(over)
        return data

    def test_first_bar_is_start_stop(self):
        view, _ = self._view()
        self.assertEqual(view._btn_conn.text(), "启动")
        view.update_data(self._data(connected=True))
        self.assertEqual(view._btn_conn.text(), "停止")

    def test_second_bar_is_start_pause(self):
        view, _ = self._view()
        self.assertEqual(view._btn_train.text(), "开始")
        view.update_data(self._data(training_active=True))
        self.assertEqual(view._btn_train.text(), "暂停")

    def test_second_bar_queued_is_still_start(self):
        """只是排上队（enabled 但没 running）不算在跑 —— 按钮显示【开始】。

        旧判据 `running or enabled` 会让用户点完【开始】就看到【暂停】，
        但其实一帧都还没跑（执行器可能还停着）。
        """
        view, _ = self._view()
        view.update_data(self._data(training_running=True, training_active=False))
        self.assertEqual(view._btn_train.text(), "开始")

    def test_second_bar_does_not_move_first_bar(self):
        """**第二栏点完，第一栏文案不变**（互不带动）。

        这是用户 2026-11-01 反馈的核心诉求，当时没修好：
        第一栏判据是 `not executor.paused`，第二栏的 `task.unpause()`
        会调 `executor.start()` 把 paused 置回 False → 第一栏自己跳回「停止」。
        """
        view, _ = self._view()
        view.update_data(self._data(connected=False))
        self.assertEqual(view._btn_conn.text(), "启动")
        # 第二栏在跑（点了【暂停】之后 training_active 变假）
        view.update_data(self._data(connected=False, training_active=True))
        self.assertEqual(view._btn_conn.text(), "启动")
        view.update_data(self._data(connected=False, training_active=False))
        self.assertEqual(view._btn_conn.text(), "启动")

    def test_disconnect_resets_second_bar_to_start(self):
        """第一栏【停止】（断开游戏）后，第二栏回到未启动状态 = 【开始】。

        用户 2026-11-02 口径：断开连接的链路要收尾干净，
        否则用户会看到一个「暂停」按钮，但游戏已经断开、点了也没用。
        """
        view, _ = self._view()
        # 断开前：连着游戏、训练在跑
        view.update_data(self._data(connected=True, training_active=True))
        self.assertEqual(view._btn_conn.text(), "停止")
        self.assertEqual(view._btn_train.text(), "暂停")
        # 断开后：连接态为假、训练任务已 disable（training_active 变假）
        view.update_data(self._data(connected=False, training_active=False))
        self.assertEqual(view._btn_conn.text(), "启动")
        self.assertEqual(view._btn_train.text(), "开始")

    def test_checkbox_starts_unchecked(self):
        """默认不勾 —— 旧版默认勾上，用户没动过就等于指定了第一个训练流角色。"""
        view, _ = self._view()
        self.assertFalse(view._captain_check.isChecked())

    def test_uncheck_calls_set_captain_empty(self):
        """取消勾选 → 必须调 set_captain("")（清空变量）。"""
        view, actions = self._view()
        view.update_data(self._data(captain="野上泷"))
        actions.log.clear()
        view._captain_check.setChecked(False)
        self.assertIn(("captain", ""), actions.log)

    def test_check_uses_current_selection_not_first(self):
        """勾选时用下拉**当前**选中的角色，不是「列表第一个」。"""
        view, actions = self._view()
        view.update_data(self._data(captain="野上泷"))
        actions.log.clear()
        view._captain_check.setChecked(False)
        view._captain_check.setChecked(True)
        self.assertIn(("captain", "野上泷"), actions.log)

    @staticmethod
    def _same_icon(btn, icon) -> bool:
        """按钮当前图标是不是这一枚（按像素比，不看对象身份）。"""
        a = btn.icon().pixmap(16, 16).toImage()
        b = icon.icon().pixmap(16, 16).toImage()
        return a == b

    def test_first_bar_icon_flips_to_stop(self):
        """第一栏跑了以后图标必须是 ■，不能还是 ▶（用户 2026-11-02 口径）。"""
        from ok.ui.qt.common.OKIcon import OKIcon
        from qfluentwidgets import FluentIcon

        view, _ = self._view()
        view.update_data(self._data(connected=False))
        self.assertTrue(self._same_icon(view._btn_conn, FluentIcon.PLAY))
        view.update_data(self._data(connected=True))
        self.assertTrue(self._same_icon(view._btn_conn, OKIcon.STOP))
        # 再断开：图标要回到 ▶
        view.update_data(self._data(connected=False))
        self.assertTrue(self._same_icon(view._btn_conn, FluentIcon.PLAY))

    def test_second_bar_shows_connecting_while_starting(self):
        """接入游戏期间第二栏显示【启动中…】。

        用户 2026-11-02 口径：「第二栏按下开始的时候，如果没有连上游戏，
        就主动启动」。接入要等窗口稳定 + 设备就绪（几秒到几十秒），
        这段时间按钮既不是【开始】也不是【暂停】，得显示点什么，
        否则又变成「点了没反应」。
        """
        from qfluentwidgets import FluentIcon

        view, _ = self._view()
        view.update_data(self._data(training_active=False, connecting=True))
        self.assertEqual(view._btn_train.text(), "启动中…")
        self.assertTrue(self._same_icon(view._btn_train, FluentIcon.SYNC))
        # 接上并跑起来 → 【暂停】
        view.update_data(self._data(training_active=True, connecting=True))
        self.assertEqual(view._btn_train.text(), "暂停")
        self.assertTrue(self._same_icon(view._btn_train, FluentIcon.PAUSE))
        # 接入失败/取消 → 回到【开始】
        view.update_data(self._data(training_active=False, connecting=False))
        self.assertEqual(view._btn_train.text(), "开始")
        self.assertTrue(self._same_icon(view._btn_train, FluentIcon.PLAY))

    def test_second_bar_icon_is_pause_not_stop(self):
        """第二栏文案是【暂停】，图标就该是 ‖；用 ■ 语义对不上。"""
        from qfluentwidgets import FluentIcon

        view, _ = self._view()
        view.update_data(self._data(training_active=False))
        self.assertTrue(self._same_icon(view._btn_train, FluentIcon.PLAY))
        view.update_data(self._data(training_active=True))
        self.assertTrue(self._same_icon(view._btn_train, FluentIcon.PAUSE))


class MonitorConnectedCriterion(unittest.TestCase):
    """第一栏的判据：**截帧通道活着 且 执行器没暂停**（用户 2026-11-02 口径）。

    根因：框架启动时会自动刷新设备并建好 WGC 截图会话，所以 app 一打开
    `capture_method` 就有对象 —— 旧判据只看它，第一栏一进页面就显示【停止】，
    用户看到的是「默认已经开始了」。
    """

    def _patch(self, capture, paused):
        import ok
        from src.ui.MonitorTab import MonitorTab

        class _Cap:
            def __init__(self, alive):
                self.alive = alive

            def connected(self):
                return self.alive

        class _DM:
            capture_method = None

        class _Ex:
            pass

        dm = _DM()
        dm.capture_method = capture
        ex = _Ex()
        ex.paused = paused
        old_dm = getattr(ok.og, "device_manager", None)
        old_ex = getattr(ok.og, "executor", None)
        ok.og.device_manager = dm
        ok.og.executor = ex
        self.addCleanup(lambda: setattr(ok.og, "device_manager", old_dm))
        self.addCleanup(lambda: setattr(ok.og, "executor", old_ex))
        return MonitorTab, dm, ex

    def test_capture_without_executor_is_not_started(self):
        """启动态：截图会话已建好，但执行器还 paused → 显示【启动】。"""
        cap = type("Cap", (), {"connected": lambda self: True})()
        tab, dm, ex = self._patch(cap, paused=True)
        self.assertFalse(tab._connected())
        ex.paused = False
        self.assertTrue(tab._connected())

    def test_closed_capture_is_not_started(self):
        """执行器在跑但截图会话没了（断开游戏）→ 显示【启动】。"""
        cap = type("Cap", (), {"connected": lambda self: False})()
        tab, dm, ex = self._patch(cap, paused=False)
        self.assertFalse(tab._connected())
        dm.capture_method = None
        self.assertFalse(tab._connected())


class ThemeSelect(unittest.TestCase):
    """「请选择一个题材」三选一：**优先红色**，都一样就选中间。

    用户口径（2026-11-02，第三次修正为准）：
    - 有一张是**红字**（粉/洋红也算）→ 选它（天赋卡就是红的）；
    - 颜色都一样（三张全白，或三张**全红**）→ 选中间那张。
    判据**不是**「哪张跟别人不一样」，所以下面专门测「全红 → 中间」这条。
    """

    W, H = 1549, 871
    TAGS = [("#悬疑", 345), ("#治愈", 728), ("#爽文", 1111)]
    Y, TH, TW = 320, 42, 90

    def _frame(self, warm=()):
        """标题 + 三张卡 + 确定；`warm` 里列出的下标用**红字**（色相 0），其余白字。"""
        frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        frame[:] = (200, 200, 200)                     # 浅灰底（低饱和，不进颜色统计）
        for idx, (_name, x) in enumerate(self.TAGS):
            roi = frame[self.Y:self.Y + self.TH, x:x + self.TW]
            roi[:] = (250, 180, 120) if idx in warm else (250, 250, 250)   # BGR 蓝底 / 白底
            # 文字像素：占框内约三成（模拟笔画），暖色卡用纯红
            band = frame[self.Y + 8:self.Y + self.TH - 8, x + 6:x + self.TW - 6]
            band[:, :] = (40, 40, 220) if idx in warm else (255, 255, 255)
        return frame

    def _texts(self, confirm=True):
        texts = [box(663, 122, "请选择一个题材", 221, 38)]
        texts += [box(x, self.Y, name, self.TW, self.TH) for name, x in self.TAGS]
        if confirm:
            texts.append(box(729, 743, "确定", 83, 46))
        return texts

    @staticmethod
    def _handler():
        from src.tasks.training.handlers import HANDLERS
        return next(h for h in HANDLERS if h.__name__ == "handle_theme_select")

    def test_unique_red_tag_wins(self):
        """只有第 3 张是红字 → 选它（不按位置、不看光标）。"""
        frame = self._frame(warm=(2,))
        texts = self._texts()
        task = DryRunTask(frame, texts)
        self.assertTrue(self._handler()(task, texts))
        clicks = [a for a in task.actions if a[0] == "click"]
        self.assertEqual(len(clicks), 1)
        self.assertAlmostEqual(clicks[0][1], 1111 + self.TW / 2, delta=3)
        self.assertTrue(any("选「#爽文」" in m for m in task.logs))
        self.assertTrue(any("唯一红字" in m for m in task.logs))

    def test_all_same_color_picks_middle(self):
        """三张全白（占比 0.00）→ 中间那张。"""
        task = DryRunTask(self._frame(), self._texts())
        self.assertTrue(self._handler()(task, self._texts()))
        clicks = [a for a in task.actions if a[0] == "click"]
        self.assertEqual(len(clicks), 1)
        self.assertAlmostEqual(clicks[0][1], 728 + self.TW / 2, delta=3)
        self.assertTrue(any("选「#治愈」" in m for m in task.logs))
        self.assertTrue(any("没有红字" in m for m in task.logs))

    def test_all_red_picks_middle(self):
        """三张**全是红字**（2026-11-02 第二次实报）→ 同色，仍选中间。"""
        frame = self._frame(warm=(0, 1, 2))
        texts = self._texts()
        task = DryRunTask(frame, texts)
        self.assertTrue(self._handler()(task, texts))
        clicks = [a for a in task.actions if a[0] == "click"]
        self.assertEqual(len(clicks), 1)
        self.assertAlmostEqual(clicks[0][1], 728 + self.TW / 2, delta=3)
        self.assertTrue(any("选「#治愈」" in m for m in task.logs))
        self.assertTrue(any("三张都红" in m for m in task.logs))


class StartablePages(unittest.TestCase):
    """`is_startable`：MCP 中途接手时允许「就地开始」的页面白名单。

    2026-11-02 实跑：游戏停在「WARD文档」汇报页，`reset_to_start()` 回大厅失败、
    这页又不在白名单 → 返回 False，整轮起不来（`logs/mcp/1798afc...`）。
    """

    def test_writing_event_pages_are_startable(self):
        for texts in (
            [box(663, 122, "请选择一个题材", 221, 38), box(729, 743, "确定", 83, 46)],
            [box(110, 98, "WARD文档", 179, 43), box(155, 183, "收集到的idea", 200, 38),
             box(1179, 687, "交稿", 75, 43)],
        ):
            task = FakeTask(texts)
            self.assertTrue(task.is_startable(texts))

    def test_match_page_is_not_startable(self):
        """比赛内部仍然不行：盲点返回会毁局。"""
        task = FakeTask([box(400, 850, "VS", 90, 40), box(300, 60, "比分", 200, 40)])
        self.assertFalse(task.is_startable(task._texts))


class ManuscriptSubmit(unittest.TestCase):
    """「WARD文档」汇报页（写小说事件的收尾）：点右下角「交稿」。

    2026-11-02 用户截图：左栏「收集到的idea」列着这次用到的标签
    （#权谋 / #治愈 / #读心术），右侧是正文，右下角黄色「交稿」。
    """

    def _texts(self):
        return [
            box(110, 98, "WARD文档", 179, 43),
            box(1301, 99, "跳过", 111, 40),
            box(155, 183, "收集到的idea", 200, 38),
            box(207, 256, "#权谋", 95, 48),
            box(466, 176, "生死决斗的过程中，哈涅塔意外觉醒了“抽卡系统”", 907, 37),
            box(1179, 687, "交稿", 75, 43),
            box(224, 807, "WARD文档", 144, 34),      # 底部装饰，别被它带偏
        ]

    def test_clicks_submit(self):
        texts = self._texts()
        task = FakeTask(texts)
        self.assertTrue(task.handle_manuscript_submit(texts))
        self.assertTrue(any(a[0] == "click_box" and a[1] == "交稿" for a in task.actions))

    def test_needs_idea_column_not_just_title(self):
        """只有标题（页面淡入中间态 / 底部那个装饰）不算这页，避免误点。"""
        texts = [box(110, 98, "WARD文档", 179, 43), box(1179, 687, "交稿", 75, 43)]
        task = FakeTask(texts)
        self.assertFalse(task.handle_manuscript_submit(texts))
        self.assertEqual(task.actions, [])


class BoostSettle(unittest.TestCase):
    """加强训练（「球队经理的帮助」那页）：头一拍只观察，点完卡片等一会儿再点确定。

    用户 2026-11-02 实机反馈「每次都是两次空点击：第一下点卡片没点下去，
    然后又点了继续又没点中」——页面淡入那一拍就点，卡片还没吃到点击。
    """

    W, H = 1549, 871

    def _texts(self):
        return [
            box(60, 100, "球队经理的帮助", 300, 40),
            box(700, 60, "加强训练", 200, 40),
            box(300, 300, "100%", 120, 50), box(300, 420, "+24", 140, 70),
            box(1000, 300, "85%", 120, 50), box(1000, 420, "+78", 140, 70),
            box(729, 743, "确定", 83, 46),
        ]

    @staticmethod
    def _handler():
        from src.tasks.training.handlers import HANDLERS
        return next(h for h in HANDLERS if h.__name__ == "handle_boost_training")

    def test_first_tick_only_waits(self):
        frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        texts = self._texts()
        task = DryRunTask(frame, texts)
        self.assertTrue(self._handler()(task, texts))
        self.assertEqual(task.actions, [])            # 淡入那一拍：一下都不点
        self.assertTrue(any("头一拍先不点" in m for m in task.logs))

    def test_second_tick_clicks_card_then_confirm(self):
        frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        texts = self._texts()
        task = DryRunTask(frame, texts)
        handler = self._handler()
        handler(task, texts)                          # 第一拍：只等
        task.actions.clear()
        self.assertTrue(handler(task, texts))         # 第二拍：点卡片 + 确定
        kinds = [(a[0], a[1] if a[0] == "click_box" else a[1]) for a in task.actions]
        self.assertEqual(kinds[0][0], "click")        # 先点卡片
        self.assertIn("确定", [a[1] for a in task.actions if a[0] == "click_box"])
        self.assertGreaterEqual(len(task.actions), 2)

    def test_no_double_confirm(self):
        """点过确定之后的那一拍不能再点一次（2026-11-02 日志 22:34:51/52 连点两次）。"""
        frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        texts = self._texts()
        task = DryRunTask(frame, texts)
        handler = self._handler()
        handler(task, texts)
        handler(task, texts)                          # 点卡片 + 确定
        task.actions.clear()
        self.assertTrue(handler(task, texts))         # 第三拍：页面还在 → 不重复点
        self.assertEqual(task.actions, [])


class SpeedEnsureGuard(unittest.TestCase):
    """倍速：判不出「这条横带里有没有倍速图标」时，一下都不许点。

    2026-11-02 23:41:34 实跑：炒股事件结束后回到训练日主界面，第一拍点了倍速
    一下，把正在跑的 3x 打成了 OFF，再补 3 下才回到 3x。
    根因：老判据「空心三角占比 ≥0.05 = 没到顶」在**没有图标**的画面上恒成立
    （实心 0.00 / 空心 1.00）。
    """

    W, H = 1549, 871
    STATE_DIR = os.path.join(ROOT, "logs", "topbar", "full_states")

    def _texts(self):
        return [box(30, 20, "第3周", 120, 40), box(200, 20, "星期四", 130, 40),
                box(380, 16, "130/130", 210, 53)]

    def _task(self, frame):
        task = DryRunTask(frame, self._texts())
        task.actions.clear()
        return task

    def _frame(self, name):
        path = os.path.join(self.STATE_DIR, name)
        if not os.path.exists(path):
            self.skipTest(f"缺少样本 {path}")
        return cv2.imread(path)

    def test_no_icon_no_click(self):
        """零帧：这条横带里什么都没有 → 判不了就不点。"""
        task = self._task(np.zeros((self.H, self.W, 3), dtype=np.uint8))
        self.assertFalse(task.ensure_speed(task._texts))
        self.assertEqual(task.actions, [])

    def test_stock_page_frame_no_click(self):
        """右上角是炒股页的金币徽章（就压在倍速图标的坐标旁）→ 不许点。"""
        task = self._task(self._frame("not_topbar_stock.png"))
        self.assertFalse(task.ensure_speed(task._texts))
        self.assertEqual(task.actions, [])

    def test_at_max_no_click(self):
        """3x 的真帧：已经到顶，不该再点。"""
        task = self._task(self._frame("full_0.png"))
        self.assertFalse(task.ensure_speed(task._texts))
        self.assertEqual(task.actions, [])

    def test_one_click_per_tick_when_not_at_max(self):
        """1x 的真帧：一拍只点一下（盲连点会把 3x 直接点回 1x）。"""
        task = self._task(self._frame("full_2.png"))
        self.assertTrue(task.ensure_speed(task._texts))
        self.assertEqual(len(task.actions), 1)
        self.assertEqual(task.actions[0][:1], ("click",))


class CookQualityCards(unittest.TestCase):
    """做饭食材三选一 + 品质卡排序（2026-11-03）。

    回归图（docs/shots/2026-11-03-cook-ingredient.png）只能证明「这张图被
    handle_ingredient_select 认领」，证明不了「选的是金卡」—— 决策必须用
    构造帧测（ENGINEERING §5：回归图管命中，这里管选哪张）。

    颜色取实机实测值（logs/live/cook_now.jpg）：蓝 103/121/197、
    紫 135/115/188、金 21/251/193（OpenCV 色相 0~180）。
    """

    H, W = 1080, 1920
    COLORS = {"蓝": (103, 121, 197), "紫": (135, 115, 188), "金": (21, 251, 193)}
    NAMES = ("隔夜饭", "菜市场的普通食材", "珍稀食材")

    def _frame(self, qualities):
        """三列品质色带（每列铺满标题框所在的那一条横带）。"""
        frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        for cx, quality in zip((0.25, 0.50, 0.75), qualities):
            bgr = cv2.cvtColor(
                np.array(self.COLORS[quality], np.uint8).reshape(1, 1, 3),
                cv2.COLOR_HSV2BGR)[0, 0].tolist()
            x1, x2 = int(self.W * (cx - 0.06)), int(self.W * (cx + 0.06))
            frame[520:620, x1:x2] = bgr
        return frame

    def _texts(self):
        return [box(int(self.W * (0.25 + i * 0.25)) - 100, 540, name, 200, 60)
                for i, name in enumerate(self.NAMES)]

    def _task(self, qualities):
        return DryRunTask(self._frame(qualities), self._texts())

    def test_quality_read_from_title_bar(self):
        """标题条认品质：蓝/紫/金都要认对（名字 OCR 会截字，不能靠名字）。"""
        task = self._task(("蓝", "紫", "金"))
        cards = task.quality_cards(task._texts)
        self.assertEqual([c["quality"] for c in cards], ["蓝", "紫", "金"])

    def test_pick_gold_over_purple_over_blue(self):
        """默认顺序 金 > 紫 > 蓝：做饭页选最右的金卡（珍稀食材）。"""
        task = self._task(("蓝", "紫", "金"))
        pick, ordered = task.quality_card_order(task.quality_cards(task._texts))
        self.assertEqual(pick["name"], "珍稀食材")
        self.assertEqual([c["quality"] for c in ordered], ["金", "紫", "蓝"])

    def test_order_is_rainbow_gold_purple_blue(self):
        """未登记页面用 彩 > 金 > 紫 > 蓝（用户 2026-11-03 口径）。"""
        def card(quality, mates=0, x=0):
            return {"box": box(x, 0, quality), "name": quality,
                    "quality": quality, "mates": mates}
        cards = [card("蓝", x=100), card("紫", x=200),
                 card("彩", x=300), card("金", x=400)]
        from src.tasks.training.scoring import TrainingScoringMixin
        pick, ordered = TrainingScoringMixin.quality_card_order(cards)
        self.assertEqual([c["quality"] for c in ordered], ["彩", "金", "紫", "蓝"])

    def test_mates_break_quality_tie(self):
        """同品质比队友数：队友多的优先，有队友的优先于没队友的。"""
        def card(name, mates, x):
            return {"box": box(x, 0, name), "name": name,
                    "quality": "紫", "mates": mates}
        from src.tasks.training.scoring import TrainingScoringMixin
        pick, _ = TrainingScoringMixin.quality_card_order(
            [card("没队友", 0, 700), card("一队友", 1, 500), card("两队友", 2, 300)])
        self.assertEqual(pick["name"], "两队友")

    def test_handler_clicks_gold_then_confirm(self):
        """handler 的动作顺序：点金卡正文 → 点「确定」（2026-11-03 实机节奏）。"""
        texts = self._texts() + [box(700, 950, "确定", 110, 60)]
        texts.append(Box(x=600, y=130, width=280, height=50,
                         name="请选择要使用的食材"))
        task = DryRunTask(self._frame(("蓝", "紫", "金")), texts)
        self.assertTrue(task.handle_ingredient_select(texts))
        kinds = [a[0] for a in task.actions]
        self.assertEqual(kinds, ["click", "click_box"])
        # 点的是最右那列（金卡）的卡片正文，而不是标题条
        self.assertGreater(task.actions[0][1], int(self.W * 0.70))
        self.assertEqual(task.actions[1][1], "确定")

    def test_wheel_clicks_once(self):
        """转盘（钓鱼啦！/ 开始做饭！共用）：只点一次，动画期间（8 秒内）不重复点。"""
        texts = [box(560, 20, "开始做饭！", 350, 100),
                 box(700, 780, "转一转", 120, 50)]
        task = DryRunTask(self._frame(("蓝", "紫", "金")), texts)
        self.assertTrue(task.handle_spin_wheel(texts))
        self.assertEqual(len(task.actions), 1)
        self.assertTrue(task.handle_spin_wheel(texts))     # 动画中再进一拍
        self.assertEqual(len(task.actions), 1)             # 不许补点

    def test_wheel_ignores_lobby_entry(self):
        """大厅「玩法目录」里也有「转一转」入口，但那不在底部按钮带里 → 不许点。

        2026-11-03：判据从「只认文字」改成「底部居中那一条」，就是为这个。
        """
        texts = [box(600, 480, "转一转", 200, 60),      # cy≈0.48，卡片区
                 box(1100, 480, "赛事", 160, 60)]
        task = DryRunTask(self._frame(("蓝", "紫", "金")), texts)
        self.assertFalse(task.handle_spin_wheel(texts))
        self.assertEqual(task.actions, [])

    def test_wheel_falls_back_on_known_title(self):
        """按钮没认出来时：已知转盘标题 + 固定坐标（两页实测同位置 0.5w/0.923h）。"""
        texts = [box(560, 20, "钓鱼啦！", 350, 100)]
        task = DryRunTask(self._frame(("蓝", "紫", "金")), texts)
        self.assertTrue(task.handle_spin_wheel(texts))
        self.assertEqual(task.actions, [("click", int(task.width * 0.5),
                                         int(task.height * 0.923))])


class LiveStreamPick(unittest.TestCase):
    """直播三选一（天赋「人气主播」：和泉香织，2026-11-07 实机×5）。

    回归数据取五次实录的 logs/debug/1107-*_noresp3s/ocr.txt（真坐标）；
    帧是纯黑 + 按列铺品质色带（FakeTask 注入品质， CookQualityCards 同款思路）。
    卡片标题条 y≈336、效果行 y≈515~605 @1080p，卡心 x ≈ 405/777/1150。
    """

    H, W = 1080, 1920
    # 五次实录的卡面（品质, 名字, [效果行...]），左→右
    SCENES = (
        (("紫", "紫", "蓝"),
         [("联机游戏回", ["可能粉丝翻倍", "可能粉丝-5万"]),
          ("动画杂谈回", ["粉丝+3万", "投射+30"]),
          ("正常回", ["粉丝+5万"])],
         "动画杂谈回"),                                   # 第1次：选①安全紫
        (("蓝", "紫", "紫"),
         [("闲聊回", ["精力+30", "と粉丝-1万"]),
          ("高难游戏回", ["精力-30", "と粉丝+15万"]),
          ("马拉松直播回", ["精力-10", "と粉丝+10万"])],
         "高难游戏回"),                                   # 第2次：②粉丝最大的紫
        (("金", "紫", "蓝"),
         [("点歌回", ["效果未知"]),
          ("联机游戏回", ["可能粉丝翻倍", "可能粉丝-5万"]),
          ("闲聊回", ["精力+30", "と粉丝-1万"])],
         "点歌回"),                                       # 第3次：③金卡>风险卡
        (("紫", "蓝", "紫"),
         [("动画杂谈回", ["粉丝+3万", "投射+30"]),
          ("闲聊回", ["精力+30", "粉丝-1万"]),
          ("高难游戏回", ["精力-30", "と粉丝+15万"])],
         "动画杂谈回"),                                   # 第4次：①安全紫（弃+15万）
        (("紫", "蓝", "金"),
         [("联机游戏回", ["可能粉丝翻倍", "可能粉丝-5万"]),
          ("抽奖回", ["零用钱-100", "粉丝+10万"]),
          ("点歌回", ["效果未知"])],
         "点歌回"),                                       # 第5次：③（蓝卡垫底）
    )

    def _frame(self, qualities):
        """三列品质色带铺在**标题条**位置（y 330~380，实测卡名 y≈336 高 40）。

        `card_quality_color` 先看标题框自己那条横带、再看框上方卡头，
        所以色带必须盖住 336~376 这一段（黑帧里卡头是黑的，靠标题条带兜底）。
        """
        frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        colors = {"蓝": (103, 121, 197), "紫": (135, 115, 188), "金": (21, 251, 193)}
        for cx, quality in zip((405, 777, 1150), qualities):
            bgr = cv2.cvtColor(
                np.array(colors[quality], np.uint8).reshape(1, 1, 3),
                cv2.COLOR_HSV2BGR)[0, 0].tolist()
            x1, x2 = cx - 90, cx + 90
            frame[330:380, x1:x2] = bgr
        return frame

    def _texts(self, cards):
        """直播页 OCR 框：真坐标（第1次实录 ocr.txt），标题+卡名+效果行+确定。"""
        texts = [box(636, 139, "请选择1个直播主题", 274, 35)]
        xs = (405, 777, 1150)
        for cx, (name, effects) in zip(xs, cards):
            texts.append(box(cx - 80, 336, name, 165, 40))
            for i, eff in enumerate(effects):
                texts.append(box(cx - 78, 515 + i * 45, eff, 160, 45))
        texts.append(box(731, 765, "确定", 81, 47))
        return texts

    def _pick(self, scene):
        qualities, cards, _ = scene
        texts = self._texts(cards)
        task = DryRunTask(self._frame(qualities), texts)
        self.assertTrue(task.handle_live_stream(texts),
                        f"没认领：{qualities} / {cards}")
        return task, texts

    def test_five_recorded_scenes(self):
        """五次实录逐一对账：规则选择必须和用户当时点的那张一致（5/5）。"""
        for i, scene in enumerate(self.SCENES, 1):
            task, _ = self._pick(scene)
            self.assertEqual(len(task.actions), 2, f"第{i}次动作数")
            self.assertEqual(task.actions[0][0], "click", f"第{i}次先点卡")
            self.assertEqual(task.actions[1][1], "确定", f"第{i}次后点确定")
            self.assertTrue(any(f"选「{scene[2]}」" in m for m in task.logs),
                            f"第{i}次应选 {scene[2]}，日志：{task.logs}")

    def test_click_lands_on_card_body(self):
        """点的是卡片**正文区**（标题框下方），不是标题条边缘。"""
        task, _ = self._pick(self.SCENES[0])
        x, y = task.actions[0][1], task.actions[0][2]
        self.assertAlmostEqual(y / self.H, 336 / self.H + 0.06, delta=0.02)
        self.assertAlmostEqual(x / self.W, 777 / self.W, delta=0.02)  # 第1次选中间

    def test_no_title_no_claim(self):
        """没有直播标题（比如别处的三张上色卡）→ 不认领。"""
        qualities, cards, _ = self.SCENES[0]
        texts = self._texts(cards)
        texts = [t for t in texts if t.name != "请选择1个直播主题"]
        task = DryRunTask(self._frame(qualities), texts)
        self.assertFalse(task.handle_live_stream(texts))
        self.assertEqual(task.actions, [])

    def test_stamina_cost_is_ignored(self):
        """第2次的口径坐实：紫卡扣30精力也选它（粉丝+15万 > +10万）。"""
        scene = self.SCENES[1]
        task, _ = self._pick(scene)
        self.assertTrue(any("粉丝增益最大" in m for m in task.logs))
        # 点中间那张（cx≈777）
        self.assertAlmostEqual(task.actions[0][1] / self.W, 777 / self.W, delta=0.02)


if __name__ == "__main__":
    unittest.main()
