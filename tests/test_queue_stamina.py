"""多角色队列 / 专注恢复的纯逻辑测试（不启动游戏、不跑 OCR）。

覆盖两条 2026-11-04 用户口径：

    ① 多角色队列：野上泷 7500 → 亚理纱·贾斯敏 7300 → 近江奏 7300，
       每局结束在「注册球员 / 训练奖励」页读该球员评分，达标才换下一个。
    ② 专注恢复：大厅顶栏「+」→ 恢复页；三张卡只点整词「使用」，
       **钻石购买那一行永远不点**；差多少补多少（够用里取最小）。

运行：
    .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_queue_stamina.py" -v
"""

import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402
from tools.check_handlers import DryRunTask  # noqa: E402
from src.tasks.training.queue import (CaptainQueue, parse_queue,  # noqa: E402
                                      position_of)
from src.tasks.training.handlers.stamina import pick_stamina_bottle  # noqa: E402


def box(x, y, name, w=240, h=34):
    return Box(x=x, y=y, width=w, height=h, name=name)


def task_with(texts, config=None, size=(1549, 871)):
    frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    task = DryRunTask(frame, texts)
    task.config.update(config or {})
    return task


# 「专注恢复」页（用户 2026-11-04 截图，1549x871）的 OCR 版式：
#   标题「专注恢复」/「当前专注 107/150」/ 三张卡（钻石购买 + 两瓶能量饮料）。
def recover_page(owned_small=21, owned_big=2):
    return [
        box(80, 118, "专注恢复", 160, 42),
        box(200, 205, "当前专注", 190, 40),
        box(215, 490, "107/150", 150, 40),
        box(240, 610, "下次恢复：00:00:34", 300, 26),
        # ① 钻石购买（按钮是钻石图标 + 价格 50，没有「使用」）
        box(660, 262, "钻石购买", 160, 34),
        box(660, 300, "专注+60", 120, 30),
        box(1185, 275, "50", 190, 50),
        # ② 专注能量饮料 +30
        box(1320, 380, f"拥有：{owned_small}", 130, 30),
        box(660, 415, "专注能量饮料", 230, 34),
        box(660, 455, "专注+30", 120, 30),
        box(1185, 430, "使用", 190, 50),
        # ③ 专注能量饮料·大瓶装 +60
        box(1300, 530, f"拥有：{owned_big}", 120, 30),
        box(660, 565, "专注能量饮料·大瓶装", 300, 34),
        box(660, 600, "专注+60", 120, 30),
        box(1185, 580, "使用", 190, 50),
    ]


# 「恭喜获得 / 训练奖励」页（docs/shots/2026-10-27-register-1.png，1307x735）实测坐标：
# 评级字母在评分正上方、位置名词在评级右边约 0.075w（**不是**评分正上方！）。
def award_page():
    return [
        box(561, 118, "恭喜获得", 182, 55),
        box(607, 211, "训练奖励", 93, 32),
        box(272, 424, "At", 36, 38),
        box(365, 430, "中锋", 38, 20),
        box(423, 426, "B", 34, 29),
        box(506, 427, "控卫", 51, 24),
        box(576, 426, "Bt", 34, 27),
        box(663, 427, "分卫", 44, 24),
        box(727, 426, "B", 29, 29),
        box(814, 428, "小前", 41, 25),
        box(878, 426, "B", 29, 32),
        box(969, 430, "大前", 37, 20),
        box(257, 457, "4822", 59, 24),
        box(407, 455, "3028", 63, 29),
        box(555, 453, "3025", 66, 34),
        box(707, 455, "2915", 64, 30),
        box(857, 453, "2917", 60, 33),
        box(611, 378, "注册球员", 90, 29),
        box(682, 622, "下一步", 282, 59),
    ]


class QueueParsing(unittest.TestCase):

    def test_parse_default_spec(self):
        items = parse_queue("野上泷:7500,亚理纱·贾斯敏:7300,近江奏:7300")
        self.assertEqual([i["name"] for i in items], ["野上泷", "亚理纱·贾斯敏", "近江奏"])
        self.assertEqual([i["target"] for i in items], [7500, 7300, 7300])

    def test_parse_tolerates_chinese_separators_and_spaces(self):
        items = parse_queue(" 野上泷：7500 ；亚理纱·贾斯敏 = 7300\n近江奏 7300，")
        self.assertEqual([i["name"] for i in items], ["野上泷", "亚理纱·贾斯敏", "近江奏"])
        self.assertEqual([i["target"] for i in items], [7500, 7300, 7300])

    def test_name_without_target_is_kept(self):
        items = parse_queue("野上泷")
        self.assertEqual(items, [{"name": "野上泷", "target": None}])

    def test_empty_spec(self):
        self.assertEqual(parse_queue(""), [])
        self.assertEqual(parse_queue(None), [])

    def test_position_of_handles_rarity_prefix(self):
        self.assertEqual(position_of("野上泷"), "大前")
        self.assertEqual(position_of("SR-近江奏"), "分卫")
        self.assertEqual(position_of("亚理纱贾斯敏"), "中锋")     # 间隔点被吃掉
        self.assertIsNone(position_of("查无此人"))


class QueueProgress(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "queue.json")

    def make(self, spec="野上泷:7500,亚理纱·贾斯敏:7300,近江奏:7300"):
        return CaptainQueue(spec, path=self.path, resume=False)

    def test_not_met_keeps_same_captain(self):
        q = self.make()
        self.assertFalse(q.record(7400))
        self.assertEqual(q.captain, "野上泷")

    def test_met_advances_cursor(self):
        q = self.make()
        self.assertTrue(q.record(7500))
        self.assertEqual(q.captain, "亚理纱·贾斯敏")
        self.assertEqual(q.position, "中锋")

    def test_unreadable_score_is_not_met(self):
        q = self.make()
        self.assertFalse(q.record(None))
        self.assertEqual(q.captain, "野上泷")

    def test_no_target_never_meets(self):
        q = CaptainQueue("野上泷", path=self.path, resume=False)
        self.assertFalse(q.record(99999))
        self.assertEqual(q.captain, "野上泷")

    def test_finishes_after_all_targets(self):
        q = self.make()
        q.record(7500)
        q.record(7300)
        q.record(7300)
        self.assertTrue(q.finished)
        self.assertIsNone(q.captain)

    def test_progress_survives_restart(self):
        q = self.make()
        q.record(7500)
        again = CaptainQueue("野上泷:7500,亚理纱·贾斯敏:7300,近江奏:7300",
                             path=self.path, resume=True)
        self.assertEqual(again.captain, "亚理纱·贾斯敏")
        self.assertEqual(len(again.runs), 1)

    def test_changed_spec_does_not_resume(self):
        q = self.make()
        q.record(7500)
        other = CaptainQueue("近江奏:7300", path=self.path, resume=True)
        self.assertEqual(other.cursor, 0)
        self.assertEqual(other.captain, "近江奏")

    def test_in_memory_queue_records_without_path(self):
        """纯内存队列（2026-11-07 起默认）：path=None 时 record/save 不炸、游标照走。"""
        q = CaptainQueue("野上泷:7500,亚理纱·贾斯敏:7300")
        self.assertIsNone(q.path)
        self.assertFalse(q.record(7400))          # 未达标 → 原地
        self.assertTrue(q.record(7500))           # 达标 → 游标前进
        self.assertEqual(q.captain, "亚理纱·贾斯敏")


class StaminaBottleChoice(unittest.TestCase):

    def rows(self):
        return task_with(recover_page()).read_stamina_recover_options(recover_page())

    def test_page_parsed_into_three_cards(self):
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        small = [r for r in rows if r["title"] == "专注能量饮料"][0]
        big = [r for r in rows if "大瓶装" in r["title"]][0]
        self.assertEqual((small["gain"], small["owned"]), (30, 21))
        self.assertEqual((big["gain"], big["owned"]), (60, 2))
        self.assertTrue(small["use"] is not None and big["use"] is not None)

    def test_diamond_row_is_forbidden(self):
        rows = self.rows()
        diamond = [r for r in rows if "钻石" in r["title"]][0]
        self.assertTrue(diamond["forbidden"])
        self.assertIsNone(diamond["use"])          # 没有「使用」按钮 → 结构性禁止
        self.assertEqual(diamond["price"], 50)

    def test_small_bottle_when_it_is_enough(self):
        pick = pick_stamina_bottle(self.rows(), 25)
        self.assertEqual(pick["gain"], 30)

    def test_big_bottle_when_small_is_not_enough(self):
        pick = pick_stamina_bottle(self.rows(), 45)
        self.assertEqual(pick["gain"], 60)

    def test_biggest_when_nothing_is_enough(self):
        pick = pick_stamina_bottle(self.rows(), 500)
        self.assertEqual(pick["gain"], 60)

    def test_nothing_when_full(self):
        self.assertIsNone(pick_stamina_bottle(self.rows(), 0))
        self.assertIsNone(pick_stamina_bottle(self.rows(), -3))

    def test_owned_zero_is_skipped(self):
        rows = task_with(recover_page(owned_small=0)).read_stamina_recover_options(
            recover_page(owned_small=0))
        pick = pick_stamina_bottle(rows, 25)
        self.assertEqual(pick["gain"], 60)         # +30 那瓶没有了 → 用大瓶

    def test_diamond_never_picked_even_if_only_option(self):
        texts = [
            box(80, 118, "专注恢复", 160, 42),
            box(215, 490, "107/150", 150, 40),
            box(660, 262, "钻石购买", 160, 34),
            box(660, 300, "专注+60", 120, 30),
            box(1185, 275, "50", 190, 50),
        ]
        rows = task_with(texts).read_stamina_recover_options(texts)
        self.assertTrue(rows)
        self.assertIsNone(pick_stamina_bottle(rows, 30))


class StaminaRecoverDetail(unittest.TestCase):
    """道具详情层：必须点「使用道具」才真的喝下去（2026-11-04 实跑事故）。

    那一局（`logs/debug/1104-190912_loop60`，1549x871）在「列表页 ↔ 详情层」之间
    来回 60 拍、饮料一瓶没喝掉（拥有恒 22、精力恒 15），最后被 STALL_LIMITS 停任务。
    三个根因都在这里钉死：
      ① 不认识详情层 → 没人点「使用道具」；
      ② 详情层的「当前专注 15/150」被 OCR 拆成 `15/15` + `0`，被当成上限 →
         差 0 → 判「不用喝」；
      ③ 关页用固定坐标 (1459,136)，而 X 框中心是 (1448,105) → 点空 30 次。
    """

    def detail_page(self):
        # 真帧 OCR（logs/debug/1104-190912_loop60/ocr.txt）原样搬过来
        return [
            box(1366, 12, "15/150+", 142, 43),
            box(84, 88, "专注恢复", 129, 38),
            box(1433, 91, "X", 30, 29),
            box(198, 173, "当前专", 113, 46),
            box(323, 138, "道具详情", 137, 43),
            box(1313, 197, "限购5/5", 113, 32),
            box(546, 250, "专注能量饮料拥有:22", 286, 40),
            box(1242, 251, "50", 83, 36),
            box(547, 296, "使用后可获得30专注。", 245, 27),
            box(1307, 352, "拥有：22", 110, 27),
            box(1249, 397, "使用", 69, 42),
            box(213, 487, "15/15", 97, 45),          # ← 事故里的假上限
            box(728, 506, "1/22", 87, 42),           # 数量选择
            box(1312, 492, "拥有：3", 116, 32),
            box(1246, 544, "使用", 74, 44),
            box(698, 619, "使用道具", 145, 49),      # ← 真正的确认按钮
        ]

    def test_detail_layer_is_detected_and_confirmed(self):
        page = self.detail_page()
        task = task_with(page)
        self.assertTrue(task.in_stamina_item_detail(page))
        self.assertTrue(task.handle_stamina_recover(page))
        clicks = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual([c[1] for c in clicks], ["使用道具"])

    def test_fake_cap_is_ignored(self):
        """`15/15` 不能当专注上限：上限 15 → 差 0 → 该喝的时候不喝（事故根因②）。"""
        page = self.detail_page()
        task = task_with(page)
        # 顶栏 15/150 才是真值；开一局要 30 → 还差 15
        self.assertEqual(task.stamina_deficit(page), 15)

    def test_close_point_comes_from_ocr_x(self):
        """关页要认 OCR 的 X（1448,105），不是固定坐标（1459,136，偏 31px 点空）。"""
        page = self.detail_page()
        task = task_with(page)
        self.assertEqual(task.stamina_recover_close_point(page), (1448, 105))
        task.close_stamina_recover(page)
        self.assertEqual(task.actions[-1], ("click", 1448, 105))

    def test_gives_up_after_eight_ticks(self):
        """连续 8 拍还在恢复页 → 记 giveup 并主动关页（别等 STALL_LIMITS 的 60 拍）。"""
        page = self.detail_page()
        task = task_with(page)
        task._stamina_ticks = task.STAMINA_PAGE_MAX_TICKS
        self.assertTrue(task.handle_stamina_recover(page))
        self.assertGreater(task._stamina_giveup_at, 0)
        self.assertEqual(task.actions[-1], ("click", 1448, 105))


class StaminaRecoverHandler(unittest.TestCase):

    def test_uses_small_bottle_and_never_diamond(self):
        page = recover_page()
        task = task_with(page)                       # 107/150，还差 23
        task._stamina_need = 23
        self.assertTrue(task.handle_stamina_recover(page))
        clicks = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0][1], "使用")
        # 点的是「专注+30」那一行（cy≈455），不是钻石行（cy≈300）
        self.assertGreater(clicks[0][3], 400)
        self.assertLess(clicks[0][3], 500)

    def test_need_from_popup_overrides_floor(self):
        page = recover_page()
        task = task_with(page)
        task._stamina_need = 40                      # 「还差40」弹窗进来的 → +30 不够
        self.assertTrue(task.handle_stamina_recover(page))
        clicks = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual(clicks[0][1], "使用")
        self.assertGreater(clicks[0][3], 550)        # 大瓶那一行
        self.assertLess(clicks[0][3], 650)

    def test_closes_page_when_nothing_to_drink(self):
        page = recover_page(owned_small=0, owned_big=0)
        task = task_with(page)
        self.assertTrue(task.handle_stamina_recover(page))
        self.assertTrue(any(a[0] == "click" for a in task.actions))
        self.assertFalse(any(a[0] == "click_box" for a in task.actions))

    def test_not_this_page(self):
        texts = [box(600, 300, "训练剧本", 160, 40)]
        task = task_with(texts)
        self.assertFalse(task.handle_stamina_recover(texts))

    def test_short_popup_clicks_reply_not_cancel(self):
        texts = [box(380, 322, "训练所需专注还差25，是否进行回复呢？", 534, 31),
                 box(700, 470, "回复", 160, 50),
                 box(500, 470, "取消", 160, 50)]
        task = task_with(texts)
        self.assertTrue(task.handle_stamina_short(texts))
        clicks = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual(clicks[0][1], "回复")
        self.assertEqual(task._stamina_need, 25)

    def test_medicine_forbidden_short_popup_cancels_and_forces_rest(self):
        """「允许吃专注药」= 关：先点「取消」关弹窗 → 停任务 → 弹提示（2026-11-05）。"""
        texts = [box(380, 322, "训练所需专注还差25，是否进行回复呢？", 534, 31),
                 box(700, 470, "回复", 160, 50),
                 box(500, 470, "取消", 160, 50)]
        task = task_with(texts, {"自动恢复专注": False})
        self.assertTrue(task.handle_stamina_short(texts))
        clicks = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual([c[1] for c in clicks], ["取消"])  # 只点取消，绝不点「回复」
        self.assertFalse(hasattr(task, "_stamina_need"))   # 没记「还差N」→ 没打算喝
        self.assertTrue(task._abort_reason)                # run() 下一拍就退出

    def test_medicine_forbidden_recover_page_closes_without_drinking(self):
        """「允许吃专注药」= 关：恢复页（哪怕进了）也不喝，直接关页。"""
        page = recover_page()
        task = task_with(page, {"自动恢复专注": False})
        self.assertTrue(task.handle_stamina_recover(page))
        self.assertFalse(any(a[0] == "click_box" for a in task.actions))  # 没点「使用」
        self.assertTrue(any(a[0] == "click" for a in task.actions))       # 点了 X 关页


class TrainingScorePage(unittest.TestCase):
    """一局结束的「训练评分」弹层：必须读出个人评分（2026-11-04 实跑漏读）。

    原样帧（`logs/mcp/a0e2b1ea…/run.log` 20:53:21）：
        训练评分  7728  SS  点击屏幕继续
    旧代码只认「恭喜获得 / 训练奖励」标题 → 评分一次没记 → 队列永远停在 7096 →
    用户看到「7728 了还在跑」。
    """

    def page(self):
        return [box(600, 300, "训练评分", 200, 60),
                box(700, 400, "7728", 160, 70),
                box(760, 480, "SS", 90, 50),
                box(650, 700, "点击屏幕继续", 300, 50)]

    def make(self, texts, target=7400, captain="近江奏"):
        task = task_with(texts)
        task.config["多角色队列"] = True
        from src.tasks.training.queue import CaptainQueue
        task._queue = CaptainQueue(f"{captain}:{target}",
                                   path=os.path.join(tempfile.gettempdir(),
                                                     "_q_test.json"), resume=False)
        return task

    def test_reads_personal_score_and_rating(self):
        texts = self.page()
        task = self.make(texts)
        self.assertEqual(task.read_training_score(texts), (7728, "SS"))

    def test_final_score_is_recorded(self):
        """2026-11-20 口径：这一页就是最终得分页，读到最终值后写进 `_run_score`。"""
        texts = self.page()
        task = self.make(texts)
        self.assertTrue(task.handle_training_score(texts))
        self.assertEqual(task._run_score, 7728)
        self.assertFalse(getattr(task, "_round_done", False))
        self.assertEqual(task.actions[-1][0], "click_box")  # 但要推进翻页

    def test_reads_settled_value_not_first_frame(self):
        """第一拍是滚动值（7728），停 1 秒后重读最终值（8870）。"""
        first = self.page()
        final = [box(600, 300, "训练评分", 200, 60),
                 box(700, 400, "8870", 160, 70),
                 box(760, 480, "SS", 90, 50),
                 box(650, 700, "点击屏幕继续", 300, 50)]
        task = self.make(first)
        task._ocr_region = lambda *a, **k: final
        self.assertTrue(task.handle_training_score(first))
        self.assertEqual(task._run_score, 8870)
        self.assertTrue(any("本局评分：8870" in m for m in task.logs), task.logs)

    def test_under_target_keeps_round_going(self):
        texts = [box(600, 300, "训练评分", 200, 60),
                 box(700, 400, "6446", 160, 70),
                 box(650, 700, "点击屏幕继续", 300, 50)]
        task = self.make(texts, target=7400)
        self.assertTrue(task.handle_training_score(texts))
        self.assertEqual(task._run_score, 6446)

    def test_waits_one_second_before_click(self):
        """2026-11-20：点之前停 1 秒（等数字滚完）。"""
        task = self.make(self.page())
        calls = []
        task.sleep = lambda *a, **k: calls.append(a)
        self.assertTrue(task.handle_training_score(self.page()))
        self.assertEqual(calls, [(1.0,)])

    def test_other_pages_are_not_this_one(self):
        texts = [box(600, 300, "下一步", 200, 60)]
        task = self.make(texts)
        self.assertFalse(task.handle_training_score(texts))
        self.assertIsNone(task._run_score)


class SquadRatings(unittest.TestCase):
    def test_positions_paired_by_rating_anchor(self):
        """位置词在评级右边约 0.075w —— 按「位置找最近评分」会整列错位。"""
        texts = award_page()
        task = task_with(texts, size=(1307, 735))
        self.assertEqual(task.read_squad_ratings(texts),
                         {"中锋": 4822, "控卫": 3028, "分卫": 3025,
                          "小前": 2915, "大前": 2917})

    def test_glued_score_frames_are_parsed(self):
        """OCR 把评分和相邻文字黏在一起时也要读到 5 个位置。

        2026-11-14 实跑（logs/1114-230254_野上泷 第 1 局）：
            ... | 大前 | 控卫 | 分卫 | 小前 | 中锋 | 中锋5937 | 元 |
            7929箱 | 5813 | 5814 | 5735 | ...
        旧写法要求整框 `isdigit()`，`7929箱` 和 `中锋5937` 被丢掉
        → 只读到 3/5 个位置，队长（大前）评分读到 None，
        达标判断永远「未达标」（收账日志：「野上泷 评分 None」）。
        """
        W, H = 1548, 871

        def at(cx, cy, name, w=70, h=28):
            return box(int(cx * W - w / 2), int(cy * H - h / 2), name, w, h)

        texts = [at(0.294, 0.599, "中锋"), at(0.407, 0.599, "控卫"),
                 at(0.524, 0.599, "分卫"), at(0.638, 0.599, "小前"),
                 at(0.756, 0.599, "大前"),
                 at(0.219, 0.639, "7929箱"),      # 黏了后缀
                 at(0.336, 0.639, "5813"),
                 at(0.450, 0.639, "5814"),
                 at(0.565, 0.639, "5735"),
                 at(0.679, 0.639, "中锋5937"),    # 黏了位置词
                 at(0.80, 0.639, "672/3600"),     # 进度条，不是评分
                 at(0.222, 0.603, "A")]           # 评级字母
        task = task_with(texts, size=(W, H))
        got = task.read_squad_ratings(texts)
        self.assertEqual(len(got), 5, f"应该读到 5 个位置，实际 {got}")
        self.assertEqual(sorted(got.values()), [5735, 5813, 5814, 5937, 7929])
        self.assertNotIn(672, got.values(), "「672/3600」不是评分")

    def test_captain_score_lookup_uses_position(self):
        texts = award_page()
        task = task_with(texts, size=(1307, 735))
        task.config["多角色队列"] = True
        task.config["队长队列"] = "野上泷:7500"
        task._queue = CaptainQueue("野上泷:7500", path=os.path.join(
            tempfile.mkdtemp(), "q.json"), resume=False)
        self.assertEqual(task._queue.position, "大前")
        self.assertEqual(task.note_run_score(texts), 2917)
        self.assertFalse(task._queue.target_met(task._run_score))

    def test_award_points_are_not_read_as_scores(self):
        """「训练奖励」那一行的 300/500/800/1000 不能被当球员评分。"""
        texts = award_page() + [box(388, 330, "300", 45, 20),
                                box(690, 327, "1000", 62, 28)]
        task = task_with(texts, size=(1307, 735))
        values = task.read_squad_ratings(texts).values()
        self.assertNotIn(300, values)
        self.assertNotIn(1000, values)
        self.assertEqual(len(values), 5)


class BreathDifficultyPage(unittest.TestCase):
    """「选择憋气难度」：必须先点卡再确定（只点确定会空转，2026-11-04 实跑 1019 次）。"""

    def page(self):
        return [
            box(676, 136, "选择憋气难度", 195, 43),
            box(600, 336, "进阶", 66, 37),
            box(637, 391, "2分钟", 78, 35),
            box(634, 471, "随机获得属性或零用钱", 287, 37),
            box(830, 336, "进阶", 66, 37),
            box(867, 391, "3分钟", 78, 35),
            box(864, 471, "有机会获得大量零用钱", 287, 37),
            box(731, 765, "确定", 81, 47),
        ]

    def test_picks_longest_card_then_confirm(self):
        page = self.page()
        task = task_with(page)
        self.assertTrue(task.handle_breath_difficulty(page))
        clicks = [a for a in task.actions if a[0] == "click"]
        self.assertEqual(len(clicks), 1)
        # 点到的是**卡身空白区** —— 不是「3分钟」标签，也不是说明文字。
        # 2026-11-14 实跑教训：点说明文字中心 (406,490) 被游戏吃掉，
        # 「确定」一直是灰的、连点 4 次没反应。要落在
        # 「标签下沿 ~ 说明文字上沿」之间（纯卡身空白）。
        # 这不是「3分钟」标签本身 —— 标签上点不下去（2026-11-04 实机）。
        self.assertGreater(clicks[0][1], 850)
        self.assertGreaterEqual(clicks[0][2], 391 + 35)   # 标签下沿
        self.assertLessEqual(clicks[0][2], 471)           # 说明文字上沿
        task.actions.clear()
        self.assertTrue(task.handle_breath_difficulty(page))
        clicked = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual(clicked[0][1], "确定")          # 再确定

    def test_retries_when_card_click_was_skipped(self):
        """选卡那一下被「让路」吞掉时，下一拍必须**重试选卡**。

        2026-11-14 实跑（logs/1114-230254_野上泷/debug/1114-230512_noresp3s）：
        click(406,451) 被闸门拦下（clicks.jsonl 里没有记录），
        但 `_breath_picked` 照样设成 True → 之后每拍只点「确定」，
        卡片永远是灰的 → 连点到看门狗接管、整页推不动。
        """
        page = self.page()
        task = task_with(page)
        real_click = task.click

        def skip_first(x=-1, y=-1, **kw):
            kw["skip"] = True
            return real_click(x, y, **kw)

        task.click = skip_first            # 第一次点击（选卡）被吞
        self.assertTrue(task.handle_breath_difficulty(page))
        self.assertFalse(getattr(task, "_breath_picked", False),
                         "点击被跳过时不能把「已选卡」标成 True")
        task.click = real_click              # 第二次正常
        task.actions.clear()
        self.assertTrue(task.handle_breath_difficulty(page))
        clicks = [a for a in task.actions if a[0] == "click"]
        self.assertEqual(len(clicks), 1, "应该**重新选卡**，不是直接点确定")
        self.assertTrue(getattr(task, "_breath_picked", False))

    def page_with_stamina(self, stamina="16/130"):
        """2026-11-25 实跑帧（logs/1125-093234_野上泷/debug/1125-094543_nochange3）。

        三张卡各带体力消耗：4分钟⚡20（红，买不起）、2分钟⚡0、3分钟⚡10；
        顶栏体力 16/130、零花钱 441。中间那张的「0」OCR 读不出来。
        """
        return [
            box(35, 16, "第5周", 94, 40),
            box(163, 18, "星期四", 85, 35),
            box(305, 13, stamina, 172, 45),
            box(848, 18, "441", 58, 35),
            box(676, 136, "选择憋气难度", 192, 39),
            box(372, 336, "进阶", 68, 37),
            box(269, 391, "4分钟", 76, 35),
            box(252, 477, "更高概率获得大量零用钱", 309, 27),
            box(361, 567, "B 20", 90, 54),
            box(742, 335, "进阶", 69, 41),
            box(635, 387, "2分钟", 83, 40),
            box(635, 474, "随机获得属性或零用钱", 284, 32),
            box(1114, 336, "进阶", 68, 37),
            box(1008, 387, "3分钟", 82, 40),
            box(1006, 474, "有机会获得大量零用钱", 286, 32),
            box(1105, 567, "B10", 88, 54),
            box(732, 769, "确定", 77, 41),
        ]

    def test_skips_card_that_costs_more_stamina(self):
        """体力 16 时不能选 ⚡20 的 4 分钟档：点了游戏只弹「请选择一个选项」。

        2026-11-25 实跑：handler 只按「时长最长」选 4 分钟档，
        「确定」连点无效 → noresp3s / nochange3 看门狗接管。
        """
        page = self.page_with_stamina()
        task = task_with(page)
        self.assertTrue(task.handle_breath_difficulty(page))
        clicks = [a for a in task.actions if a[0] == "click"]
        self.assertEqual(len(clicks), 1)
        self.assertGreater(clicks[0][1], 900, "应该选右边的 3 分钟档（⚡10）")
        self.assertTrue(any("体力 16" in msg for msg in task.logs))

    def test_falls_back_to_cheapest_when_stamina_is_low(self):
        """体力连 ⚡10 都不够时，落到 ⚡0 的 2 分钟档。"""
        page = self.page_with_stamina("5/130")
        task = task_with(page)
        self.assertTrue(task.handle_breath_difficulty(page))
        clicks = [a for a in task.actions if a[0] == "click"]
        self.assertEqual(len(clicks), 1)
        self.assertTrue(700 < clicks[0][1] < 850, "应该落到中间的 2 分钟档（⚡0）")

    def test_lowers_tier_after_two_dead_confirms(self):
        """点卡后确定连点两次没反应 → 换下一档，不是重选同一张。

        2026-11-25 实跑就是这个死循环：重选同一张 ⚡20 的卡，
        每次都被游戏拒绝，直到看门狗接管。
        """
        page = self.page_with_stamina()
        task = task_with(page)
        self.assertTrue(task.handle_breath_difficulty(page))
        task._breath_confirms = 2            # 之后连点两次「确定」都没翻页
        task._breath_picked = True
        self.assertTrue(task.handle_breath_difficulty(page))   # 这一拍只重置状态
        self.assertEqual(task._breath_rank, 1)
        task.actions.clear()
        self.assertTrue(task.handle_breath_difficulty(page))   # 这一拍重新选卡
        clicks = [a for a in task.actions if a[0] == "click"]
        self.assertEqual(len(clicks), 1, "应该重新选卡（降一档），不是点确定")
        self.assertTrue(700 < clicks[0][1] < 850, "降档后落到 2 分钟档（⚡0）")

    def test_not_this_page(self):
        texts = [box(600, 300, "训练剧本", 160, 40)]
        task = task_with(texts)
        self.assertFalse(task.handle_breath_difficulty(texts))

    def test_stall_limit_declared(self):
        """这页要进 STALL_LIMITS：指纹每拍都变的时候，只能靠「连续命中次数」兜底。"""
        from src.tasks.training.handlers import STALL_LIMITS
        self.assertIn("handle_breath_difficulty", STALL_LIMITS)


class AttrPanelReading(unittest.TestCase):
    """六维面板读数：**超上限**的数字不许进 `info["attrs"]`。

    事故（2026-11-04）：旧判据取「这一行第一个 ≥100 的数字」，实跑读出
    `体能 8116`（真值 1016）、`意识 3114`（真值 1114）→ 监控页六维和「分数预测」
    从 9k 跳到 1w4。属性是**合并**语义，错值会一直挂到下次进休息室。
    """

    def panel(self, tinted=False):
        """底部面板两块三行（区域 attr_panel = 0.03~0.62w / 0.78~1.00h）。

        坐标取实测帧 `logs/unknown/1104-102229_unknown_s25.txt` 的相对位置，
        右列整体收进 0.62w 以内（区域只用来缩小搜索范围，见 Defs 的设计原则 2）。
        """
        body = "8116/1680" if tinted else "B1016/1680"
        mind = "3114/1689" if tinted else "C1114/1689"
        return [
            box(179, 688, "投射", 74, 44), box(266, 688, "B1488/1680", 190, 45),
            box(610, 682, "体能", 100, 47), box(700, 682, body, 190, 45),
            box(178, 749, "弹跳", 74, 44), box(266, 749, "C1277/1550", 190, 45),
            box(610, 733, "防守", 100, 45), box(700, 733, "C1282/1709", 190, 45),
            box(178, 794, "运控", 74, 44), box(266, 794, "C+1323/1565", 190, 45),
            box(610, 795, "意识", 100, 45), box(700, 795, mind, 190, 45),
        ]

    def test_value_above_cap_is_rejected(self):
        from src.tasks.training.parsers import attr_value_from
        self.assertIsNone(attr_value_from("8116/1680"))
        self.assertIsNone(attr_value_from("3114/1689"))
        self.assertIsNone(attr_value_from("3488/1680"))      # 等级粘在前 → 也超上限
        self.assertIsNone(attr_value_from("8116", cap=1680))  # 没写上限时用记住的上限

    def test_normal_readings(self):
        from src.tasks.training.parsers import attr_value_from
        self.assertEqual(attr_value_from("投射B1488/1680"), 1488)
        self.assertEqual(attr_value_from("C+1323/1565"), 1323)
        self.assertEqual(attr_value_from("体能B 1464/+16"), 1464)
        self.assertIsNone(attr_value_from("+16"))
        self.assertIsNone(attr_value_from(""))

    def test_clean_frame_reads_all_six(self):
        page = self.panel()
        task = task_with(page, size=(1549, 871))
        self.assertEqual(task.read_attribute_values(page),
                         {"投射": 1488, "体能": 1016, "弹跳": 1277,
                          "防守": 1282, "运控": 1323, "意识": 1114})

    def test_left_cell_does_not_swallow_right_column_numbers(self):
        """左列那一格不能把右列的数字算成自己的（2026-11-04 实测把上限 1689 当成当前值）。"""
        page = [
            box(434, 796, "运控", 66, 41),       # 左列，本帧没读到自己的值
            box(1100, 807, "1689", 60, 30),       # 右列的数字，同一行带
        ]
        task = task_with(page, size=(1549, 871))
        self.assertNotIn("运控", task.read_attribute_values(page))

    def test_subject_list_label_is_not_taken_as_panel_name(self):
        """右侧科目按钮上的同名文字不能被当成本格名字（2026-11-19 实测）。

        那一帧面板里是 `意识C280/1600`，右侧科目按钮上另有一个整词 `意识`，
        而且 OCR 结果里排在前面。旧逻辑先命中按钮那个 → 去别的行找值 → 整格丢。
        """
        page = [
            box(1134, 697, "意识", 88, 53),          # 科目按钮上的同名文字
            box(630, 789, "意识C280/1600", 291, 58),  # 面板这一行（名字和数值黏连）
            box(177, 673, "投射", 75, 44), box(308, 675, "710/2238", 157, 45),
        ]
        task = task_with(page, size=(1549, 871))
        self.assertEqual(task.read_attribute_values(page).get("意识"), 280)

    def test_glitched_frame_leaves_that_cell_alone(self):
        page = self.panel(tinted=True)
        task = task_with(page, size=(1549, 871))
        values = task.read_attribute_values(page)
        self.assertNotIn("体能", values)      # 8116 > 1680 → 这一格不更新
        self.assertNotIn("意识", values)      # 3114 > 1689 → 同上
        self.assertEqual(values["投射"], 1488)
        # info["attrs"] 是合并语义：这一格保留上一次的值，不会掉成「—」
        task.info["attrs"] = {"体能": 1016, "意识": 1114}
        merged = task.refresh_attr_info(page)
        self.assertEqual(merged["体能"], 1016)
        self.assertEqual(merged["意识"], 1114)


class StaminaRecoverRouting(unittest.TestCase):
    """「专注恢复」页必须被认成**自己的页面**，不能落进事件三选一。

    2026-11-15 用户实跑（v0.1.20，`run.log` 20:13:24–20:15:58）：
    这一页没登记锚点 → 分类器认成 event_choice → 路由把
    `handle_event_choice` 提到最前 → 它把「钻石购买」当作竖排三选一的第一个，
    每秒点一次标题框 (656,226)，连点 112 下，直到用户手动关窗口。
    """

    def test_page_is_classified_as_stamina_recover(self):
        from src.tasks.training.pages import classify_page
        page = recover_page()
        task = task_with(page)
        found, candidates = classify_page(task, page)
        self.assertEqual(found, "stamina_recover")
        self.assertNotEqual(candidates[0], "event_choice")

    def test_router_sends_this_page_to_stamina_handler(self):
        from src.tasks.training import page_handlers
        self.assertEqual(page_handlers.handlers_for("stamina_recover"),
                         ("handle_stamina_recover",))

    def test_event_choice_backs_off(self):
        from src.tasks.training import page_signatures
        page = recover_page()
        task = task_with(page)
        self.assertTrue(page_signatures.page_has(task, page, "stamina_recover"))
        self.assertTrue(page_signatures.page_is_other(task, page, "event_choice"))

    def test_plain_event_page_is_not_excluded(self):
        """普通事件二选一不能被 exclusive_page 误伤（误伤 = 没人认领 → 点屏幕中央）。"""
        from src.tasks.training import page_signatures
        page = [box(500, 400, "放弃按摩", 160, 50),
                box(500, 520, "安心按摩", 160, 50)]
        task = task_with(page)
        self.assertFalse(page_signatures.page_is_other(task, page, "event_choice"))


class PageSignatureSemantics(unittest.TestCase):
    """签名表：组内是「且」，所以「任一命中」必须拆成一词一组（`any_of`）。

    2026-11-15 事故：`exclusive_page` / `gacha_page` / `match_event` /
    `weekly_summary` 第一组都是从旧代码 `match_text(元组)`（或）搬过来的，
    裹成一整组后变成「所有词同时出现」→ 永不命中。
    """

    def test_any_of_matches_single_word(self):
        from src.tasks.training import page_signatures
        page = [box(400, 300, "小游戏强化", 100, 30)]
        task = task_with(page)
        self.assertIn("gacha_page", page_signatures.matched_signatures(task, page))

    def test_event_effect_word_is_not_gacha_page(self):
        """事件选项的效果文字「器材加成-100熟练度」不能算扭蛋页（2026-11-15 回放）。

        旧代码的扭蛋签名里写着 `熟练度。`（带句号），搬进签名表时丢了句号 →
        16 帧事件页被 `handle_event_choice` 以「这是别的页面」排掉，没人认领。
        """
        from src.tasks.training import page_signatures
        page = [box(500, 600, "器材加成-100熟练度", 220, 30)]
        task = task_with(page)
        self.assertNotIn("gacha_page", page_signatures.matched_signatures(task, page))

    def test_match_event_needs_only_one_word(self):
        from src.tasks.training import page_signatures
        page = [box(400, 300, "暂时退出", 100, 30)]
        task = task_with(page)
        self.assertIn("match_event", page_signatures.matched_signatures(task, page))

    def test_weekly_summary_first_group_is_or(self):
        from src.tasks.training import page_signatures
        page = [box(400, 300, "本周总结", 100, 30)]
        task = task_with(page)
        self.assertIn("weekly_summary", page_signatures.matched_signatures(task, page))


if __name__ == "__main__":
    unittest.main(verbosity=2)
