"""训练日、科目选择、加强训练。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import re
import time

from ok.feature.Box import Box

from src.tasks.ui import Defs  # noqa: F401


class TrainingHandlers:

    def handle_training_result(self, texts):
        """训练结果浮层：精力降低了X / 属性提升了 / 触发训练后事件。

        这个浮层没有「下一步」按钮，点屏幕中央或等几秒就会收起。
        旧代码认不出来，会走「没见过」兜底，每天多存 2~3 张图。
        """
        if self.match_text(texts, ("精力降低了", "属性提升了", "触发训练后事件")) is None:
            return False
        self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=0.6)
        self.log_info("训练结果：点屏幕继续")
        return True

    def handle_training_hub(self, texts):
        """训练日主界面：六维属性 + 右下角主按钮。

        只有右下角真的写着「比赛 / VS / 训练」时才点，避免在科目选择页误触。
        """
        action = self.texts_in_region(texts, "main_action")
        if self.match_text(action, ("VS", "比赛", "训练")) is None:
            return False
        self.click_main_action(after_sleep=0.5)
        return True

    def handle_vacation(self, texts):
        """度假 / 海边合宿：第 4 周期有 4 天高加成训练，界面与平时不同。

        只做最保守的推进：确认/开始训练/主按钮。遇到日历页（带「训练计划」）直接跳过，
        避免在行程表上乱点。
        """
        if self.match_text(texts, "训练计划") is not None:
            return False
        if self.match_text(texts, Defs.VACATION_TEXTS) is None:
            return False

        confirm = self.match_text(self._ocr_region("confirm"), Defs.CONFIRM_TEXTS)
        if confirm is not None:
            self.click_box(confirm, after_sleep=0.5)
            self.log_info("度假：决定")
            return True
        if self.click_text(("开始训练", "出发", "训练"), after_sleep=0.5, must=False):
            self.log_info("度假：开始训练")
            return True
        self.click_main_action(after_sleep=0.5)
        self.log_info("度假：点主按钮")
        return True

    def handle_training_plan(self, texts):
        """训练计划（日历）页：读左上角「XX 的训练计划」，定流派，然后关掉日历。

        用户 2026-10-29 口径（快速接手流程）：进训练时流派是空的，
        打开日历，左上角显示的 XX 就是队长，去角色表里查 XX 属于哪个流派，
        然后按队长的流派继续跑。

        坑（2026-10-27 00:54 实跑）：这一页同时有「野餐/划船/转动扭蛋/训练」等词，
        旧版被 handle_street 抢走 —— 当成扭蛋页「退出」，还误记了一次「步行街已去」；
        在 15:17 那局里还被 handle_park 点了一下「野餐」。
        所以这个 handler 必须排在所有地点 handler 前面（见 handlers/__init__.py）。
        """
        if not self.is_training_plan(texts):
            return False
        # 2026-10-30：每到新一天/新一周游戏都会自己弹这页。流派一旦定了就不再解析，
        # 只把日历关掉（用户口径：流派非空就不用一直看，又不会变）。
        if self.style_known:
            self.exit_page(after_sleep=0.8)
            self.log_debug(f"日历：流派已定（{self.style}）→ 直接关掉")
            return True
        name = self.read_plan_captain(texts)
        style = self.style_for_name(name) if name else None
        if style:
            self._style = style
            self._style_name = name
            self.log_info(f"训练计划：队长「{name}」→ 流派「{style}」", notify=True)
        elif name:
            # 名字看着像人名但角色表里没有（SR/R 卡没收录 / OCR 认错）：
            # 先按训练流跑，但**允许再试**（最多 3 次，见 handle_style_probe）
            self._style_name = name
            self.log_warning(f"训练计划：队长「{name}」不在角色表里 → 先按「{self.style}」跑")
        else:
            self.log_warning("训练计划：没读出队长名字 → 关掉日历，下一拍再试")
        self.exit_page(after_sleep=0.8)
        return True

    def handle_style_probe(self, texts):
        """流派还不知道（快速接手流程）→ 打开左上角的训练计划日历。

        用户 2026-10-29：全自动流程在配置里填了队长，开局就有流派，不会走到这里；
        队长留空才是快速接手：进训练后点一次日历，解析队长流派再继续。
        训练日主界面左上角那个按钮就是日历（不是返回，见 flow.exit_page 的注释），
        最多 3 次，避免别的页面乱点或无限重试。
        """
        if self.style_known:
            return False
        # 度假/海边合宿页也有「星期 + 休息」，但左上角不是日历：
        # 2026-10-29 实跑 16:41 就在度假页点了左上角（什么都没发生，白烧一次机会）。
        if self.match_text(texts, Defs.VACATION_TEXTS) is not None:
            return False
        if (self.match_text(texts, "星期") is None
                or self.match_text(texts, "休息") is None):
            return False
        tries = getattr(self, "_style_probe_tries", 0)
        if tries >= 3:
            return False
        if time.monotonic() - getattr(self, "_style_probe_at", 0.0) < 3.0:
            return False
        self._style_probe_tries = tries + 1
        self._style_probe_at = time.monotonic()
        self.click(int(self.width * 0.035), int(self.height * 0.06), after_sleep=0.8)
        if self._style_probe_tries >= 3:
            self.log_warning(
                f"流派识别 3 次都没成功 → 先按「{self.style}」跑"
                "（配置里填「指定队长」可跳过这一步）")
        else:
            self.log_info("流派未知：点左上角打开训练计划日历")
        return True

    def handle_training_day(self, texts):
        """训练日主界面：右下「训练 / 休息」。"""
        if self.match_text(texts, "星期") is None:
            return False
        if self.match_text(texts, "休息") is None:
            return False
        if self.ensure_speed(texts):
            return True
        # 2026-10-29 用户补充：野上泷周五也能出行 —— 训练日主界面上出现「出行」时，
        # 先把这次外出机会用掉（一天一次），再回来训练/休息；否则练完这天就过去了。
        # 2026-10-31 修正：只认**按钮**，而且点到了才记「今天用过了」。
        # 旧版 `click_text("出行")` 会把对话框台词「今天可以出行哦！要不」当按钮点
        # （官方日志 `left_click 今天可以出行哦！要不 (950, 182)`），按钮一下没点到，
        # 外出标记却已经置上，下一拍直接进训练（用户：「为什么没有点出行，
        # 反而默认进了训练」）。
        if self.match_text(texts, "出行") is not None:
            st = self.weekend_state()
            if not st.get("outing_done"):
                go_out = self.find_go_out_button(texts)
                if go_out is not None:
                    self.click_box(go_out, after_sleep=0.6)
                    st["outing_done"] = True
                    self.log_info("训练日有「出行」按钮 → 先外出（一天一次）")
                    return True
                line = self.match_text(texts, "出行")
                if line is not None and len(line.name.strip()) > 4:
                    # 按钮还没出来，这一拍只推进对话（不算用掉当天机会）
                    self.click_box(line, after_sleep=0.6)
                    self.log_info("训练日：对话框提到「出行」但还没按钮 → 先推进对话")
                    return True
        state = self.top_bar()
        cur = state.get("stamina")
        # 周五按流派分档（用户 2026-10-29）：训练流不变，其他流派 55，商店流 80
        limit = self.rest_threshold(state.get("day"))
        action = self.texts_in_region(texts, "main_action")
        force_rest = self._force_rest
        if force_rest:
            self._force_rest = False
            self.log_info("失败率过高，强制休息")
        if force_rest or (cur is not None and cur < limit):
            target = self.match_text(action, "休息")
            if target is not None:
                self.click_box(target, after_sleep=0.6)
            else:
                self.flow_click("day_rest", after_sleep=0.6)
            if not force_rest:
                self.log_info(f"体力 {cur} < {limit}（{self.style}·星期{state.get('day')}）→ 休息")
        else:
            target = self.match_text(action, "训练")
            if target is not None:
                self.click_box(target, after_sleep=0.6)
            else:
                self.flow_click("day_train", after_sleep=0.6)
            self.log_info(f"体力 {cur}，进入训练科目")
        return True

    def _boost_text_cards(self, texts):
        """把「文字效果版」加强训练的两张卡按左右分半，算各自的分数。

        返回 (left, right)，每个是 {"x","y","label","score"} 或 None。
        判据（用户口径：优先级 = 属性/等级 > 精力）：
        - 出现「等级 / 属性 / 训练」→ 100 分（这类是给训练等级或六维的）；
        - 只有「精力 / 体力」→ 0 分（纯回精力，不选）；
        - 其它能用 `_effect_ev` 读出 +N 的，按真实属性总量算。
        """
        h = self.height
        bands = {"left": [], "right": []}
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / h
            if not (0.05 <= cx <= 0.95 and 0.25 <= cy <= 0.88):
                continue
            if len(t.name.strip()) < 2:
                continue
            if any(k in t.name for k in ("加强训练", "球队经理")):
                continue                     # 标题，不算卡片内容
            if t.name.strip() in ("失败", "成功", "确定", "取消"):
                continue                     # UI 词，不是卡片效果
            bands["left" if cx < 0.5 else "right"].append(t)

        def score_of(items):
            # 统一口径（同事件选项）：回精力=flat→0 分；其余按期望值
            ev, flat = self.effect_value(items)
            if flat:
                return 0.0
            return ev if ev is not None else 1.0

        out = []
        for name, key in (("left", "left"), ("right", "right")):
            items = bands[key]
            if not items:
                # 2026-10-30 实跑：页面切换途中 OCR 只剩标题，「这一半」一个框都没有，
                # 下面 sum(...) / len(items) 直接 ZeroDivisionError，整个 handler 中断。
                out.append(None)
                continue
            sc = score_of(items)
            if sc is None:
                out.append(None)
                continue
            # 点这一半的中心偏下一点（卡片正文区）
            cx = sum(t.x + t.width / 2 for t in items) / len(items) / self.width
            label = max(items, key=lambda t: len(t.name.strip())).name.strip()
            out.append({"x": min(max(cx, 0.12), 0.88), "y": 0.55,
                        "label": label, "score": sc})
        return out[0], out[1]

    def handle_subject_select(self, texts):
        """训练科目页：先探测带队友小人的科目，再按「队友 + 装备 + 满经验暴击」选。

        实机（2026-10-26）：
        - 每点一个科目，被选中的科目会移到右上「大按钮」位，其余按钮整体上移，
          所以**不能复用上一帧的坐标**，每次探测都要重新 OCR 找名字；
        - 预览界面会显示：场上角色名（队友）、底部 1~3 张器材卡、器材经验条；
        - **器材满经验**：卡片底部变成「MAX」，整张卡和右上大按钮带彩虹边框，
          训练时触发属性暴击 —— 这是最高优先级的加分项。
        """
        subjects = self.texts_in_region(texts, "subject_list")
        if self.match_text(subjects, Defs.TRAINING_SUBJECTS) is None:
            return False
        # 训练科目页铁定有「失败率」；商店右栏也有「防守秘籍+/防守+40」，
        # 只按科目名匹配会把商店当成科目页（实训 2026-10-26，400 步全耗在商店里）。
        if self.match_text(texts, "失败率") is None:
            return False
        if self.match_text(texts, ("上新时间", "能力详情")) is not None:
            return False
        # 扭蛋/加成页的说明文字里也会出现「体能」「投射」等词（例：「所有小游戏额外获得20点体能」），
        # 不排除掉就会被当成科目页，然后疯狂点卡片、点确定（实训 2026-10-25 死循环）。
        if self.match_text(texts, ("扭蛋", "本局训练中", "小游戏强化", "器材熟练度", "熟练度。")) is not None:
            return False

        # 已经点过「决定」但界面还没切走：补点决定，别重新探测一遍
        if time.monotonic() - getattr(self, "_subject_confirmed_at", 0) < 2.0:
            if self.click_text(("决定",), after_sleep=0.6, must=False):
                return True

        fail = self.read_fail_rate(texts)
        if fail is not None:
            self.info_set("当前失败率", f"{fail}%")
        limit = int(self.config.get("失败率上限(%)", 50))
        if fail is not None and fail > limit:
            if self.config.get("失败率超限改休息", True):
                self._force_rest = True
                self.log_warning(f"失败率 {fail}% > {limit}% → 退出科目页，回主界面休息")
                # 右上角返回箭头回训练日主界面；下一拍 handle_training_day 强制休息
                self.exit_page(after_sleep=0.8)
                return True
            self.log_warning(f"失败率 {fail}% 超过上限 {limit}%（配置为不休息，继续）")

        # 用 subjects_from_texts：它会把被 OCR 拆成两个字的科目名合起来
        # （2026-10-27 第 4 周度假页「防守」被读成「防」「守」→ 整个科目漏掉）
        boxes = self.subjects_from_texts(texts)
        # 按屏幕从上到下探测（旧版按配置表扫，看起来像随机乱点 —— 用户 2026-10-26）
        boxes = dict(sorted(boxes.items(), key=lambda kv: kv[1].y))

        if not boxes:
            self.log_warning("认不出任何训练科目")
            return False

        # 候选科目 = 有橙色小人 **或** 按钮外圈已经亮（金框/彩框）**或** 当前已选。
        # 2026-10-27 16:48 事故：当前已选「投射」预读 队友 2｜经理 1｜装备 3｜
        # 满经验 1｜金框 1 = 8.30，但小人图标漏判 → 不在 with_team → 连候选都不是，
        # 最后选了 1.00 的「防守」。用户指出：**金框已经识别到了，是筛选逻辑的锅**，
        # 所以候选池必须把「圈已经亮了」的科目一起收进来，而不是只信小人图标。
        icons = self.find_teammate_icons()
        with_team = [n for n in boxes if self.has_teammate_icon(boxes[n], icons)]
        ring_hit = [n for n in boxes
                    if self.has_gold_border(texts, subject=n, quiet=True)
                    or self.has_crit_border(texts, subject=n, quiet=True)]
        current = self.current_subject(texts)
        candidates = [n for n in boxes
                      if n in with_team or n in ring_hit or n == current]
        self.log_info("候选科目（小人 " + ("、".join(with_team) if with_team else "无")
                      + "｜圈亮 " + ("、".join(ring_hit) if ring_hit else "无")
                      + "｜当前 " + (current or "无") + "）：" + "、".join(candidates))

        # 当前已经选中的科目（右边有「决定」的那一行）不要点：点它有直接开练的风险。
        # 它的预览信息直接读当前帧（彩虹边框在开页第一帧就有，器材卡随后出现）。
        results = {}
        if current in boxes:
            results[current] = self.analyze_preview(texts, subject=current)
            r = results[current]
            self.log_info(
                f"当前已选「{current}」：队友 {r['mates']}｜经理 {r['manager']}"
                f"｜装备 {r['equip']}｜满经验 {r['full']}｜金框 {r['gold']}"
                f"｜彩虹 {int(r['crit'])}｜得分 {self.subject_score(r):.2f}")

        last_probed = None
        for name in candidates:
            if name == current:
                continue
            box = self.find_subject_box(name)
            if box is None:
                # 2026-10-29 度假页：候选里有「防守」，但重新 OCR 时那一行被读成
                # 「防 + MAX」、「守」丢了 → find_subject_box 找不到就静默跳过，
                # 日志里看不出少测了一个科目。改成告警，并把没探测到的从候选池剔除。
                self.log_warning(f"找不到「{name}」的按钮框，跳过（未参与打分）")
                continue
            results[name] = self.probe_subject(box)
            last_probed = name
            r = results[name]
            self.log_info(
                f"探测「{name}」：队友 {r['mates']}｜经理 {r['manager']}"
                f"｜装备 {r['equip']}｜满经验 {r['full']}｜金框 {r['gold']}"
                f"｜彩虹 {int(r['crit'])}｜得分 {self.subject_score(r):.2f}")

        attr_values = self.read_attribute_values(texts)
        if attr_values:
            self.info["attrs"] = attr_values   # 监控页「六维 / 分数预测」用
            self.log_debug(f"六维当前值：{attr_values}")

        def rank(name):
            """分数 → 当前数值低的 → 屏幕上靠前的（兜底）。推荐属性已取消。"""
            return self.subject_rank(self.subject_score(results.get(name)),
                                     attr_values.get(name),
                                     boxes[name].y)

        # 候选池 = 小人 / 金边 / 彩边 / 当前已选（用户 2026-10-27：三种信号都算）
        # 但只保留**真正探测过**的科目：探测失败的名字没有分数，留在池里等于按 0 分
        # 跟别人比，还可能被选去盲点；一个都没探到时退回当前已选，别乱点。
        pool = [n for n in candidates if n in results]
        if not pool:
            pool = [current] if current in boxes else list(boxes)
        name = max(pool, key=rank)
        r = results.get(name) or self.empty_preview()
        # 2026-10-27：这一行必须同时打「金框」和「彩虹」。
        # 代码里彩框会让金框恒为 0（互斥），只打金框时彩框会被统计成「无框」，
        # 事后无法校准彩框权重（实测 118 次训练里彩框就是这么丢的）。
        self.log_info(
            f"选科目「{name}」（队友 {r['mates']}｜经理 {r['manager']}｜装备 {r['equip']}"
            f"｜满经验 {r['full']}｜金框 {r['gold']}｜彩虹 {int(r['crit'])}"
            f"｜得分 {self.subject_score(r):.2f}"
            f"｜当前值 {attr_values.get(name, '?')}）")

        # 若最佳科目不是当前选中的那个，先点一下选中（点两下会直接开练，
        # 所以统一只点一下，再按右上角「决定」）
        selected = last_probed or current
        if name != selected:
            box = self.find_subject_box(name)
            if box is not None:
                self.click_box(box, after_sleep=0.45)

        if not self.click_text(("决定",), after_sleep=0.8, must=False):
            box = self.find_subject_box(name)
            if box is not None:
                self.click_box(box, after_sleep=0.6)
        self._subject_confirmed_at = time.monotonic()
        self.log_info(f"训练「{name}」（失败率 {fail}%）")
        return True

    def find_subject_box(self, name):
        """重新 OCR 当前帧，按名字找科目框（选中后按钮会整体上移，不能复用旧坐标）。

        必须**整词匹配**：match_text 的「拼接兜底」在没有匹配时会返回最大的那个框
        （2026-10-27 17:04 实跑：找「防守」返回了「失败率0%」框，点到 (895,118)，
        等于没点开科目，预览还是别人的）。
        """
        return self.subjects_from_texts(self._ocr_region()).get(name)

    def handle_pre_match_shop(self, texts):
        """比赛日主界面：进比赛之前先逛一次商店（只有训练流/商店流）。

        用户 2026-10-29 口径：周一比赛之前还能买一次商品再进比赛；
        周日首页的「先逛店」逻辑已删除，改成这里。

        判据（2026-10-30）：训练日主界面（有「星期」）+ 右下主按钮区出现「比赛/VS」。
        **不能按「周一」判断**：训练日也可能是周一，实测训练日 main_action 区域为空。
        """
        if self.style not in ("训练流", "商店流"):
            return False
        # 2026-10-29：比赛日主界面不一定有「休息」（当天只剩比赛），所以不要求它
        if self.match_text(texts, "星期") is None:
            return False
        # 判据：右下主按钮区出现「比赛」**且**画面上有 VS/跳过比赛。
        # 只用「比赛」不够：训练日主界面的 main_action 区域也会出现「比赛」
        # （fixture 2026-10-25-hover-tooltip：比赛 @(0.89,0.86)，但那是训练日）。
        # 比赛日实测多一个 VS（对话区 @(0.38,0.11)）或「跳过比赛」按钮。
        main_text = self.match_text(self.texts_in_region(texts, "main_action"),
                                    ("比赛", "VS")) is not None
        if not main_text:
            return False
        if self.match_text(texts, ("VS", "跳过比赛")) is None:
            return False
        if self.match_text(texts, ("点击屏幕继续", "目标达成", "获得奖励")) is not None:
            return False

        state = self.top_bar()
        day = state.get("day")
        shop = self.find_shop_button(texts)
        key = (state.get("week"), day)
        if shop is None:
            # 认成比赛日却找不到商店入口：存一帧，下次复盘直接看这张
            # （遗留问题就是缺「比赛日主界面」的证据帧）。
            if key != getattr(self, "_prematch_noshop_key", None):
                self._prematch_noshop_key = key
                self.log_warning(
                    f"比赛日（周{day or '?'}）没找到右上角「商店」按钮 → 已存证")
                self.dump_debug(texts, f"prematch_noshop_{day or 'x'}")
            return False
        if key[1] is not None:
            if key == getattr(self, "_shop_day", None):
                return False            # 今天已经真进过店
            if key == getattr(self, "_shop_try_key", None) and \
                    getattr(self, "_shop_try_count", 0) >= 2:
                return False            # 今天点了两次都没打开，别再空点
        else:
            # 星期读不到：退化成「至少隔 60 秒再进一次」，不要整天都不进
            if time.monotonic() - getattr(self, "_shop_at", 0.0) < 60:
                return False
        # 这里**不能**直接写 _shop_day：点完不等于进店。2026-10-30 15:04:04 那下
        # 点在顶栏「新品上市」上、店面没开，但 _shop_day 已经记账 → 当天再也没进店。
        # 记账改到 handle_shop 确认商店页真的出现之后。
        self._shop_at = time.monotonic()
        if key == getattr(self, "_shop_try_key", None):
            self._shop_try_count = getattr(self, "_shop_try_count", 0) + 1
        else:
            self._shop_try_key = key
            self._shop_try_count = 1
        # 赛前清空：比赛当天把零用钱花光（配置开关）
        self._shop_spend_all = bool(self.config.get("赛前清空余额", False))
        self.click_box(shop, after_sleep=0.9)
        self.log_info(f"比赛日（周{day or '?'}）：{self.style}先进店买一次"
                      + ("（赛前清空）" if self._shop_spend_all else ""))
        return True

    def find_shop_button(self, texts):
        """比赛日右上角的商店入口按钮。

        2026-10-30 两次实跑的教训：
        * logs/debug/1030-150147_prematch_noshop_一/ocr.txt：OCR 把「商店」拆成
          「商」(1396,178)「店」(1426,178) 两个单字，中心 y=0.216 —— 旧版用
          top_right（y<0.20）去筛，两个字被切在区域外，整局不进店。
        * 同一块区域里还有顶栏的「新品上市」(1421,101)，旧版把「新品/新品上市」
          当候选，结果点在 y=0.10 的导航条上，店面根本没打开（15:04:04 那次）。
        所以：区域换成 shop_entry（放宽到 y<0.32），去掉「新品」类候选，
        「商」+「店」单字合并排最前 —— 那才是入口按钮本身。
        """
        from ok.feature.Box import Box
        area = self.texts_in_region(texts, "shop_entry")
        singles = [t for t in area if t.name.strip() in ("商", "店")]
        left = next((t for t in singles if t.name.strip() == "商"), None)
        right = next((t for t in singles if t.name.strip() == "店"), None)
        if left is not None and right is not None and right.x > left.x:
            if abs((left.y + left.height / 2) - (right.y + right.height / 2)) < left.height:
                x = min(left.x, right.x)
                y = min(left.y, right.y)
                w = max(left.x + left.width, right.x + right.width) - x
                h = max(left.y + left.height, right.y + right.height) - y
                return Box(name="商店", x=x, y=y, width=w, height=h)
        for name in ("商店", "商品", "商品手册", "手册"):
            btn = self.exact_text(area, (name,))
            if btn is not None:
                self.log_debug(f"商店按钮：整词命中「{name}」")
                return btn
        # 包含匹配兜底（OCR 拼接/多字）：长度限制住，别把正文当按钮
        for t in area:
            name = t.name.strip()
            if ("商店" in name or "商品" in name) and len(name) <= 6:
                return t
        # 只剩一个单字（另一个字没识别出来）：点这个字，总比整局不进店强
        single = left or right
        if single is not None:
            return Box(name="商店", x=single.x, y=single.y,
                       width=single.width, height=single.height)
        return self.exact_text(texts, ("商店",))

    def handle_boost_training(self, texts):
        """加强训练二选一：按 **收益 × 成功率** 的期望值选（2026-10-26 用户口径）。

        实测 36×0.8=28.8 > 12×1.0=12；30×0.95=28.5、30×0.65=19.5 也都高于 10×1.0。
        点选中卡片的成功率文字中心，再点「确定」——不要点两张卡中间的空白。

        2026-10-27 用户录屏反馈：每张卡上其实有**两组数字**（卡顶气泡的「100%」+
        卡内大号「+36」，卡内还有一个小号同名数字），旧写法「每个百分比各算一张卡」
        会把同一张卡算成两张，还会出现「100%/+12、80%/+36」这种凭空的成功率。
        现在先按 x 坐标把数字**聚类成列**（一张卡一列），每列只取一组。
        """
        if self.match_text(texts, "加强训练") is None:
            return False

        rates = [t for t in texts if Defs.PERCENT_RE.match(t.name.strip())]
        gains = [t for t in texts if Defs.GAIN_RE.match(t.name.strip())]
        if not rates:
            # 2026-10-30：这一版加强训练是**文字效果**（「训练等级提升 / 精力提升+30」），
            # 没有百分比。以前直接点「确定」= 吃默认（正好是回精力那张）。
            # 现在按左右两半算分：等级/属性类算 100 分，纯「精力提升」排除，
            # 其余读得到 +N 的用 _effect_ev。
            left, right = self._boost_text_cards(texts)
            cards = [c for c in (left, right) if c is not None]
            if cards:
                best = max(cards, key=lambda c: (c["score"], -c["x"]))
                self.click(int(best["x"] * self.width),
                           int(best["y"] * self.height), after_sleep=0.35)
                self.log_info("加强训练（文字效果）："
                              + "｜".join(f"{c['label']}={c['score']:.0f}"
                                          for c in cards)
                              + f" → 选「{best['label']}」")
                self.click_text(("确定",), after_sleep=0.6, must=False)
                return True
            # 实在认不出卡片：不盲点卡片，先按默认选择点「确定」，下一拍再判
            self.click_text(("确定",), must=False)
            return True

        def cluster(items, tol=0.08):
            """按 x 聚类成一列（同一张卡的所有数字）。返回 [(中心 x, [框...]), ...]。"""
            cols = []
            for t in sorted(items, key=lambda b: b.x):
                cx = (t.x + t.width / 2) / self.width
                if cols and abs(cx - cols[-1][0]) < tol:
                    cols[-1][1].append(t)
                else:
                    cols.append([cx, [t]])
            return [(c[0], c[1]) for c in cols]

        rate_cols = cluster(rates)
        gain_cols = cluster(gains)
        self.log_debug(
            "加强训练：百分比列 " + "｜".join(
                f"{c:.2f}:{','.join(b.name.strip() for b in bs)}" for c, bs in rate_cols)
            + " 收益列 " + "｜".join(
                f"{c:.2f}:{','.join(b.name.strip() for b in bs)}" for c, bs in gain_cols))

        picks = []
        for cx, group in rate_cols:
            # 同一列里取字号最大的那个（大号才是卡片主数字）
            rate_box = max(group, key=lambda b: b.height)
            p = int(Defs.PERCENT_RE.match(rate_box.name.strip()).group(1))
            gain = None
            if gain_cols:
                _gcx, ggroup = min(gain_cols, key=lambda c: abs(c[0] - cx))
                if abs(_gcx - cx) < 0.20:
                    gain_box = max(ggroup, key=lambda b: b.height)
                    gain = gain_box
            v = int(Defs.GAIN_RE.match(gain.name.strip()).group(1)) if gain else p
            picks.append((v * p / 100.0, v, p, cx))
        if not picks:
            return False

        # 2026-10-29 用户：训练等级上调后加强训练从 2 张变 3 张。
        # 这里本来就是按列聚类、逐列算期望，2/3 张通用；只有「列数 < 2」或
        # 「同一列出现多组数字」才说明 OCR 把别的数字算进来了。
        if len(rate_cols) < 2 or any(len(bs) > 1 for _c, bs in rate_cols):
            # 数字列数不对 = 大概率 OCR 把卡上别的数字算进来了，存证一张人工核对
            self.log_warning(
                f"加强训练：识别到 {len(rate_cols)} 个百分比列"
                f"（{'；'.join(','.join(b.name.strip() for b in bs) for _c, bs in rate_cols)}），已存证")
            self.dump_unknown(texts, "boost")

        ev, v, p, card_cx = max(picks, key=lambda x: x[0])
        # 点**卡片正中**：只点成功率文字框（在卡片顶部）实测会落在两卡之间偏上的位置
        # （用户 2026-10-27 录屏：两张卡中间出现了一次多余点击）。
        self.click(int(card_cx * self.width), int(self.height * 0.5), after_sleep=0.35)
        self.click_text(("确定",), after_sleep=0.6)
        detail = "  ".join(f"{pp}%/+{vv}={e:.0f}" for e, vv, pp, _cx in picks)
        self.log_info(f"加强训练：{detail} → 选 {p}%/+{v}")
        return True


