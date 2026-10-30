"""大厅、剧情、选队长、组队、器材、确认编队。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
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


class EntryHandlers:

    def handle_dialog_continue(self, texts):
        """剧情/提示：右上角有「跳过」就点，否则交给 handle_story_arrow 连点推进。

        例外：屏幕中部是竖排选项（三选一）时，「跳过」通常是灰的，点了不推进。
        这时必须让位给 handle_event_choice（用户 2026-10-26 反馈：
        机器人一直点跳过，不点第一个选项）。
        """
        if (len(self.choice_candidates(texts)) >= 2
                or len(self.options_not_paragraph(texts)) >= 2):
            return False
        skip = self.match_text(self.texts_in_region(texts, "top_right"), ("跳过", "点击屏幕继续"))
        if skip is not None:
            self.click_box(skip, after_sleep=0.35)
            return True
        return False

    def handle_club_members(self, texts):
        """社团成员 / 角色列表页：误入时退出（左上角 <<）。"""
        # 必须整词：「她说要给社团成员们疏通经脉」这种正文也会命中包含匹配，
        # 结果按摩事件页被当成社团成员页，一直点左上角返回（2026-10-28 18:13 卡死）
        if self.exact_text(texts, ("社团成员",)) is None:
            return False
        self.click(int(self.width * 0.035), int(self.height * 0.06), after_sleep=0.8)
        self.log_info("社团成员页：点左上角返回")
        return True

    def handle_story_arrow(self, texts):
        """剧情对话：点一下、确认界面变了就停，没变才补点（最多 3 下）。

        判据几经修正（2026-10-25 实机）：
        1. 不能只靠「白色 ▼」——**很多剧情页根本没有 ▼**，点屏幕任意位置就推进；
        2. 也不能靠「背景必须暗」——浅色场景的剧情页大量存在；
        3. 也不能靠「白色像素够多」——扭蛋页的黄色按钮/白底卡片会误判（实训死循环）；
        4. 唯一可靠的特征是**底部台词条里的一行长中文**：y 0.72~0.96、12 字以上。

        训练日/科目页/赛程页底部也有文字（属性、按钮），所以先排除这些关键词。
        """
        if self.match_text(texts, ("失败率", "决定", "休息", "赢得比赛", "VS")) is not None:
            return False
        # 赛事关卡详情页（全国大赛关卡列表）右下角常有一片白色（卡片边缘/滚动条），
        # has_story_arrow 会判成 ▼，于是每 0.5 秒点右下角（2026-10-27 实机）。
        # 这一页的强特征：右侧信息栏「关卡加成」+「奖励预览」。
        if (self.match_text(texts, "关卡加成") is not None
                and self.match_text(texts, "奖励预览") is not None):
            return False
        # 周末地图：地点名是导航，不是可推进的剧情。
        # 2026-10-26 卡死：地图上被当成剧情页连点 ▼，反复 50 秒才被看门狗救回来。
        if len([t for t in texts if t.name.strip() in Defs.WEEKEND_PLACES]) >= 2:
            return False

        # 训练事件 / 只有 ▼ 的剧情页（实机 2026-10-26）：右下角白色 ▼ 是最可靠的信号。
        # 这类页面没有长台词，旧判据认不出来，会一直卡在「没见过」。
        # 竖向三选一（如「更衣室加设空衣柜 / JellyRabbit毛绒玩具 / 了解千圣的想法」）
        # 右上角是「跳过」按钮，也没有 ▼ —— 有白底圆角横条组就点第一个。
        if (self.match_text(texts, "训练事件") is not None
                or self.has_story_arrow()
                or self._vertical_choice(texts) is not None):
            self.click(int(self.width * 0.96), int(self.height * 0.93), after_sleep=0.5)
            self.log_info("训练事件/剧情：点右下角 ▼")
            return True

        x1, y1, x2, y2 = Defs.region("dialog_text")
        lines = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            s = t.name.strip()
            # 台词是**纯文字长句**；属性面板的「弹跳B404/1350」含数字和斜杠，要排除
            if len(s) < 12:
                continue
            if any(ch.isdigit() for ch in s) or "/" in s:
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in s):
                lines.append(s)

        if not lines:
            return False
        # 一次只点一下。主循环 ~0.4 秒后就会回来重新 OCR、重新判断，
        # 本身就是重试器；在 handler 里连点反而会把「点不动」藏起来，
        # 让 run() 的「点 3 次没变化 → debug」看门狗发现不了。
        # 旧版无条件连点 3 下，对话一下就能推进时，第 2、3 下会点在空处，
        # 甚至点到刚弹出来的下一层 UI（加固训练卡片 / 确定）上
        # （2026-10-26 用户观察：屏幕中间多点两下）。
        self.click(int(self.width * 0.5), int(self.height * 0.70), after_sleep=0.45)
        self.log_info(f"剧情对话：点一下推进（{lines[0][:16] if lines else '白字兜底'}）")
        return True

    def has_story_arrow(self):
        """右下角有没有白色 ▼（剧情/训练事件的推进箭头）。"""
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = Defs.region("story_arrow")
        roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        if roi.size == 0:
            return False
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        white = (b > 225) & (g > 225) & (r > 225)
        return int(white.sum()) > 30

    def handle_lobby(self, texts):
        # 大厅判据：社团GO / 赛事，或底栏的「新人抽奖 / 转一转 / 冠军之路」。
        # 新版大厅的「社团GO」有时 OCR 不到，只靠它会在主界面空转（实训 2026-10-26）。
        if self.match_text(texts, ("赛事", "社团GO", "新人抽奖", "转一转", "冠军之路")) is None:
            return False
        action = self.texts_in_region(texts, "main_action")
        target = self.match_text(action, "训练")
        if target is not None:
            ok = self.click_and_expect(
                int(target.x + target.width / 2),
                int(target.y + target.height / 2), ("训练剧本",), timeout=8)
        else:
            x, y = self.flow_point("lobby_training")
            ok = self.click_and_expect(x, y, ("训练剧本",), timeout=8)
        self.log_info("进入训练剧本" if ok else "点「训练」没反应")
        return True

    def handle_training_entry(self, texts):
        if self.match_text(texts, "训练剧本") is None:
            return False
        ok = self.click_text_and_expect(texts, "启程", ("训练核心",), timeout=10)
        if not ok:
            # 兜底：万一 OCR 没认出「启程」，再用实测固定坐标
            x, y = self.flow_point("training_start")
            ok = self.click_and_expect(x, y, ("训练核心",), timeout=6)
        self.log_info("进入训练核心" if ok else "点「启程」没反应")
        return True

    def handle_captain(self, texts):
        """训练核心页：选队长。

        实机（2026-10-25）：右侧 2x2 四张卡，选中的人名字显示在底部中间
        （「SSR 佐久间 雨乃」）。配置 `指定队长` 填名字时，逐张点开比对，
        找到就点「下一步」；留空时沿用旧逻辑（点第一张，等级最高）。
        """
        if self.match_text(texts, "训练核心") is None:
            return False

        want = (self.config.get("指定队长", "") or "").strip()
        if want and self.pick_captain_by_name(want):
            self._captain_matched_config = True
            return True
        if want:
            self._captain_matched_config = False
            self.log_warning(f"四张队长卡里没找到「{want}」，退回第一张")

        self.flow_click("captain_card", after_sleep=0.5)
        self.log_info("选第一位队长（列表按等级降序，第一张最强）")
        # 选完队长后按钮可能移动，重新 OCR 再点「下一步」
        ok = self.click_text_and_expect(
            self._ocr_region(), "下一步", ("选择队友",), timeout=8)
        self.log_info("进入选择队友" if ok else "点「下一步」没反应")
        return True

    def pick_captain_by_name(self, want, pages=4):
        """按名字挑队长；一屏 4 张没找到就**往下翻一页**继续找。

        2026-10-30 用户提醒：选队长列表能往下翻，春澄永远不在第一屏的 4 张里；
        旧版只读 4 张，找不到就回落第一张，等于永远选不到她。
        """
        tried = 0
        for page in range(pages):
            for idx in (1, 2, 3, 4):
                rx, ry = Defs.CAPTAIN_CARD[idx]
                self.click(int(self.width_of_screen(rx)), int(self.height_of_screen(ry)),
                           after_sleep=0.7)
                name = self.read_captain_name()
                tried += 1
                self.log_info(f"队长卡 {tried}（第{page + 1}屏）：{name or '（没认出名字）'}")
                if name and want in name:
                    ok = self.click_text_and_expect(
                        self._ocr_region(), "下一步", ("选择队友",), timeout=8)
                    self.log_info(f"选定队长「{name}」" + ("" if ok else "（点下一步没反应）"))
                    return True
            if page < pages - 1:
                self.log_info(f"第{page + 1}屏没有「{want}」→ 列表往下翻")
                self.swipe(int(self.width * 0.85), int(self.height * 0.62),
                           int(self.width * 0.85), int(self.height * 0.32),
                           duration=0.4, after_sleep=0.8)
        return False

    def handle_team(self, texts):
        if self.match_text(texts, "选择队友") is None:
            return False
        auto = self.match_text(texts, "自动选择")
        if auto is not None:
            self.click_box(auto, after_sleep=0.7)
            self.log_info("队友：自动选择")
        # 自动选择后队伍从 1 人变 5 人，底部面板会整体下移 40~50px，
        # 必须重新 OCR「下一步」，不能复用点击前的坐标（实训 2026-10-26）。
        # 2026-10-29：再等 0.8 秒让面板动画走完，否则新帧读到的还是旧位置，
        # 于是每次都要等 8 秒超时、白打一条 stall5s。
        self.sleep(0.8)
        ok = self.click_text_and_expect(
            self._ocr_region(), "下一步", ("选择器材",), timeout=8)
        if not ok:
            x, y = self.flow_point("next_step")
            ok = self.click_and_expect(x, y, ("选择器材",), timeout=5)
        self.log_info("进入选择器材" if ok else "点「下一步」没反应")
        return True

    def handle_note_select(self, texts):
        """「选择笔记」页：先点「自动选择」，再点「下一步」。

        用户 2026-10-29 截图：标题「选择笔记」，右边一网格笔记（评分/属性），
        底部「重置 / 自动选择」，右下黄色「下一步」。
        口径变更（2026-10-30）：**先自动选择，再下一步**（旧口径是不选直接过）。
        判据用标题整词，避免和「器材」页混。
        """
        # 必须**整词**：确认编队页有「未选择笔记 / 请携带笔记一起训练吧」，
        # 包含匹配会把那一页也当成选择笔记页，反复点下一步（2026-10-29 实机）。
        if self.exact_text(texts, ("选择笔记",)) is None:
            return False
        if self.match_text(texts, "确认编队") is not None:
            return False

        # 2026-10-30 用户反馈「选择笔记页一直有多余点击」：
        # 页面切换有动画，handler 每拍都会重新命中同一页 → 自动选择被反复点、
        # 下一步连点好几次。用两个运行期标记限流（reset_run_state 会清零）：
        #   * 自动选择：整局只点 1 次（点完卡片高亮会变，指纹去重不可靠）；
        #   * 下一步：连续最多 2 次，之后只等切页。
        if not getattr(self, "_note_auto_done", False):
            auto = self.exact_text(texts, ("自动选择",))
            if auto is not None:
                self.click_box(auto, after_sleep=0.5)
                self._note_auto_done = True
                self.log_info("选择笔记：自动选择")
                texts = self._ocr_region()
            else:
                self.log_info("选择笔记：没找到「自动选择」，直接下一步")

        tries = getattr(self, "_note_next_tries", 0)
        if tries >= 2:
            self.log_debug("选择笔记：本页已点过下一步，等切页（不再点）")
            return True

        target = self.exact_text(texts, ("下一步",))
        if target is not None:
            x = int(target.x + target.width / 2)
            y = int(target.y + target.height / 2)
        else:
            x, y = self.flow_point("next_step")
        self._note_next_tries = tries + 1
        ok = self.click_and_expect(x, y, ("确认编队", "选择器材"), timeout=6)
        self.log_info("选择笔记：下一步" + ("（已切页）" if ok else "（等切页）"))
        return True

    def handle_equipment(self, texts):
        if self.match_text(texts, "选择器材") is None:
            return False
        auto = self.match_text(texts, "自动选择")
        if auto is not None:
            self.click_box(auto, after_sleep=0.7)
            self.log_info("器材：自动选择")
        self.sleep(0.8)          # 同上：等卡片刷新完再找「下一步」
        # 2026-10-30：新流程里器材页之后是「选择笔记」，不是直接「确认编队」。
        # 只等确认编队会白等满 8 秒超时（用户实测「发呆 16 秒」就是这里 +
        # 后面 5 秒兜底）。两个目标都算成功。
        ok = self.click_text_and_expect(
            self._ocr_region(), "下一步", ("选择笔记", "确认编队"), timeout=8)
        if not ok:
            x, y = self.flow_point("next_step")
            ok = self.click_and_expect(x, y, ("选择笔记", "确认编队"), timeout=5)
        self.log_info("器材：下一步" if ok else "点「下一步」没反应")
        return True

    def handle_confirm_team(self, texts):
        """确认编队：开始训练会扣体力且中途取消不返还，默认要用户先允许。"""
        if self.match_text(texts, "确认编队") is None:
            return False
        if not self.config.get("允许开始训练", True):
            self.log_info("已禁用自动开始训练，停在这里等你确认")
            return True
        # 注意：确认框正文里也有「开始训练」四个字（整句会被 OCR 成一个框），
        # 必须用**整词**匹配，否则会点到正文中间 (511,923)，按钮反而没点到。
        target = next((t for t in texts if t.name.strip() == "开始训练"), None)
        if target is not None:
            self.click_box(target, after_sleep=0.8)
        else:
            self.flow_click("confirm_start", after_sleep=0.8)
        # 新的一局：队长可能换了 —— 配置里填了队长就按它写流派，否则等日历解析
        self.reset_style()
        # 只有「真的选到了配置里那个队长」才套用配置流派；
        # 选的是别的卡（配置队长不在池子里）就等日历读实际队长。
        if getattr(self, "_captain_matched_config", False):
            self.apply_configured_captain()
        # 选择笔记页的限流标记清零，否则下一轮不会自动选择
        self._note_auto_done = False
        self._note_next_tries = 0
        self.log_info("开始训练（已扣体力，不可退）｜流派：" + self.style
                      + ("" if self.style_known else "（待日历识别队长）"))
        return True

    def is_pre_match_day(self, texts):
        """今天是不是「比赛前一天」（左上角倒计时 1 天 / 明天比赛）。

        实机文案：左上角「5天 赢得比赛」「1天 赢得比赛」。
        用户口径：最后一场比赛前一天要把零用钱花光（钱不带进下一场）。
        """
        banner = "".join(
            t.name for t in self.texts_in_region(texts, "today_banner")).replace(" ", "")
        if "比赛" not in banner and "VS" not in banner:
            return False
        return bool(re.search(r"1\s*天", banner)) or "明天" in banner

    def _vertical_choice(self, texts):
        """竖排三选一的选项组（剧情三选一，右上角有「跳过」）。

        与 handle_event_choice 的竖向分支同源，但这里只**找**不点：
        剧情三选一在 story_arrow 之前处理，点屏幕右下角即可推进（点了就选第一个）。
        返回选项列表（>=3 个才算）。
        """
        if self.match_text(texts, "训练计划") is not None:
            return None
        if self.match_text(self.texts_in_region(texts, "top_right"), "跳过") is None:
            return None
        ui_words = {"下一场", "下一步", "跳过", "确定", "取消",
                    "关闭", "继续", "领取", "开始训练"}
        vopts = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            s = t.name.strip()
            if s in ui_words or not s or self.is_decor(s):
                continue
            # 竖排选项是**居中**的一列（实测 story-choice: cx=0.50）；贴边的列是
            # 表格列或字幕（周结算左列 cx≈0.26、周末前对话字幕 cx≈0.68）。
            if not (0.38 <= cx <= 0.62 and 0.15 <= cy <= 0.75):
                continue
            if len(s) < 3 or len(s) > 20:
                continue
            if not self._has_white_band(t):
                continue
            vopts.append(t)
        if len(vopts) < 3:
            return None
        groups = []
        for t in sorted(vopts, key=lambda x: x.y):
            placed = False
            for g in groups:
                gx = (g[-1].x + g[-1].width / 2) / self.width
                cx = (t.x + t.width / 2) / self.width
                if abs(gx - cx) < 0.10 and 0 < (t.y - g[-1].y) / self.height < 0.25:
                    g.append(t)
                    placed = True
                    break
            if not placed:
                groups.append([t])
        groups = [g for g in groups if len(g) >= 3]
        return max(groups, key=len) if groups else None
