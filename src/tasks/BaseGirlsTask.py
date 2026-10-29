"""篮球少女任务基类：文字优先的界面操作。

这个游戏的 UI 特点：几乎所有可交互元素都有中文大字标签（地点、训练科目、
跳过比赛、确认…），而 3D 场景反而没有稳定纹理。所以这里统一走 OCR 找文字
再点文字框，只在「大按钮」这种无文字目标上退化成区域点击。
"""

import random
import re
import time

from ok import BaseTask, og

from src.tasks.ui import Defs


class BaseGirlsTask(BaseTask):

    def apply_user_idle_wait(self, seconds):
        """把「空闲等待(秒)」传给输入后端（GirlsInteraction），0 = 不等（一直抢）。

        语义：用户最后一次键鼠操作之后，要静默这么多秒脚本才继续动手。这是
        "用户操作优先"的唯一开关——脚本要抢前台、要动真实鼠标，就必须等用户
        停手；等待没有上限，不会因为等太久就去抢。

        为什么要从任务配置走：interaction 是框架全局单例，用户在任务页改的值
        只能通过这里推下去；其它输入后端没有 user_idle_wait 属性时静默跳过。
        """
        try:
            interaction = og.device_manager.interaction
        except Exception as e:                      # noqa: BLE001
            self.log_debug(f"读不到 interaction，跳过空闲等待配置: {e}")
            return None
        if not hasattr(interaction, "user_idle_wait"):
            return interaction
        try:
            value = max(0.0, float(seconds))
        except (TypeError, ValueError) as e:
            self.log_warning(f"空闲等待配置无效（{seconds}）：{e}")
            return interaction
        # TriggerTask 每拍都会调到这里，只在值变化时打日志，别刷屏
        if interaction.user_idle_wait != value:
            interaction.user_idle_wait = value
            if value <= 0:
                self.log_info("空闲等待：已关闭（用户操作时也会抢前台）")
            else:
                self.log_info(f"空闲等待：你停手 {value:.1f} 秒后脚本才继续挂机")
        return interaction

    # ---------- 文字 ----------

    def _ocr_region(self, region_name=None, box=None):
        if box is None and region_name:
            box = self.box_of_screen(*Defs.region(region_name))
        try:
            return self.ocr(box=box) or []
        except Exception as e:
            self.log_debug(f"ocr failed: {e}")
            return []

    # 大厅子菜单页（强化 / 招募 / 卡池…）：玩家自己点进去的界面，**任务不该动它们**。
    # 2026-10-28 用户反馈：跑着任务时在大厅点进「强化」，脚本一直替他点升级卡；
    # 招募页被 handle_story_arrow 当成剧情页点了一下。
    LOBBY_SUBPAGES = ("强化", "招募", "卡池", "商店兑换", "飞跃计划",
                      "问卷", "新生集训", "冠军之路", "社团GO")

    def is_lobby_subpage(self, texts):
        """当前画面是不是大厅里玩家自己开的子菜单（任务不管这种页面）。"""
        names = "".join(t.name for t in texts)
        # 大厅主界面本身也有「招募 / 飞跃计划 / 社团GO」这些入口文字，
        # 但它的标志是同时有「赛事」和「训练」两个大按钮 —— 那种页面必须放行，
        # 否则任务连训练都开不了（2026-10-28 实跑踩到：任务在大厅空转 366 拍）。
        if "赛事" in names and "训练" in names:
            return False
        # 有弹窗/对话框时一律放行（弹窗盖在大厅上，背景那些入口文字还在，
        # 但这时候该由 handle_resume_training / handle_popup 处理）
        if any(k in names for k in ("训练确认", "继续训练", "删档",
                                    "确定", "确认", "关闭", "下一步")):
            return False
        hits = [k for k in self.LOBBY_SUBPAGES if k in names]
        if not hits:
            return False
        # 训练/比赛流程的关键标记出现时，说明这不是纯子菜单页（比如强化页里的
        # 器材选择、比赛里的结算），交回给正常 handler。
        # 2026-10-29 实跑：训练剧本页右侧有「强化/图鉴/成就」页签，整页被判成
        # 大厅子菜单 → 从大厅点「训练」进来后空转到卡死（「启程」明明在屏幕上）。
        flow_marks = ("失败率", "决定", "休息", "出行", "下一步", "VS", "跳过",
                      "训练剧本", "训练核心", "启程", "备战")
        if any(k in names for k in flow_marks):
            return False
        return True

    @staticmethod
    def exact_text(texts, names):
        """整词匹配：框里的文字**完全等于** names 之一才返回。

        为什么要它：match_text 是包含匹配，还会在整段拼接命中时返回「最长的框」，
        于是「找防守」能返回「失败率0%」、「找决定」能命中正文里的「…决定通过…」。
        按钮类判据一律用这个。

        2026-10-27 起放在基类：QuickMatchTask 组合的是 MatchHandlers，
        不含训练流程 mixin，之前 GUI 里启用「快速比赛」直接报
        `'QuickMatchTask' object has no attribute 'exact_text'`。
        """
        for name in names:
            for t in texts:
                if t.name.strip() == name:
                    return t
        return None

    def dump_unknown(self, texts, tag):
        """把不认识的界面存证：截图 + OCR 文本（logs/ 已被 .gitignore 忽略）。

        同样放在基类：TrainingTask 和 QuickMatchTask 都要用。
        """
        try:
            import os
            import cv2
            from src.tasks.training.constants import JPEG_PARAMS
            root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            folder = os.path.join(root, "logs", "unknown")
            os.makedirs(folder, exist_ok=True)
            name = f"{time.strftime('%m%d-%H%M%S')}_{tag}_s{getattr(self, '_steps', 0)}"
            frame = self.frame
            if frame is not None:
                cv2.imwrite(os.path.join(folder, name + ".jpg"), frame, JPEG_PARAMS)
            with open(os.path.join(folder, name + ".txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(
                    f"{t.name}\t({t.x},{t.y},{t.width},{t.height})" for t in texts))
            self.log_info(f"存证 logs/unknown/{name}.jpg")
        except Exception as e:
            self.log_debug(f"存证失败: {type(e).__name__}: {e}")

    @staticmethod
    def match_text(texts, names):
        """在一批 OCR 结果里找名字匹配的框。names 支持字符串 / 元组 / 正则。"""
        if isinstance(names, str) or hasattr(names, "search"):
            names = (names,)
        for name in names:
            if hasattr(name, "search"):
                for t in texts:
                    if name.search(t.name):
                        return t
                continue
            for t in texts:
                if name in t.name:
                    return t
        # 兜底：整段拼接后匹配，处理「跳 过 比 赛」被拆成多个框
        joined = "".join(t.name for t in texts)
        for name in names:
            if not hasattr(name, "search") and name in joined and texts:
                return max(texts, key=lambda t: len(t.name))
        return None

    @staticmethod
    def is_label(name):
        """浮层/角标文字（「划船比赛！」「一定能赚!」这种），只是提示，不是按钮。

        2026-10-29 从 TrainingFlowMixin 挪到基类：match.py 的选卡判据也要用它，
        而 QuickMatchTask 不继承 TrainingFlowMixin（实机报 AttributeError）。
        """
        s = (name or "").strip()
        if not s:
            return False
        return s.endswith("！") or s.endswith("!") or "加成" in s or "比赛！" in s

    @staticmethod
    def is_decor(name):
        """这段文字是不是纯视觉标签（折扣！/限时特惠/UP! …），不是可点元素。

        判据很克制：去掉标点后**以装饰词开头**且整串很短（≤6 字）才算，
        这样「参加社团活动」这类真选项不会被误杀。
        """
        s = (name or "").replace(" ", "").replace("　", "")
        s = s.strip("！!~～·.。,，")
        if not s or len(s) > 6:
            return False
        up = s.upper()
        return any(up.startswith(w.upper()) for w in Defs.DECOR_WORDS)

    def find_text(self, names, region_name=None, box=None):
        """找文字并返回**真实文本框**。

        注意：不能直接用 `ocr(box=区域)` —— 那样返回的是「区域本身」这个框
        （实测 x=576,y=886,w=768,h=173），点它的中心会落在按钮外面。
        正确做法：全屏 OCR，再按区域过滤文本框。
        """
        texts = self._ocr_region()
        if region_name is not None:
            texts = self.texts_in_region(texts, region_name)
        return self.match_text(texts, names)

    def texts_in_region(self, texts, region_name):
        """把整帧 OCR 结果过滤到某个区域内（按文本框中心点）。"""
        x1, y1, x2, y2 = Defs.region(region_name)
        picked = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                picked.append(t)
        return picked

    def click_text(self, names, region_name=None, box=None, after_sleep=0.6, must=True):
        target = self.find_text(names, region_name, box)
        if target is None:
            if must:
                self.log_warning(f"未找到文字: {names}")
            return False
        self.click_box(target, after_sleep=after_sleep)
        self.log_info(f"点击「{target.name}」")
        return True

    def click_and_expect(self, x, y, expect, timeout=5.0, poll=0.5):
        """点一下，然后等「下一页该出现的文字」出现。

        点击丢失是这个游戏的常态（启程就丢过），所以每次点击都要有反馈校验。
        不能用像素差判断——3D 大厅一直在动，随便两帧都不一样。
        返回 True 表示界面确实变了。
        """
        self.click(int(x), int(y))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sleep(poll)
            if self.match_text(self._ocr_region(), expect) is not None:
                return True
        return False

    def click_text_and_expect(self, texts, names, expect, region_name=None, timeout=8.0):
        """按当前帧的 OCR 文本框点击，再等下一页文字出现。

        比写死相对坐标稳：同一个「下一步」按钮在「队伍未满 / 已满」两种状态下
        位置会差 40~50px（实训 2026-10-26），固定坐标会点到按钮上方的属性栏。
        """
        pool = self.texts_in_region(texts, region_name) if region_name else texts
        target = self.match_text(pool, names)
        if target is None:
            return False
        x = int(target.x + target.width / 2)
        y = int(target.y + target.height / 2)
        return self.click_and_expect(x, y, expect, timeout=timeout)

    # ---------- 顶栏状态 ----------

    def top_bar(self):
        """解析顶栏：周数 / 星期几 / 体力 / 金币。解析不到的字段为 None。

        体力**只在含「/」的那个文本框里找**：OCR 常把「第1周」的「1」和体力粘在一起
        （曾出现 3 + 101/115 → 31101），整段拼接再匹配必错。

        2026-10-26 补：还会出现 `369/115`（周数 3 粘在 69 前面）。做法是把前面的
        数字逐位去掉，取第一个 `0 < cur <= max` 的组合；多个候选时取最右边那个
        （体力条在周数右边）。
        """
        texts = self._ocr_region("top_bar")
        joined = " ".join(t.name for t in texts)
        state = {"week": None, "day": None, "stamina": None, "stamina_max": None, "coin": None}

        if m := Defs.WEEK_RE.search(joined.replace(" ", "")):
            state["week"] = int(m.group(1))
        if m := Defs.DAY_RE.search(joined.replace(" ", "")):
            state["day"] = "日" if m.group(1) == "天" else m.group(1)

        best = None
        for t in texts:
            name = t.name.replace(" ", "")
            if "/" not in name:
                continue
            if not (m := Defs.STAMINA_RE.search(name)):
                continue
            digits, mx = m.group(1), int(m.group(2))
            cur = int(digits)
            if cur > mx:
                # 逐位去掉粘连的前缀：369/115 → 69/115
                for i in range(1, len(digits)):
                    cand = int(digits[i:])
                    if 0 < cand <= mx:
                        cur = cand
                        break
            if 0 <= cur <= mx:
                # 取最右边的候选（体力在周数右边；金币没有斜杠）
                if best is None or t.x >= best[0]:
                    best = (t.x, cur, mx)
        if best is not None:
            state["stamina"], state["stamina_max"] = best[1], best[2]
        return state

    # ---------- 流程按钮（相对坐标，随窗口缩放） ----------

    def flow_point(self, key):
        """FLOW_COORDS 里的相对坐标 -> 当前客户区像素。"""
        r = Defs.FLOW_COORDS[key]
        return int(self.width_of_screen(r[0])), int(self.height_of_screen(r[1]))

    def flow_click(self, key, after_sleep=1.5):
        x, y = self.flow_point(key)
        self.click(x, y, after_sleep=after_sleep)
        return True

    # ---------- 大按钮 / 区域点击 ----------

    def click_region(self, region_name, after_sleep=0.8):
        box = self.box_of_screen(*Defs.region(region_name))
        x = box.x + box.width * random.uniform(0.3, 0.7)
        y = box.y + box.height * random.uniform(0.3, 0.7)
        self.click(int(x), int(y), after_sleep=after_sleep)
        return True

    def click_main_action(self, after_sleep=1.5):
        """右下角主按钮（训练 / VS 比赛）。没有文字标签，只能按区域点。"""
        self.log_info("点击右下角主按钮")
        return self.click_region("main_action", after_sleep=after_sleep)

    # ---------- 通用 ----------

    def is_loading(self, texts=None):
        """加载/过场：出现「加载」字样，或**整屏只剩一个百分比**（加载进度）。

        坑：加强训练界面有「100%」「80%」两个成功率，绝不能当成加载进度，
        否则每个决策周期都 sleep(1) 空转、没有任何日志（实训 2026-10-25 卡了 3 分钟）。
        """
        if texts is None:
            texts = self._ocr_region()
        if self.match_text(texts, "加载") is not None:
            return True
        if len(texts) <= 2:
            for t in texts:
                if re.fullmatch(r"\d{1,3}%", t.name.strip()):
                    return True
        return False

    def dismiss_popup(self):
        """关掉公告/领奖/月卡这类弹窗。返回是否处理了。"""
        if self.click_text(Defs.POPUP_TEXTS, "dialog", after_sleep=0.8, must=False):
            return True
        return False

    def in_weekend_map(self):
        return bool(self.find_text(Defs.WEEKEND_PLACES[:2], "weekend_map"))

    def in_prepare_page(self):
        return bool(self.find_text(("跳过比赛", "前往比赛"), "result_page"))

    def in_result_page(self):
        return bool(self.find_text(("比赛胜利", "比赛失败", "获得", "奖励"), "result_page"))
