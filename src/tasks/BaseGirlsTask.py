"""篮球少女任务基类：文字优先的界面操作。

这个游戏的 UI 特点：几乎所有可交互元素都有中文大字标签（地点、训练科目、
跳过比赛、确认…），而 3D 场景反而没有稳定纹理。所以这里统一走 OCR 找文字
再点文字框，只在「大按钮」这种无文字目标上退化成区域点击。
"""

import random
import re
import time

from ok import BaseTask, og

from src.tasks.attempt import AttemptSupport
from src.tasks.ui import Defs


class BaseGirlsTask(AttemptSupport, BaseTask):

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

    # ---------- MCP / 基线 ----------

    def is_lobby(self, texts=None):
        """当前画面是不是大厅主界面。

        判据复用 handle_lobby 的入口文案（社团 / 社团GO / 开学纳新祭 / 转一转 / 冠军之路），
        再排除几个已知的非大厅页面。

        2026-10-30 修正：旧判据是「同时出现赛事和训练」，但它是**子串**匹配——
        「训练加速&弹跳UP！」这类 banner 会命中「训练」，「队伍调整页」也有
        「赛事/训练」导航，于是队伍页被当成大厅，reset 直接跳过恢复、
        探针把点击打在队伍页上（实跑日志 logs/mcp/3766817a…）。
        """
        texts = texts or []
        names = "".join(t.name for t in texts)
        if not any(k in names for k in self.LOBBY_MARKS):
            return False
        return not any(k in names for k in self.NOT_LOBBY_MARKS)

    def looks_like_match(self, texts=None):
        """当前画面像比赛/小游戏吗（这类界面不能盲点返回）。

        强标记（暂停/战术板）单个就够；VS/比分 太容易出现在队伍对比、
        赛事列表里，必须两个同时出现才判定。2026-10-30 实跑：队伍调整页
        被单个弱标记误判成比赛，导致回大厅直接中止。
        """
        texts = texts or []
        names = "".join(t.name for t in texts)
        if any(k in names for k in self.MATCH_STRONG_MARKS):
            return True
        return all(k in names for k in self.MATCH_WEAK_MARKS)

    def exit_page(self, after_sleep=0.45):
        """点右上角的返回箭头退出当前页面。

        坐标来自 2026-10-26 实测帧（2418x1360）：返回箭头是顶栏最右边那个
        「↩」按钮，按钮中心约 (0.945w, 0.040h)。旧值 (0.958, 0.060) 落在按钮
        右下角的圆角外，点十几次都不关页面（扭蛋页实测）。
        坑：左上角 (70,55) 那个按钮在训练日会打开「训练计划」日历，不是返回。

        2026-10-30 从 TrainingFlowMixin 挪到基类：MCP 的 reset_to_start 需要它，
        而 QuickMatchTask 不继承 TrainingFlowMixin。
        """
        self.click(int(self.width * 0.945), int(self.height * 0.040), after_sleep=after_sleep)

    # 退出当前页面可控的控件文案（OCR 精确匹配，且必须落在屏幕顶部）。
    # 后半截是箭头图标被 OCR 认错的结果：2026-10-30 实测「选择队友」页左上角的
    # 返回图标识别成「《」，只写「×」永远匹配不上。
    EXIT_TEXTS = ("关闭", "返回", "返回大厅", "放弃", "取消", "退出",
                  "上一页", "上一步", "×", "✕", "X", "x",
                  "《", "く", "<", "〈", "‹")

    # 返回箭头实测过的两个位置（相对坐标）：
    #   训练页/日历页 (0.945, 0.040)  ← flow.exit_page
    #   周末地点内页  (0.957, 0.047)  ← weekend.leave_street
    # 两个不是同一个点，恢复时轮流点。
    BACK_POINTS = ((0.945, 0.040), (0.957, 0.047))

    # 比赛结算页：必须先把结算点掉，否则返回箭头无效。
    # 只在这个页面允许点「下一步」这类前进按钮，别的页面一律不准（防止误开训练）。
    RESULT_MARKS = ("比赛胜利", "比赛失败", "获得奖励", "结算")
    RESULT_DISMISS = ("跳过比赛", "确定", "确认", "下一步", "返回大厅", "结束")

    # 大厅指纹：沿用 handle_lobby 的入口文案（见 handlers/entry.py）。
    LOBBY_MARKS = ("社团", "社团GO", "开学纳新祭", "转一转", "冠军之路")
    # 这些文案出现就说明不是大厅主界面（队伍/训练/选人页面）
    NOT_LOBBY_MARKS = ("保存调整", "队伍能力", "选择队友", "选择队长",
                       "训练剧本", "失败率", "决定", "开始训练")
    # 比赛界面标记：强标记单独生效，弱标记要两个同时出现
    MATCH_STRONG_MARKS = ("暂停", "战术板")
    MATCH_WEAK_MARKS = ("VS", "比分")

    def find_exit_control(self, texts):
        """找当前页面顶部的退出控件（×/关闭/返回/放弃）。

        为什么不能只点右上角返回箭头：2026-10-30 实测「选队长」页的退出控件是
        **左上角 (52,40) 的 ×**，右上角那个位置点了 6 次画面纹丝不动，
        连点判定触发后 MCP 的 reset_to_start 直接失败。
        限制在屏幕顶部 20% 内，避免把正文里的 × 当按钮。
        """
        limit = self.height * 0.2
        for name in self.EXIT_TEXTS:
            for t in texts:
                if t.name.strip() == name and (t.y + t.height / 2) <= limit:
                    return t
        return None

    def dismiss_result_page(self, texts):
        """比赛/结算页：把结算点掉。返回 True 表示点了一次。"""
        names = "".join(t.name for t in texts)
        if not any(k in names for k in self.RESULT_MARKS):
            return False
        target = self.match_text(texts, self.RESULT_DISMISS)
        if target is None:
            return False
        self.log_info(f"结算页：点「{target.name}」")
        self.click_box(target, after_sleep=0.8)
        return True

    def _back_arrow(self, index):
        """按 BACK_POINTS 轮流点返回箭头（两个实测坐标）。"""
        rx, ry = self.BACK_POINTS[index % len(self.BACK_POINTS)]
        self.click(int(self.width * rx), int(self.height * ry), after_sleep=0.6)

    def recover_to_lobby(self, max_rounds=8):
        """把游戏带回大厅主界面（MCP 的 reset_to_start 基线）。

        规则：
        - 加载中 → 等，不算失败；
        - 弹窗 → 先关弹窗；
        - 比赛/小游戏内部 → **不盲点返回**，直接返回 False（点进暂停菜单更糟）；
        - 其它页面 → 点右上角返回箭头；连点两次画面不变就改试 ESC（Unity 常见返回键）；
        - 仍然不动就放弃并存证，别无限点（2026-10-30 从「选队长」页实测：
          返回箭头对该页无效，需要 ESC 兜底）。

        返回 True 表示已经在大厅；False 表示没恢复成功（调用方决定是中止还是继续）。
        """
        last_sig, frozen = None, 0
        back_index = 0
        texts = []
        for _ in range(max_rounds):
            texts = self._ocr_region()
            if not texts:
                self.sleep(0.4)
                continue
            if self.is_lobby(texts):
                return True
            if self.is_loading(texts):
                self.sleep(0.6)
                continue
            if self.looks_like_match(texts):
                self.log_warning(
                    "回大厅中止：当前在比赛/小游戏界面，盲点返回有风险，请人工退到大厅")
                self.attempt_dump("recover_blocked", texts)
                return False
            if self.dismiss_popup():
                self.sleep(0.4)
                continue
            # 结算页必须先把结算点掉，否则返回箭头是无效的
            if self.dismiss_result_page(texts):
                self.sleep(0.4)
                continue

            sig = hash("|".join(sorted(t.name for t in texts)))
            frozen = frozen + 1 if sig == last_sig else 0
            last_sig = sig
            if frozen >= 2:
                self.log_warning("返回箭头连点两次画面没变，改试 ESC")
                self.send_key("esc")
                self.sleep(0.8)
                if frozen >= 3:
                    break
                continue
            exit_control = self.find_exit_control(texts)
            if exit_control is not None:
                self.log_info(f"点退出控件「{exit_control.name}」"
                              f"@({exit_control.x},{exit_control.y})")
                self.click_box(exit_control, after_sleep=0.6)
            else:
                # 训练页和周末地点内页的箭头坐标不同，轮流点
                self._back_arrow(back_index)
                back_index += 1

        ok = self.is_lobby()
        if not ok:
            names = "/".join(t.name for t in texts[:10])
            self.log_warning(f"回大厅失败，当前界面：{names}")
            self.attempt_dump("recover_failed", texts)
        return ok

    # 这些页面是「任务可以正常工作」的起点（不一定在大厅）
    START_MARKS = ("星期", "出行", "失败率", "训练剧本", "赛前准备",
                   "跳过比赛", "训练核心", "选择队友", "选择笔记", "选择器材")

    def is_startable(self, texts=None):
        """当前界面能不能直接开始跑任务（大厅 or 已知流程页）。

        2026-10-30：进程被杀后游戏停在「训练日/周末首页」，这类页面没有返回入口，
        recover_to_lobby 必然失败。但任务本来就在这些页面工作，所以把它们也算合法起点；
        比赛内部仍然排除（不能盲点）。
        """
        if texts is None:
            texts = self._ocr_region()
        if self.is_lobby(texts):
            return True
        if self.looks_like_match(texts):
            return False
        names = "".join(t.name for t in texts or [])
        return any(k in names for k in self.START_MARKS)

    def reset_to_start(self):
        """MCP 契约：只在大厅开始。

        2026-10-30 事故：允许「流程中途也算起点」后，任务接手了用户正在跑的一局，
        还把配置里的队长流派套到那一局上（实际队长不同）→ 周末计划全错。
        现在默认**只认大厅**；确实需要中途接手时，显式设 OK_MCP_ALLOW_RESUME=1。
        """
        import os
        self.begin_attempt()
        if self.recover_to_lobby():
            return True
        if os.environ.get("OK_MCP_ALLOW_RESUME") == "1" and self.is_startable():
            self.log_warning("回大厅失败，OK_MCP_ALLOW_RESUME=1 → 从当前流程页接手")
            return True
        self.log_warning("回大厅失败，且没有开启 OK_MCP_ALLOW_RESUME → 不接手中途的局")
        self.attempt_dump("reset_refused")
        return False

    def verify_start_state(self):
        """MCP 契约：断言「大厅或已知流程页」。校验失败必须抛异常。"""
        texts = self._ocr_region()
        assert texts, "起始校验失败：这一帧没有识别到任何文字（窗口被遮挡？）"
        names = "/".join(t.name for t in texts[:10])
        assert self.is_startable(texts), (
            f"起始校验失败：既不是大厅也不是已知流程页（识别到：{names}）")
        return True

    # ---------- 文字 ----------

    def _ocr_region(self, region_name=None, box=None, frame=None):
        """对某一帧做 OCR。传 frame 就只认这一帧（和存证图保持同一时刻）。"""
        if box is None and region_name:
            box = self.box_of_screen(*Defs.region(region_name))
        try:
            return self.ocr(box=box, frame=frame) or []
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

    # ---------- 周末地图：队友气泡 ----------

    # 气泡是深藏青（约 #2e3350，带白边）。2026-10-30 用
    # logs/debug/1026-184525_nochange3/frame.jpg 标定：
    #   游戏厅 0.0751 / 餐厅 0.0524 / 步行街 0.0963（有气泡）
    #   公园   0.0010（没有气泡，上方是「划船比赛!」红banner）
    # 0.02 落在两者中间，留足余量。
    MATE_BUBBLE_MIN_FRACTION = 0.02
    MATE_REGION_DX = 0.07      # 标签中心左右各 7% 宽
    MATE_REGION_UP_TOP = 0.35  # 从标签中心往上 35% 高
    MATE_REGION_UP_BOTTOM = 0.10

    def venue_has_mate(self, label_box, frame=None):
        """周末地图上，这个地点名字**上方有没有队友对话气泡**。

        判据只看颜色，不认头像：气泡是深藏青实心块，地图背景是浅蓝，
        两者占比差 50 倍以上（见上面的标定值）。frame 默认用本拍识别那一帧
        （run() 里存的 _tick_frame），保证和 OCR 框同一时刻。
        """
        frame = frame if frame is not None else getattr(self, "_tick_frame", None)
        if frame is None:
            frame = self.frame
        if frame is None or label_box is None:
            return False
        height, width = frame.shape[:2]
        cx = label_box.x + label_box.width / 2
        cy = label_box.y + label_box.height / 2
        x1 = int(max(0.0, (cx / width) - self.MATE_REGION_DX) * width)
        x2 = int(min(1.0, (cx / width) + self.MATE_REGION_DX) * width)
        y1 = int(max(0.0, (cy / height) - self.MATE_REGION_UP_TOP) * height)
        y2 = int(max(0.0, (cy / height) - self.MATE_REGION_UP_BOTTOM) * height)
        if x2 <= x1 or y2 <= y1:
            return False
        crop = frame[y1:y2, x1:x2]
        b = crop[:, :, 0].astype(int)
        g = crop[:, :, 1].astype(int)
        r = crop[:, :, 2].astype(int)
        mask = (r < 95) & (g < 95) & (b < 140) & (b >= r)
        return float(mask.mean()) >= self.MATE_BUBBLE_MIN_FRACTION

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
