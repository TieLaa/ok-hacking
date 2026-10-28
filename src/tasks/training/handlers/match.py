"""比赛全流程：赛前准备、开场 ▶▶、比赛内快进、赛程结算、技能三选一、事件选项、倍速。

这些 handler 同时被 `TrainingTask`（养成）和 `QuickMatchTask`（常驻快速比赛）复用，
所以不要在这里假设「当前一定在养成流程里」。
"""

import random
import re
import time

import os
import threading
from collections import deque

import cv2
import numpy as np

from src.tasks.ui import Defs  # noqa: F401
from src.tasks.training.constants import JPEG_PARAMS  # noqa: F401


class MatchHandlers:

    def handle_match_intro(self, texts):
        """NBA 转场：**跳过比赛 / 赛后结算之后**一闪而过的 NBA logo，没有可点的控件。

        2026-10-26 复盘：这个页面从不出现在比赛开场，只在「跳过比赛」成功和结算
        返回之后出现，约 1 秒自己走完。点左下角（比赛内快进的位置）不会有任何反应，
        所以这里只等，不点。
        """
        names = {t.name.strip() for t in texts}
        if not (names & {"NBA", "VS"}):
            return False
        # 纯转场页：文字很少，避免在比赛结算/数据页误判
        if len([t for t in texts if len(t.name.strip()) >= 2]) > 6:
            return False
        self._no_stall_until = time.monotonic() + 3
        self.sleep(0.4)
        return True

    def handle_in_match(self, texts):
        """比赛内：先把手动切成自动，再连点「快进」。

        开场阶段（跳球前）左下角**只有一个 ▶▶ 快进按钮**，没有「自动/手动」，
        旧代码因为找不到这两个词直接 return False，整段开场一次都不点
        （用户 2026-10-26 现场截图确认）。现在用 `_match_intro_at` 标记这个窗口，
        窗口内也点快进；跳球后 HUD 齐全，再走「手动 → 自动」的正常流程。
        """
        hud = self.texts_in_region(texts, "match_hud")
        intro = bool(self._match_intro_at) and time.monotonic() - self._match_intro_at < 90
        if not hud and not intro:
            return False
        # 赛前准备页还没点掉：交给 handle_prepare 继续点「跳过/前往比赛」
        if self.match_text(texts, ("跳过比赛", "前往比赛")) is not None:
            return False

        auto = self.match_text(hud, "自动")
        manual = self.match_text(hud, "手动")
        if auto is not None or manual is not None:
            self._match_intro_at = 0.0          # 跳球完成，开场窗口关闭
        if manual is not None:
            self.click_box(manual, after_sleep=0.35)
            self.log_info("比赛：手动 → 自动")
            return True
        if auto is None and not intro:
            return False

        x = int(self.width_of_screen(Defs.MATCH_FAST_FORWARD[0]))
        y = int(self.height_of_screen(Defs.MATCH_FAST_FORWARD[1]))
        self.click(x, y, after_sleep=0.6)
        if auto is None:
            self.log_debug("比赛开场：点左下角 ▶▶ 快进")
        return True

    def handle_match_result(self, texts):
        """比赛结算 / 赛程页 / 目标达成：一路点「下一步」或屏幕。

        判定必须**整词**：「失败率 0%」里也有「失败」，科目页会被误判成结算页。

        一局的终点（用户 2026-10-26）：最终结算页（A 级评价大字那张）点「下一步」
        会进入**注册球员编队** —— 那是这一局的终点，任务停在这里。
        判据：只有评价等级大字（A/S/B 单独成框）+ 没有顶栏周数，才点「下一步」；
        点完看到「选择器材/确认编队」等编队页文字后，run() 直接 return 结束。
        """
        if self.match_text(texts, ("失败率", "决定", "休息")) is not None:
            return False
        # 锦标赛对阵图：只有「赛程已完成 / 距离下场比赛 / 下一场」和学校名，
        # 没有「结算/赛程」这种整词标题，旧判据认不出来会被事件 handler 当成竖排选项。
        if self.match_text(texts, ("赛程已完成", "距离下场比赛", "挑战中")) is not None:
            nxt = self.match_text(texts, "下一步")
            if nxt is None:
                nxt = self.match_text(texts, "下一场")
            if nxt is not None:
                self.click_box(nxt, after_sleep=0.6)
                self.log_info("赛程：下一步")
            return True
        nxt_match = self.match_text(texts, "下一场")
        if nxt_match is not None:
            self.click_box(nxt_match, after_sleep=0.6)
            self.log_info("赛程：下一场")
            return True
        if self.match_text(texts, (Defs.RESULT_TITLE_RE,)) is None:
            return False
        nxt = self.match_text(texts, "下一步")
        if nxt is not None:
            self.click_box(nxt, after_sleep=0.5)
            self.log_info("结算：下一步")
            return True
        if self.match_text(texts, "点击屏幕继续") is not None:
            self.click(int(self.width_of_screen(0.5)), int(self.height_of_screen(0.9)), after_sleep=0.5)
            self.log_info("结算：点击继续")
            return True
        self.click(int(self.width_of_screen(0.5)), int(self.height_of_screen(0.5)), after_sleep=0.45)
        return True

    def in_registration_page(self, texts):
        """最终结算的下一页：注册球员 / 签约 —— 这才是**一局的终点**。

        坑（2026-10-26）：这里原来写了「选择器材 / 确认编队」，但那两步是
        **每局开局的标准流程**（训练 → 启程 → 选队长 → 选择器材 → 确认编队 → 开始训练）。
        结果新一局刚开局 3 秒就被当成"跑完了"直接退出。
        所以只认注册/签约这类**一次性**文案，绝不认开局建队的按钮。
        """
        return self.match_text(texts, ("注册球员", "球员登录", "签约", "入部申请", "成为注册")) is not None

    def init_match_state(self):
        """比赛相关的共享状态：养成任务和「快速比赛」模块都要初始化（两个任务共用）。"""
        self._skip_tries = 0
        self._match_intro_at = 0.0
        self._speed_week = None
        self._speed_locked = False

    def try_speed_2x(self, texts):
        """大师赛解锁 2x：把顶栏倍速按钮从 1x 切到 2x。

        不作为独立 handler：只在「训练日主界面 / 赛前准备」这两个确定安全的时机调用，
        避免在结算页、商店页乱点顶栏。

        判据只看图标右侧三角是否实心蓝（1x 是灰白，2x 是实心蓝），不依赖 OCR 小字。
        每周最多尝试一次：点了没变化就认为还没解锁，本周不再点，避免每拍刷屏。
        """
        if self.match_text(texts, Defs.WEEK_RE) is None:
            return False
        week_m = Defs.WEEK_RE.search("".join(t.name for t in texts).replace(" ", ""))
        week = int(week_m.group(1)) if week_m else None

        if self.speed_is_2x():
            return False
        if self._speed_week == week and self._speed_locked:
            return False
        if self._speed_week != week:
            self._speed_week = week
            self._speed_locked = False

        x = int(self.width * Defs.SPEED_ICON_CLICK[0])
        y = int(self.height * Defs.SPEED_ICON_CLICK[1])
        self.click(x, y, after_sleep=0.5)
        if self.speed_is_2x():
            self.log_info("已切到 2x 速度")
        else:
            self._speed_locked = True
            self.log_info("倍速按钮点了没变 2x（未到大师赛），本周不再尝试")
        return True

    def speed_is_2x(self):
        """顶栏倍速图标：右三角实心蓝 = 2x。"""
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = Defs.SPEED_ICON_TRIANGLE
        roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        if roi.size == 0:
            return False
        b = roi[:, :, 0].astype(int)
        r = roi[:, :, 2].astype(int)
        blue = (b > 150) & (b > r + 20)
        return float(blue.mean()) > Defs.SPEED_BLUE_RATIO_2X

    def _card_choices(self, texts):
        """底排横向大卡：同一行 2~3 块短标题 + 每块自己下方一行效果文字。

        返回 [(标题框, [效果框...]), ...]；不只是「按摩」，温泉/强化训练等
        三选一大卡都是这个形态。
        """
        h = self.height
        w = self.width
        rows = {}
        for t in texts:
            s = t.name.strip()
            cy = (t.y + t.height / 2) / h
            if not (0.62 <= cy <= 0.88):
                continue
            if not (2 <= len(s) <= 8) or any(ch.isdigit() for ch in s):
                continue
            if self.is_label(s) or self.is_decor(s):
                continue
            key = round(cy, 1)
            rows.setdefault(key, []).append(t)
        best = []
        for key, group in rows.items():
            if not (2 <= len(group) <= 3):
                continue
            items = []
            for t in group:
                cx = (t.x + t.width / 2) / w
                effs = [o for o in texts
                        if o is not t
                        and abs((o.x + o.width / 2) / w - cx) < 0.12
                        and (o.y + o.height / 2) / h > key
                        and (o.y + o.height / 2) / h < key + 0.16
                        and any(k in o.name for k in ("属性", "精力", "概率", "%"))
                        and o.name.strip() != t.name.strip()]
                if effs:
                    items.append((t, effs))
            if len(items) >= 2 and len(items) > len(best):
                best = items
        return best

    def handle_prepare(self, texts):
        """赛前准备页：跳过比赛 / 前往比赛。

        规则（用户实测）：只有**打赢过**的比赛才能跳过；没打赢的必须进比赛，
        而且只能在比赛里点加速。所以这里做两级降级：
        先点「跳过比赛」，下一拍如果还在这一页，就改点「前往比赛」。
        """
        skip = self.match_text(texts, "跳过比赛")
        go = self.match_text(texts, "前往比赛")
        if skip is None and go is None:
            return False

        if self.try_speed_2x(texts):
            return True

        mode = self.config.get("比赛日处理", "")
        if mode.startswith("总是进比赛") and go is not None:
            self._match_intro_at = time.monotonic()
            self.click_box(go, after_sleep=0.6)
            self.log_info("进比赛（等外部战斗脚本接管）")
            return True

        if skip is not None and self._skip_tries == 0:
            self._skip_tries = 1
            self.click_box(skip, after_sleep=0.5)
            self.log_info("尝试跳过比赛")
            return True

        if go is not None:
            self._skip_tries = 0
            # 从这一刻进入比赛开场：只有左下角 ▶▶，由 handle_in_match 点
            self._match_intro_at = time.monotonic()
            self.click_box(go, after_sleep=0.6)
            self.log_info("跳过不可用，进比赛（开场点 ▶▶ 加速）")
            return True

        self.log_warning("赛前准备页：跳过和前往都没找到")
        return False

    def handle_register_player(self, texts):
        """一局打完的**注册球员**收尾流程（用户 2026-10-27 提供 6 屏截图）。

        这是「训练赛 → 注册球员 → 编队 → 保存 → 回大厅」的固定六步，走完才算一局结束：

        1. 恭喜获得 / 注册球员页：点「下一步」（左下还有「记录笔记」，不要点）
        2. 「训练结束」弹窗：点「编队」（另一个「前往主界面」会跳过编队，不点）
        3. 战术板页：点「自动编队」（一行阵容自动排好）
        4. 同一页出现「评分上升」后：点「保存调整」（保存前是灰的，保存后变黄）
        5. 「更新最高队伍等级」弹窗：点「关闭」
        6. 战术板页：点左上角「«」（战术板标题左边的双箭头）回大厅

        **必须靠 `_register_stage` 串起来**：战术板页玩家自己也能进（阵容菜单），
        没有进入过第 1 步就绝不能在这里点「自动编队」，否则会改掉玩家的阵容。
        同理「关闭」不能交给 handle_popup —— 它会在任何页面上点第一个「关闭」。
        """
        stage = getattr(self, "_register_stage", None)

        # 1) 注册球员页（标题「注册球员」+ 黄色「下一步」）
        if self.in_registration_page(texts):
            target = self.exact_text(texts, ("下一步",))
            if target is None:
                # OCR 偶尔把「下一步」读成「下一」（丢字），按前缀兜底
                target = next((t for t in texts
                               if t.name.strip().startswith("下一")
                               and len(t.name.strip()) <= 4), None)
            if target is not None:
                tries = getattr(self, "_reg_step1_tries", 0) + 1
                now = time.monotonic()
                if now - getattr(self, "_register_click_at", 0.0) < 2.0:
                    return True
                # 点不动就换位置再试：某些分辨率下「下一步」的点击热区比 OCR 文字框高/低
                # （2026-10-28 1549x871 实跑连点 60 秒都没推进）
                offs = (0.0, -0.015, 0.015, -0.03, 0.03)
                dy = offs[min(tries - 1, len(offs) - 1)] * self.height
                self._register_click_at = now
                self._reg_step1_tries = tries
                if tries > 6:
                    self.dump_unknown(texts, "register_step1_stuck")
                    self.log_error("注册球员第 1 步点了 6 次都没推进 → 存证 logs/debug/ 后放弃")
                    self._reg_step1_tries = 0
                    return False
                if tries == 2:
                    # 第一次没推进就存一帧，方便事后核对按钮真实位置
                    self.dump_unknown(texts, "register_step1_fail")
                if stage is None:
                    self.sleep(0.45)
                x = int(target.x + target.width / 2)
                y = int(target.y + target.height / 2 + dy)
                self.click(x, y, after_sleep=1.0)
                self._register_stage = "next"
                self.log_info(f"注册球员 1/6：下一步 @({x},{y})"
                              f"{'' if tries == 1 else f' 第{tries}次'}")
                return True

        # 2) 训练结束弹窗：编队（不是「前往主界面」）
        # **必须整词匹配**：事件页正文里也有「训练结束后，米歇尔理事长拿来了一本…」
        # （2026-10-27 18:12 实跑），包含匹配会把事件页当成收尾弹窗，
        # 结果随手点了最右边的黄按钮，等于把三选一事件乱选了。
        if self.exact_text(texts, ("训练结束",)) is not None:
            target = self.exact_text(texts, ("编队",))
            if target is not None:
                self.click_box(target, after_sleep=0.8)
                self._register_stage = "team"
                self._reg_step1_tries = 0
                self.log_info("注册球员 2/6：编队")
                return True
            # OCR 经常读不出黄色按钮里的「编队」（样张 2026-10-27-register-2-team.png
            # 只认出左边的「前往主界面」）。这页固定「左前往主界面 / 右编队」且
            # 两个按钮关于弹窗中线对称：拿正文框的中心当中线，把左边按钮镜像过去。
            left = self.exact_text(texts, ("前往主界面",))
            body = next((t for t in texts if "成为注册球员" in t.name), None)
            if left is not None and body is not None:
                mid = body.x + body.width / 2
                x = int(2 * mid - (left.x + left.width / 2))
                y = int(left.y + left.height / 2)
                self.click(x, y, after_sleep=0.8)
                self._register_stage = "team"
                self.log_info(f"注册球员 2/6：编队（镜像左侧按钮 @{x},{y}）")
                return True
            btns = self._yellow_buttons()
            if len(btns) >= 2:
                bx, by, bw, bh = max(btns, key=lambda b: b[0])
                self.click(int(bx + bw / 2), int(by + bh / 2), after_sleep=0.8)
                self._register_stage = "team"
                self.log_info("注册球员 2/6：编队（黄色按钮兜底）")
                return True

        if stage is None:
            return False

        # 2b) 编队详情页（右侧是「注册球员」名单，底部 重置/自动选择/保存编队）
        #     这一步在用户截图里没有出现，但实跑（2026-10-27 16:18）确实会进：
        #     「编队」→ 详情页 → 自动选择 → 保存编队 → 回到战术板。
        in_team_edit = (self.exact_text(texts, ("保存编队",)) is not None
                        and self.exact_text(texts, ("自动选择",)) is not None)
        if in_team_edit:
            if stage in ("team", "next"):
                target = self.exact_text(texts, ("自动选择",))
                self.click_box(target, after_sleep=0.8)
                self._register_stage = "team_auto"
                self.log_info("注册球员 2b：编队详情 → 自动选择")
                return True
            if stage == "team_auto":
                target = self.exact_text(texts, ("保存编队",))
                self.click_box(target, after_sleep=1.0)
                self._register_stage = "team_saved"
                self.log_info("注册球员 2b：保存编队")
                return True

        # 3) 战术板：自动编队
        if (self.match_text(texts, "战术板") is not None
                and self.exact_text(texts, ("自动编队",)) is not None
                and stage in ("next", "team", "team_saved")):
            target = self.exact_text(texts, ("自动编队",))
            self.click_box(target, after_sleep=0.9)
            self._register_stage = "auto"
            self.log_info("注册球员 3/6：自动编队")
            return True

        # 4) 同一页出现「评分上升」后：保存调整
        if (self.match_text(texts, "战术板") is not None
                and self.exact_text(texts, ("保存调整",)) is not None
                and stage == "auto"):
            target = self.exact_text(texts, ("保存调整",))
            self.click_box(target, after_sleep=0.8)
            self._register_stage = "save"
            self.log_info("注册球员 4/6：保存调整")
            return True

        # 5) 更新最高队伍等级弹窗：关闭
        if (self.match_text(texts, "更新最高队伍等级") is not None
                and self.exact_text(texts, ("关闭",)) is not None):
            target = self.exact_text(texts, ("关闭",))
            self.click_box(target, after_sleep=0.6)
            self._register_stage = "closed"
            self.log_info("注册球员 5/6：关闭升级弹窗")
            return True

        # 6) 返回大厅：左上角 « **没有文字**，用标题框当锚点向左推
        #    2026-10-27 16:18 事故：按截图比例写成 (0.0505w, 0.1075h)，
        #    但截图带系统标题栏（比游戏画面高约 29px），点在按钮下沿外面，
        #    连点 19 拍没反应、走到最大步数才停。改成「标题框左边 3% 宽、同一行高」。
        if stage in ("save", "closed", "back"):
            title = self.exact_text(texts, ("战术板", "首发"))
            if title is not None or in_team_edit:
                if title is not None:
                    x = int(title.x - self.width * 0.030)
                    y = int(title.y + title.height / 2)
                else:
                    x, y = int(self.width * 0.0505), int(self.height * 0.068)
                self.click(x, y, after_sleep=0.9)
                self._register_stage = "back"
                self.log_info(f"注册球员 6/6：返回大厅 @({x},{y})")
                return True

        return False

    def handle_tap_to_continue(self, texts):
        """「点击屏幕继续」类过场：MVP 结算、胜负动画 —— 点屏幕中央推进。

        2026-10-28 用户实测：比赛打赢后的 MVP 页只有一行「点击屏幕继续」，
        没有任何按钮，训练流程和快速比赛的 handler 都不认领，于是卡在那儿。

        排在专属 handler 之后（顺序见 handlers/__init__.py）：
        带「下一步 / 确定 / 跳过 / 放弃 / 继续」按钮的页面不抢，交给对应 handler。
        """
        hint = self.match_text(texts, "点击屏幕继续")
        if hint is None:
            return False
        if self.exact_text(texts, ("下一步", "确定", "跳过", "继续", "放弃")) is not None:
            return False
        # **先点这行字自身**（用户 2026-10-28：MVP 页点屏幕正中没反应）。
        # 但训练日开场的「新目标」弹窗是整屏可点、点文字可能被底下的按钮挡住，
        # 所以同一画面第二拍还没变化时退回点屏幕中央。
        sig = frozenset(t.name.strip() for t in texts)
        if getattr(self, "_tap_sig", None) == sig:
            self._tap_count = getattr(self, "_tap_count", 1) + 1
        else:
            self._tap_sig, self._tap_count = sig, 1
        if self._tap_count <= 1:
            self.click_box(hint, after_sleep=0.5)
            self.log_info("点击屏幕继续（点文字 "
                          f"@{hint.x + hint.width // 2},{hint.y + hint.height // 2}）")
        else:
            self._tap_count = 0
            self.click(int(self.width * 0.5), int(self.height * 0.4), after_sleep=0.5)
            self.log_info("点击屏幕继续（文字没反应，改点屏幕中央）")
        return True

    def handle_result(self, texts):
        """比赛/训练结算页：确认、继续、领取、下一步。

        一局的终点（用户 2026-10-26）：最终结算点「下一步」会进入**注册球员编队**，
        任务到这里就算跑完，不要再往下点。

        2026-10-26 补充：地点活动（划船/野餐）成功页也是「成功！+ 属性 + 下一步」，
        顶栏还在、没有评价大字，handle_match_result 的 RESULT_TITLE_RE 认不出来，
        这里必须兜住「下一步」，否则会在结果页点中央空转到卡死。
        """
        # 必须是整词按钮：match_text 的子串匹配会把台词里的「继续」当成按钮
        # （2026-10-26 实测：点了「…请继续保持这份斗志…」这句台词）。
        target = self.exact_text(texts, ("确认", "继续", "领取", "下一步"))
        if not target:
            return False
        # 结算页的「确认」可能连点两次
        self.click_box(target, after_sleep=0.45)
        self.log_info(f"结算页「{target.name}」")
        return True

    def handle_skill_select(self, texts):
        """技能三选一：**按品质算分**（评分公式见 docs/score-model/README.md）。

        评分里技能价值：彩色 75 / 金色 50 / 白色 25（蓝、绿图标都算白）。
        所以品质是第一权重；标签是否与队长匹配只作同品质时的次级参考。
        """
        if self.match_text(texts, "请选择1个技能") is None:
            return False
        if os.environ.get("OK_DUMP_CHOICES"):
            self.dump_unknown(texts, "choice_skill")

        pref = list(self.config.get("技能标签优先", ["得分手"]))
        frame = self.frame

        best_idx, best_score = 0, -1
        detail = []
        for i in range(3):
            card_texts = self._card_texts(texts, i)
            has_pref = 1 if self.match_text(card_texts, tuple(pref)) is not None else 0
            quality = self._card_quality(frame, i)      # 3 彩 / 2 金 / 1 白
            score = quality * 10 + has_pref
            detail.append(f"{i + 1}:{Defs.QUALITY_LABEL[quality]}/标签{has_pref}")
            if score > best_score:
                best_idx, best_score = i, score
        self.log_info("技能评分 " + " ".join(detail) + f" → 选第 {best_idx + 1} 张")

        cx, cy = Defs.SKILL_CARDS[best_idx]
        self.click(int(self.width_of_screen(cx)), int(self.height_of_screen(cy)), after_sleep=0.4)
        confirm = self.find_text(("确定", "确认"))
        if confirm is not None:
            self.click_box(confirm, after_sleep=0.5)
            self.log_info("技能：确定")
        return True

    @staticmethod
    def _pick_choice(infos, kind):
        """按用户规则挑选项，返回 (选中项, 理由)。

        用户口径（2026-10-26，2026-10-27 确认多选同样适用）：
        1. **纯回精力**的选项不参与竞争（只补 10 点体力，不如一点属性）；
           全部都是回精力时才退回按数值取最大；
        2. 其余按「收益×概率」的期望值取最高；同分取靠右（二选一时）。
        竖向排列是剧情三选一，仍然固定选第一个。
        """
        if kind == "v":
            return min(infos, key=lambda i: i["y"]), "竖向排列 → 第一个"
        pool = [i for i in infos if not i["flat"]] or infos
        pick = max(pool, key=lambda i: (i["ev"] if i["ev"] is not None else -1, i["x"]))
        why = ("二选一 → 排除纯回精力，取期望最高" if len(infos) == 2
               else "横向多选 → 排除纯回精力，取期望最高")
        return pick, why

    def handle_event_choice(self, texts):
        """突发事件二/三选一：必须点其中一个选项（右上「跳过」是灰的）。

        用户规则（2026-10-26）：
        1. **左右二选一不要选纯回精力的那个**（只回 10 精力，另一个期望值更高）；
           有「X%概率 +N」就按 收益×概率 算期望；
        2. **竖向三选一默认选第一个**（最上面那个）。

        判据用**黄色按钮边框**而不是底部短文字：训练动画页底部也有
        「投射/体能/防守」等 2 字属性名，旧判据会把它们当选项乱点。
        """
        if self.match_text(texts, ("在公园", "在餐厅", "野餐", "散步", "划船",
                                   "当服务员", "桌游", "扭蛋")) is not None:
            return False
        # 周末地图：地点名是导航，不是选项（2026-10-26 把「游戏厅/公园」当选项点了）。
        if len([t for t in texts if t.name.strip() in Defs.WEEKEND_PLACES]) >= 2:
            return False
        # 训练科目页也有黄色卡片（器材卡）和底部属性面板，但它们不是选项。
        # 科目页必有「失败率」，并且「决定」是**独立的按钮框**（实训 2026-10-26）。
        # 2026-10-27 事故（第3周「球星卡训练事件·广播站支持」）：正文里有
        # 「成功后，决定通过广播站平台支持球队。」——旧判据用包含匹配找「决定」，
        # 把事件页当成科目页直接 return False，结果 3 次点屏幕中央没反应、卡死停止。
        if self.match_text(texts, "失败率") is not None:
            return False
        if self.exact_text(texts, ("决定",)) is not None:
            return False
        if self.match_text(texts, ("上新时间", "能力详情")) is not None:
            return False
        if self.match_text(self.texts_in_region(texts, "main_action"),
                           ("VS", "比赛", "训练")) is not None:
            return False
        # 2026-10-27 事故（第2周周日划船结算页）：中间「工资／获得／获得」
        # 三行奖励条目被 choice_candidates 当成竖向三选一，连点 9 次都不动，
        # 任务被判卡死停止。结算页底部一定有「下一步」，事件选项页没有。
        if self.match_text(texts, "下一步") is not None:
            return False

        # 2026-10-27 事故（QuickMatch 挂在全国大赛关卡列表）：右侧信息栏
        # 「对手实力 / 关卡加成 / 奖励预览」同列竖排、短文本，全中 choice_candidates
        # 的形态判据，被当成竖向三选一，每 0.5 秒点一次「对手实力」（点了 30 秒没反应，
        # TriggerTask 没有看门狗，只能等人发现）。赛事/关卡详情页同时有这几个标题。
        if (self.match_text(texts, "关卡加成") is not None
                and self.match_text(texts, "奖励预览") is not None):
            return False
        if (self.match_text(texts, "对手实力") is not None
                and self.match_text(texts, "前往挑战") is not None):
            return False
        # 本周总结页（球队评分表格）：左列竖排「控卫／分卫／小前／大前／中锋」，
        # 同一列、2 字短文本，被 choice_candidates 当成竖向三选一，
        # 每拍点一次「控卫」连点 4~5 下（2026-10-27 00:02 实机）。
        # 页面在播动画（分数 4771→5937、文字每拍都在变），看门狗的
        # 「3 次无变化」判不出来，只能靠页面特征排除。
        if self.match_text(texts, ("本周总结", "球队评分")) is not None:
            return False
        if (self.match_text(texts, "球员") is not None
                and self.match_text(texts, "位置") is not None
                and self.match_text(texts, "评分") is not None):
            return False
        # ---- 竖向 2/3 选 1（白底彩边，跳过是灰的）----
        # **不要求白底**：事件页背景有时偏暖/偏暗，_has_white_band 会漏判；
        # 漏判后 dialog_continue 会去点灰的「跳过」，表现是一直点跳过、
        # 不选第一个（用户 2026-10-26 反馈）。形态判据见 choice_candidates。
        has_skip = (self.match_text(texts, "训练计划") is None
                    and self.match_text(
                        self.texts_in_region(texts, "top_right"), "跳过") is not None)

        group = self.choice_candidates(texts)
        # 事件只可能二/三选一：组里出现 4 项以上，说明把表格列或信息栏捞进来了
        # （2026-10-27 00:02 周结算页 5 个位置名「控卫…中锋」就是这样被当成选项的）
        if group and len(group) > 3:
            return False
        # 竖向选项是**居中**的一列卡片：实测 `2026-10-26-story-choice.png` 三项
        # cx 全是 0.50。靠这条排除「贴边的一列短文本」——这类几乎都是表格列或字幕：
        #   周结算左列「控卫/分卫/小前/大前/中锋」cx≈0.26，
        #   周末前对话「我要去和小式一起去看电影」cx≈0.68。
        # 比按文字内容/符号做排除可靠：台词里什么符号都可能出现。
        if group:
            centers = [(t.x + t.width / 2) / self.width for t in group]
            if (max(centers) - min(centers) > 0.06
                    or not 0.38 <= sum(centers) / len(centers) <= 0.62):
                return False
        # 只有 1 项也不是选项：剧情页右上角同样有「跳过」，has_skip 会把它放进来
        # （2026-10-27 10:07 周末前对话「我要去和小式一起去看电影」被点 5 次空点）
        if group and len(group) >= 2 and (has_skip or len(group) >= 3):
            pick = min(group, key=lambda t: t.y)
            if os.environ.get("OK_DUMP_CHOICES"):
                self.dump_unknown(texts, "choice_vertical")
            self.log_info("事件选项（竖向）：" + "、".join(t.name.strip() for t in group)
                          + f" → 选第一个「{pick.name.strip()}」")
            self.click_box(pick, after_sleep=0.45)
            return True

        # ---- 底排横向 3 选 1 大卡片（按摩/温泉/强化训练这类）----
        # 2026-10-28 实跑卡死：按摩事件「放弃按摩 属性+10 / 安心按摩 70%概率属性+30 /
        # 神秘按摩 30%概率属性+60」，三个选项是白底大卡（不是黄色按钮），
        # 既不成居中一列、也不被 _yellow_buttons 认出 → 没人认领 → 卡死停任务。
        cards = self._card_choices(texts)
        if len(cards) >= 2:
            infos = []
            for box, effs in cards:
                ev = self._effect_ev(effs)
                attrs = any(any(a in e.name for a in Defs.TRAINING_SUBJECTS)
                            for e in effs)
                flat = (any("精力" in e.name for e in effs) and not attrs)
                infos.append({"x": box.x, "y": box.y, "box": (box.x, box.y, box.width, box.height),
                              "name": box.name.strip(), "name_box": box,
                              "ev": ev, "flat": flat})
            pick, why = self._pick_choice(infos, "h")
            detail = "、".join(
                f"{i['name']}({('%.0f' % i['ev']) if i['ev'] is not None else '?'}"
                f"{'·回精力' if i['flat'] else ''})" for i in infos)
            self.log_info(f"事件选项（横向卡片）：{detail} → 选「{pick['name']}」（{why}）")
            self.click_box(pick["name_box"], after_sleep=0.45)
            return True

        buttons = self._yellow_buttons()
        if len(buttons) < 2:
            return False

        # 只接受「排成一行」或「排成一列」且尺寸相近的按钮组。
        # 训练剧本页也有黄色「启程」大按钮 + 标题里的黄色碎片，但两者是斜对角，
        # 既不成行也不成列，必须排除（实训 2026-10-26）。
        def center(b):
            return b[0] + b[2] / 2, b[1] + b[3] / 2

        def similar(a, b):
            return (max(a[2], b[2]) / max(min(a[2], b[2]), 1) < 2.6
                    and max(a[3], b[3]) / max(min(a[3], b[3]), 1) < 2.2)

        best_group, best_kind = [], None
        by_y = sorted(buttons, key=lambda b: b[1])
        for i, b in enumerate(by_y):
            group = [b]
            for c in by_y[i + 1:]:
                if abs(center(b)[1] - center(c)[1]) < self.height * 0.05 and similar(b, c):
                    group.append(c)
            if len(group) > len(best_group):
                best_group, best_kind = group, "h"
        by_x = sorted(buttons, key=lambda b: b[0])
        for i, b in enumerate(by_x):
            group = [b]
            for c in by_x[i + 1:]:
                if abs(center(b)[0] - center(c)[0]) < self.width * 0.12 and similar(b, c):
                    group.append(c)
            if len(group) > len(best_group):
                best_group, best_kind = group, "v"
        if len(best_group) < 2:
            return False
        buttons = best_group

        infos = []
        for (bx, by, bw, bh) in buttons:
            inside = []
            for t in texts:
                cx = (t.x + t.width / 2) / self.width
                cy = (t.y + t.height / 2) / self.height
                if (bx / self.width <= cx <= (bx + bw) / self.width
                        and by / self.height <= cy <= (by + bh) / self.height):
                    inside.append(t)
            # 名字放宽到 12 字、不要求纯中文（「85%概率」类卡片也要能认出）；
            # 纯数字不算名字。
            names = [t for t in inside
                     if 2 <= len(t.name.strip()) <= 12
                     and not t.name.strip().isdigit()]
            effects = [t for t in inside if any(ch.isdigit() for ch in t.name)]
            name_box = max(names, key=lambda t: t.height) if names else None
            name = name_box.name.strip() if name_box else "?"
            ev = self._effect_ev(effects)
            attrs = any(any(a in t.name for a in Defs.TRAINING_SUBJECTS)
                        for t in effects)
            # 「纯回精力」= 只补体力、不加任何六维属性。带属性的（「精力+10，投射+5」）不算，
            # 否则会把好选项也一起排除掉。
            flat_stamina = (any("精力" in t.name for t in effects) and not attrs)
            infos.append({"x": bx, "y": by, "box": (bx, by, bw, bh),
                          "name": name, "name_box": name_box,
                          "ev": ev, "flat": flat_stamina})

        # 框内一个字都没有的，是两卡之间的黄色装饰/空白，点它没用
        # （用户 2026-10-26 反馈：二选一时在中间空白点了两次）。
        infos = [i for i in infos if i["name_box"] is not None]
        if len(infos) < 2:
            return False

        pick, why = self._pick_choice(infos, best_kind)
        detail = "、".join(
            f"{i['name']}({('%.0f' % i['ev']) if i['ev'] is not None else '?'}"
            f"{'·回精力' if i['flat'] else ''})" for i in infos)
        self.log_info(f"事件选项：{detail} → 选「{pick['name']}」（{why}）")
        if os.environ.get("OK_DUMP_CHOICES"):
            self.dump_unknown(texts, "choice_yellow")
        # 点文字框中心，不点黄色外框中心：外框可能横跨两卡，
        # 中心落在中间空白上（用户 2026-10-26 反馈）。
        self.click_box(pick["name_box"], after_sleep=0.45)
        return True
