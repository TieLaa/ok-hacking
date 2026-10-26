"""比赛、赛程结算、技能三选一、事件选项。

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


class MatchHandlers:

    def handle_in_match(self, texts):
        """比赛内：先把手动切成自动，再连点「快进」。

        实机验证：左下角三个控件 —— 快进(图标) / 1x(倍率) / 手动·自动(文字)。
        切到自动后整场不需要人，连点快进约 12 次就能打完。
        """
        hud = self.texts_in_region(texts, "match_hud")
        if not hud:
            return False
        manual = self.match_text(hud, "手动")
        if manual is not None:
            self.click_box(manual, after_sleep=0.35)
            self.log_info("比赛：手动 → 自动")
            return True
        if self.match_text(hud, "自动") is None:
            return False
        x = int(self.width_of_screen(Defs.MATCH_FAST_FORWARD[0]))
        y = int(self.height_of_screen(Defs.MATCH_FAST_FORWARD[1]))
        self.click(x, y, after_sleep=0.6)
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

    def handle_skill_select(self, texts):
        """技能三选一：**按品质算分**（评分公式见 docs/score-model/README.md）。

        评分里技能价值：彩色 75 / 金色 50 / 白色 25（蓝、绿图标都算白）。
        所以品质是第一权重；标签是否与队长匹配只作同品质时的次级参考。
        """
        if self.match_text(texts, "请选择1个技能") is None:
            return False

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
        # 训练科目页也有黄色卡片（器材卡）和底部属性面板，但它们不是选项。
        # 科目页必有「失败率」和「决定」，事件页没有（实训 2026-10-26）。
        if self.match_text(texts, ("失败率", "决定")) is not None:
            return False
        if self.match_text(texts, ("上新时间", "能力详情")) is not None:
            return False
        if self.match_text(self.texts_in_region(texts, "main_action"),
                           ("VS", "比赛", "训练")) is not None:
            return False

        # ---- 竖向 3 选 1（白色圆角按钮）----
        # 用户规则：默认选第一个。实测 2026-10-26：三个选项文字居中竖排，
        # x 几乎相同、y 间隔 0.1~0.2，按钮本身是白底彩边，不是黄色。
        #
        # 必须要求右上角有「跳过」：训练动画页的「训练加成/表情加成/…」、
        # 训练计划日历的「星期天/星期六/…」也是竖排文字，但它们没有跳过按钮。
        # 注意：不满足条件时**不能直接 return False**，否则会连黄色二选一事件
        # 一起拒掉（旧事件页的「跳过」是灰的，OCR 有时识别不到）。
        has_skip = (self.match_text(texts, "训练计划") is None
                    and self.match_text(
                        self.texts_in_region(texts, "top_right"), "跳过") is not None)

        if has_skip:
            ui_words = {"下一场", "下一步", "跳过", "确定", "取消",
                        "关闭", "继续", "领取", "开始训练"}
            vopts = []
            for t in texts:
                cx = (t.x + t.width / 2) / self.width
                cy = (t.y + t.height / 2) / self.height
                s = t.name.strip()
                if s in ui_words or self.is_decor(s):
                    continue
                if not (0.30 <= cx <= 0.70 and 0.20 <= cy <= 0.70):
                    continue
                # 选项名允许书名号/间隔号：实测《巫术的起源与发展》也是选项，
                # 旧的「必须全中文」会把它过滤掉，导致三选一只剩两个 → 整组不命中。
                # 只排除带数字或百分比的（那是效果文案，不是选项名）。
                if not (2 <= len(s) <= 20) or re.search(r"[0-9%]", s):
                    continue
                if not self._has_white_band(t):
                    continue
                vopts.append(t)

            groups = []
            for t in sorted(vopts, key=lambda x: x.y):
                cx = (t.x + t.width / 2) / self.width
                placed = False
                for g in groups:
                    gx = (g[-1].x + g[-1].width / 2) / self.width
                    if abs(gx - cx) < 0.08 and 0 < (t.y - g[-1].y) / self.height < 0.22:
                        g.append(t)
                        placed = True
                        break
                if not placed:
                    groups.append([t])
            groups = [g for g in groups if len(g) >= 3]
            if groups:
                group = max(groups, key=len)
                pick = min(group, key=lambda t: t.y)
                self.log_info("事件选项（竖向）：" + "、".join(t.name for t in group)
                              + f" → 选第一个「{pick.name}」")
                self.click_box(pick, after_sleep=0.45)
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
            names = [t for t in inside
                     if 2 <= len(t.name.strip()) <= 8
                     and all("\u4e00" <= ch <= "\u9fff" for ch in t.name.strip())]
            effects = [t for t in inside if any(ch.isdigit() for ch in t.name)]
            name = max(names, key=lambda t: t.height).name if names else "?"
            ev = self._effect_ev(effects)
            flat_stamina = any(
                "精力" in t.name and "%" not in t.name for t in effects)
            infos.append({"x": bx, "y": by, "box": (bx, by, bw, bh),
                          "name": name, "ev": ev, "flat": flat_stamina})

        if best_kind == "v":
            pick = min(infos, key=lambda i: i["y"])
            why = "竖向排列 → 第一个"
        elif len(infos) == 2:
            # 左右二选一：先把「纯回精力」排除
            pool = [i for i in infos if not i["flat"]] or infos
            pick = max(pool, key=lambda i: (i["ev"] if i["ev"] is not None else -1,
                                            i["x"]))
            why = "二选一 → 排除纯回精力，取期望最高"
        else:
            pick = max(infos, key=lambda i: (i["ev"] if i["ev"] is not None else -1))
            why = "横向多选 → 期望最高"

        detail = "、".join(
            f"{i['name']}({('%.0f' % i['ev']) if i['ev'] is not None else '?'}"
            f"{'·回精力' if i['flat'] else ''})" for i in infos)
        self.log_info(f"事件选项：{detail} → 选「{pick['name']}」（{why}）")
        bx, by, bw, bh = pick["box"]
        self.click(int(bx + bw / 2), int(by + bh / 2), after_sleep=0.45)
        return True
