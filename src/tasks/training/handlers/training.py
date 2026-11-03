"""训练日、科目选择、加强训练。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import re
import time

from ok.feature.Box import Box

from src.tasks.ui import labels


class TrainingHandlers:

    # 训练日「出行」按钮是页面淡入后出现的：实测（2026-10-31 19:31 / 2026-11-02
    # 01:03 起共 13 次）**下一拍**（约 0.45 秒）就能 OCR 到。
    # 台词「今天可以出行哦！要不」是静态提示，点它什么都不会推进
    # （用户 2026-11-02：队长的头像右边的对话框里出现了无用的空点击），
    # 所以先等几拍；等不到才退回「点台词推进」这条老路。
    GO_OUT_WAIT_TICKS = 2

    # 训练日主界面的「决策拍」同样要躲开淡入：整页刚出现的那一拍，右侧「出行」
    # 还没画出来，`outing_offer` 返回 None → 直接按默认去训练（专注一去不回）
    # 或去休息（这一天就过去了）。实测 2026-11-02 08:56:47 / 08:57:36 两次都是
    # 头一拍没读到「出行」，隔一拍（08:57:38）就读到了。
    # 代价：每天多等一拍（≈0.5 秒）。换的是一次不可撤销的错误点击。
    HUB_SETTLE_TICKS = 1

    # 加强训练（「球队经理的帮助」那页）同样要躲淡入：头一拍只观察不点。
    # 2026-11-02 用户实机反馈「两次空点击」：第一拍卡片还在滑入，点下去不生效，
    # 紧接着的「确定」也就白点了。
    #
    # 等待时长**可调**（用户 2026-11-02：「等待时间减少 1 秒，看看效果」）：
    # 旧值 0.35 + (0.55-0.35) + 0.35 ≈ 0.9 秒额外等待，现在 ≈ 0.05 秒，
    # 剩下的「躲淡入」靠**跳过一整拍**（BOOST_SETTLE_TICKS）实现，不再叠加 sleep。
    # 调这页速度只改这三个数，别再去动 handler 结构。
    BOOST_SETTLE_TICKS = 1        # 头一拍只观察、不点（躲淡入，别设 0）
    BOOST_SETTLE_SLEEP = 0.0      # 那一拍不额外等（原 0.35）
    BOOST_CARD_SLEEP = 0.40       # 点完卡片的等待（原 0.55）
    BOOST_CONFIRM_GAP = 0.05      # 卡片→确定 的额外间隔（原 0.35）
    BOOST_CONFIRM_LOCK = 1.2      # 点过确定后的静默期（原 2.0）

    def handle_training_result(self, texts):
        """训练结果浮层：精力降低了X / 属性提升了 / 触发训练后事件。

        这个浮层没有「下一步」按钮，点屏幕中央或等几秒就会收起。
        旧代码认不出来，会走「没见过」兜底，每天多存 2~3 张图。
        """
        if self.match_text(texts, ("精力降低了", "属性提升了", "触发训练后事件")) is None:
            return False
        # 2026-11-02：这页点一下就该收起，页面正在淡出时再点就是**空点击**
        # （用户反馈「经理帮助事件连着两次空点击」的第一下就是 22:34:49 这次）。
        # 加个短锁：点过就等 1.5 秒，等不到新的一帧才允许再点。
        if time.monotonic() - getattr(self, "_result_click_at", 0.0) < 1.5:
            self.sleep(0.3)
            return True
        self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=0.6)
        self._result_click_at = time.monotonic()
        self.log_info("训练结果：点屏幕继续")
        return True

    def handle_note_study(self, texts):
        """笔记/球星卡「学习」页：**不可跳过的动画 + 右下角「学习」按钮**。

        用户 2026-11-02 口径：这是**开局装备的笔记**触发的界面，动画跳不过，
        动画放完右下角才出现「学习」按钮，点它才算过、才能拿加成。

        坑（2026-11-02 01:22 实跑，用户当场看到）：
        - 动画期间 OCR 只剩「契合度：优 / 评分 / 技能名」，没有一个 handler 认 →
          落 unknown 兜底，**点屏幕中央两次空点击**；
        - 更糟：这页带「评分」，被 handle_street 当成扭蛋详情页 →
          **点右上角「返回」找关闭键**。
        所以这个 handler 必须排在 handle_street / handle_event_choice 之前。

        两条动作：
        - 读到整词「学习」→ 点它**真实的文本框**（不写死坐标，按钮位置随分辨率变）；
        - 只有「契合度」、还没读到按钮 → **等**，一下都不点（动画没放完，点了是空点击）。

        同一页最多点 3 下：点完游戏要放过场，这一拍 OCR 可能还是旧帧；
        不限次数就会变成连点（用户 2026-11-02 说的「空点击」）。
        """
        if self.match_text(texts, labels.NOTE_STUDY_MARKS) is None:
            return False
        # 选卡页（选择笔记/选择器材）也可能弹出卡片详情带「契合度」——
        # 那是开局流程的页，交给 handle_note_select / handle_equipment，别在这儿等。
        if self.match_text(texts, ("选择笔记", "选择器材")) is not None:
            return False
        clicked = getattr(self, "_note_study_clicks", 0)
        if self.exact_text(texts, labels.BUTTONS["study"]) is not None:
            if clicked >= 3:
                self.log_debug("笔记学习页：本页已点 3 下「学习」，等过场")
                self.sleep(0.5)
                return True
            self._note_study_wait = 0
            self._note_study_clicks = clicked + 1
            self.click_button("study", texts=texts, after_sleep=0.8)
            self.log_info(f"笔记学习：点「学习」完成触发事件（第 {clicked + 1} 下）")
            return True
        # 动画中：等按钮出来。等太久（10 拍≈10 秒）就放手交给兜底，避免死等。
        self._note_study_wait = getattr(self, "_note_study_wait", 0) + 1
        if self._note_study_wait > 10:
            self.log_warning("笔记学习页：等了 10 拍仍没读到「学习」→ 交给兜底")
            return False
        self.log_debug(f"笔记学习页：动画中，等「学习」按钮（第 {self._note_study_wait} 拍）")
        self.sleep(0.5)
        return True

    def handle_training_hub(self, texts):
        """训练日主界面：六维属性 + 右下角主按钮。

        只有右下角真的写着「比赛 / VS / 训练」时才点，避免在科目选择页误触。

        六维不需要在这里读：这一页的刷新点是 `handle_training_day` 的决策拍
        （`parsers.snapshot_lounge`，每次进休息室拍一次）。
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
        if self.match_text(texts, labels.VACATION_TEXTS) is None:
            return False

        confirm = self.match_text(self._ocr_region("confirm"), labels.CONFIRM_TEXTS)
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

    def handle_training_day(self, texts):
        """训练日主界面：右下「训练 / 休息」。

        **判断在 `training/daycycle.py`**（用户 2026-11-01 的架构）：
        「今天练还是休」「今天要不要外出」这两个**新一天入口**是同级的，
        都归那个模块；这里只负责「照它说的做」。
        """
        if self.match_text(texts, "星期") is None:
            return False
        if self.match_text(texts, "休息") is None:
            return False
        # 这一页的第几次命中（0 = 刚出现的第一拍）。离开这页时由 LEAVE_RESETS 清零。
        self._hub_ticks = getattr(self, "_hub_ticks", 0) + 1
        hub_seen = self._hub_ticks - 1
        # 头一拍不碰倍速：整页还在淡入时，右上角图标可能还没画出来，
        # 像素判据会把「没画出三角」读成「还有空心三角」（2026-11-02 23:41:34
        # 炒股页回来后的第一拍就这么把 3x 点成了 OFF）。下一页拍图标就位了。
        if hub_seen >= self.HUB_SETTLE_TICKS and self.ensure_speed(texts):
            return True
        # 2026-10-29 用户补充：野上泷周五也能出行 —— 训练日主界面上出现「出行」时，
        # 先把这次外出机会用掉（一天一次），再回来训练/休息；否则练完这天就过去了。
        action, reason = self.outing_offer(texts)
        if action == "点按钮":
            self._outing_wait = 0
            button = self.find_go_out_button(texts)
            self.click_box(button, after_sleep=0.6)
            self.mark_outing_used()
            self.log_info(f"训练日有「出行」按钮 → 先外出（一天一次）：{reason}")
            return True
        if action == "推进对话":
            # 只等按钮淡入，**不点台词**：台词点不出任何东西，
            # 反而在对话框里留一下空点击（用户 2026-11-02 反馈）。
            self._outing_wait = getattr(self, "_outing_wait", 0) + 1
            if self._outing_wait <= self.GO_OUT_WAIT_TICKS:
                self.log_info(f"训练日：{reason}（先等按钮淡入，不点台词）")
                return True
            line = self.match_text(texts, "出行")
            self.click_box(line, after_sleep=0.6)
            self.log_info(f"训练日：{reason}"
                          f"（等 {self.GO_OUT_WAIT_TICKS} 拍还没有按钮 → 才点台词）")
            return True
        self._outing_wait = 0
        # 头一拍不决策：右侧「出行」还在淡入，OCR 读不到它。
        # 只有「今天还没出过行」时才等 —— 已经出过的日子不必为空等。
        if hub_seen < self.HUB_SETTLE_TICKS and not self.outing_used():
            self.log_info(f"训练日：头一拍先不决策（{reason}）→ 等「出行」按钮淡入")
            self.sleep(0.5)
            return True
        # 进休息室 = 训练日主界面出现。**只在这一刻**给监控页拍一次快照：
        # 六维 + 精力 + 零用钱（2026-11-03 用户口径）。
        # 校门口/地图没有六维面板；周末加的是零碎属性，回头累加对不上面板。
        # 放在「决策拍」之后：躲开整页淡入时读到的半截数字。
        # 一次性由 `_lounge_snapshot_done` 保证，离开页面时由 LEAVE_RESETS 清掉。
        if not getattr(self, "_lounge_snapshot_done", False):
            self._lounge_snapshot_done = True
            self.snapshot_lounge(texts)
        action, reason = self.training_day_action(texts)
        area = self.texts_in_region(texts, "main_action")
        if action == "休息":
            target = self.match_text(area, "休息")
            if target is not None:
                self.click_box(target, after_sleep=0.6)
            else:
                self.flow_click("day_rest", after_sleep=0.6)
        else:
            target = self.match_text(area, "训练")
            if target is not None:
                self.click_box(target, after_sleep=0.6)
            else:
                self.flow_click("day_train", after_sleep=0.6)
        self.log_info(reason)
        return True

    def _boost_text_cards(self, texts):
        """把「文字效果版」加强训练的两张卡按左右分半，算各自的分数。

        返回 (left, right)，每个是 {"x","y","label","score"} 或 None。
        判据（用户口径：优先级 = 属性/等级 > 精力）：
        - 出现「等级 / 属性 / 训练」→ 100 分（这类是给训练等级或六维的）；
        - 只有「精力」（含别名「体力」）→ 0 分（纯回精力，不选）；
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
        if self.match_text(subjects, labels.TRAINING_SUBJECTS) is None:
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
            if self.click_button("decide", after_sleep=0.6, must=False):
                return True

        fail = self.read_fail_rate(texts)
        if fail is not None:
            self.info_set("当前失败率", f"{fail}%")
        limit = int(self.config.get("失败率上限(%)"))
        if fail is not None and fail > limit:
            if self.config.get("失败率超限改休息"):
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

        # 六维当前值：进休息室时已经拍过快照（`parsers.snapshot_lounge`），
        # 这里**优先用那一份**，只有独立调用（回归 / 不带主循环）时才现场补读一次。
        # 注意：这里读它只为了排行「当前值低的科目优先」，**不是**面板的刷新点。
        attr_values = dict(self.info.get("attrs") or {}) or self.read_attribute_values(texts)

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

        if not self.click_button("decide", after_sleep=0.8, must=False):
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
        self._shop_spend_all = bool(self.config.get("赛前清空余额"))
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

        # 2026-11-02 用户实机反馈：「每次经理的帮助事件，鼠标都有两次空点击——
        # 第一下点卡片没点下去，然后又点了继续又没点中」。日志（22:34:49~52）：
        #   22:34:49 训练结果：点屏幕继续      ← 页面在切换，中央是空的
        #   22:34:51/52 连点两次「确定」       ← 卡片那一下没生效，确定也跟着白点
        # 两个原因，各修一处：
        # 1. **页面淡入那一拍就点**：卡片还在滑入，点下去不生效 → 头一拍只观察；
        # 2. **点完卡片立刻点确定**（0.35s）：选中还在动画里 → 卡片与确定之间留出
        #    间隔，并且确定只点一次（`_boost_confirmed_at` 锁），避免下一拍重复点。
        # 计数**只在离开这页时**清零（LEAVE_RESETS），不要按「指纹变了」清零：
        # 这页的百分比/收益数字每次 OCR 都可能少一个字，指纹一变就重新「等一拍」，
        # 实测同一页会连打两次「头一拍先不点」，白等两拍（2026-11-02 22:57 日志）。
        self._boost_ticks = getattr(self, "_boost_ticks", 0) + 1
        if self._boost_ticks <= self.BOOST_SETTLE_TICKS:
            self.log_info("加强训练：头一拍先不点（卡片还在淡入）→ 等一拍")
            self.sleep(self.BOOST_SETTLE_SLEEP)
            return True
        if (time.monotonic() - getattr(self, "_boost_confirmed_at", 0.0)
                < self.BOOST_CONFIRM_LOCK):
            # 刚点过「确定」，页面正在切走：这一拍什么都别点
            self.log_debug("加强训练：已点过确定，等页面切走（不重复点）")
            self.sleep(0.3)
            return True

        rates = [t for t in texts if labels.PERCENT_RE.match(t.name.strip())]
        gains = [t for t in texts if labels.GAIN_RE.match(t.name.strip())]
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
                           int(best["y"] * self.height),
                           after_sleep=self.BOOST_CARD_SLEEP)
                self.log_info("加强训练（文字效果）："
                              + "｜".join(f"{c['label']}={c['score']:.0f}"
                                          for c in cards)
                              + f" → 选「{best['label']}」")
                self.sleep(self.BOOST_CONFIRM_GAP)   # 同上：等选中生效再点确定
                self.click_button("confirm", after_sleep=0.6, must=False)
                self._boost_confirmed_at = time.monotonic()
                return True
            # 实在认不出卡片：不盲点卡片，先按默认选择点「确定」，下一拍再判
            self.click_button("confirm", must=False)
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
            p = int(labels.PERCENT_RE.match(rate_box.name.strip()).group(1))
            gain = None
            if gain_cols:
                _gcx, ggroup = min(gain_cols, key=lambda c: abs(c[0] - cx))
                if abs(_gcx - cx) < 0.20:
                    gain_box = max(ggroup, key=lambda b: b.height)
                    gain = gain_box
            v = int(labels.GAIN_RE.match(gain.name.strip()).group(1)) if gain else p
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
        # 点**卡片正中**：只点成功率文字框（在卡片顶部）实测会落在两卡之间偏上的位置
        # （用户 2026-10-27 录屏：两张卡中间出现了一次多余点击）。
        # 2026-11-02：点完卡片**等选中动画走完**再点确定（旧版 0.35s 太短，
        # 用户看到「点卡片没点下去 → 又点确定也没点中」两次空点击）。
        self.click(int(card_cx * self.width), int(self.height * 0.5),
                   after_sleep=self.BOOST_CARD_SLEEP)
        self.sleep(self.BOOST_CONFIRM_GAP)
        self.click_button("confirm", after_sleep=0.6, must=False)
        self._boost_confirmed_at = time.monotonic()
        detail = "  ".join(f"{pp}%/+{vv}={e:.0f}" for e, vv, pp, _cx in picks)
        self.log_info(f"加强训练：{detail} → 选 {p}%/+{v}")
        return True


