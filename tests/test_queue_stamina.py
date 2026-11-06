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
        task = task_with(page, {"恢复精力下限": 45})
        # 顶栏 15/150 才是真值 → 差 45-15 = 30
        self.assertEqual(task.stamina_deficit(page), 30)

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
        task = task_with(page, {"恢复精力下限": 130})     # 107 → 差 23
        self.assertTrue(task.handle_stamina_recover(page))
        clicks = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0][1], "使用")
        # 点的是「专注+30」那一行（cy≈455），不是钻石行（cy≈300）
        self.assertGreater(clicks[0][3], 400)
        self.assertLess(clicks[0][3], 500)

    def test_need_from_popup_overrides_floor(self):
        page = recover_page()
        task = task_with(page, {"恢复精力下限": 10})
        task._stamina_need = 40                      # 「还差40」弹窗进来的 → +30 不够
        self.assertTrue(task.handle_stamina_recover(page))
        clicks = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual(clicks[0][1], "使用")
        self.assertGreater(clicks[0][3], 550)        # 大瓶那一行
        self.assertLess(clicks[0][3], 650)

    def test_closes_page_when_nothing_to_drink(self):
        page = recover_page(owned_small=0, owned_big=0)
        task = task_with(page, {"恢复精力下限": 130})
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
        task = task_with(texts, {"自动恢复精力": False})
        self.assertTrue(task.handle_stamina_short(texts))
        clicks = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual([c[1] for c in clicks], ["取消"])  # 只点取消，绝不点「回复」
        self.assertFalse(hasattr(task, "_stamina_need"))   # 没记「还差N」→ 没打算喝
        self.assertTrue(task._abort_reason)                # run() 下一拍就退出

    def test_medicine_forbidden_recover_page_closes_without_drinking(self):
        """「允许吃专注药」= 关：恢复页（哪怕进了）也不喝，直接关页。"""
        page = recover_page()
        task = task_with(page, {"自动恢复精力": False, "恢复精力下限": 130})
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

    def test_met_score_page_does_not_record(self):
        """2026-11-06 口径：训练评分弹层的分数**不入账**（只参考）。

        判分点 = 注册球员页的球员评分（含累积加成，同局实测弹层 8026 vs
        注册球员 8380）；弹层先到先得会短路注册球员页，导致达标了还白跑。
        """
        texts = self.page()
        task = self.make(texts)
        self.assertTrue(task.handle_training_score(texts))
        self.assertIsNone(task._run_score)         # 不入账
        self.assertFalse(getattr(task, "_round_done", False))
        self.assertEqual(task.actions[-1][0], "click_box")  # 但要推进翻页

    def test_under_target_keeps_round_going(self):
        texts = [box(600, 300, "训练评分", 200, 60),
                 box(700, 400, "6446", 160, 70),
                 box(650, 700, "点击屏幕继续", 300, 50)]
        task = self.make(texts, target=7400)
        self.assertTrue(task.handle_training_score(texts))
        self.assertIsNone(task._run_score)         # 弹层分数一律不入账

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
        # 点到的是**卡身中部**（3分钟那张卡下面那行「有机会获得大量零用钱」），
        # 不是「3分钟」标签本身 —— 标签上点不下去（2026-11-04 实机）。
        self.assertGreater(clicks[0][1], 850)
        self.assertGreater(clicks[0][2], 460)
        task.actions.clear()
        self.assertTrue(task.handle_breath_difficulty(page))
        clicked = [a for a in task.actions if a[0] == "click_box"]
        self.assertEqual(clicked[0][1], "确定")          # 再确定

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
