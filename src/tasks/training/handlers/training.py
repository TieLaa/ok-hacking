"""训练日、科目选择、加强训练。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import re
import time

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

    def handle_training_day(self, texts):
        """训练日主界面：右下「训练 / 休息」。"""
        if self.match_text(texts, "星期") is None:
            return False
        if self.match_text(texts, "休息") is None:
            return False
        if self.try_speed_2x(texts):
            return True
        state = self.top_bar()
        cur = state.get("stamina")
        limit = int(self.config.get("休息体力阈值", 40))
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
                self.log_info(f"体力 {cur} 偏低，休息")
        else:
            target = self.match_text(action, "训练")
            if target is not None:
                self.click_box(target, after_sleep=0.6)
            else:
                self.flow_click("day_train", after_sleep=0.6)
            self.log_info(f"体力 {cur}，进入训练科目")
        return True

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

        recommended = set(self.config.get("推荐属性", []))
        boxes = {}
        for name in Defs.TRAINING_SUBJECTS:
            box = self.match_text(subjects, name)
            if box is not None:
                boxes[name] = box
        # 按屏幕从上到下探测（旧版按配置表扫，看起来像随机乱点 —— 用户 2026-10-26）
        boxes = dict(sorted(boxes.items(), key=lambda kv: kv[1].y))

        if not boxes:
            self.log_warning("认不出任何训练科目")
            return False

        # 橙色小人只表示「有队友」；数量/装备必须点开看。
        icons = self.find_teammate_icons()
        with_team = [n for n in boxes if self.has_teammate_icon(boxes[n], icons)]
        self.log_info("有队友的科目：" + ("、".join(with_team) if with_team else "无（按推荐属性选）"))

        # 当前已经选中的科目（右边有「决定」的那一行）不要点：点它有直接开练的风险。
        # 它的预览信息直接读当前帧（彩虹边框在开页第一帧就有，器材卡随后出现）。
        current = self.current_subject(texts)
        results = {}
        if current in boxes:
            results[current] = self.analyze_preview(texts)
            r = results[current]
            self.log_info(
                f"当前已选「{current}」：队友 {r['mates']}｜经理 {r['manager']}"
                f"｜装备 {r['equip']}｜满经验 {r['full']}｜金框 {r['gold']}"
                f"｜彩虹 {int(r['crit'])}｜得分 {self.subject_score(r):.2f}")

        last_probed = None
        for name in with_team:
            if name == current:
                continue
            box = self.find_subject_box(name)
            if box is None:
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
            self.log_debug(f"六维当前值：{attr_values}")

        def rank(name):
            """分数 → 队长推荐属性 → 当前数值低的 → 屏幕上靠前的（兜底）。"""
            return self.subject_rank(self.subject_score(results.get(name)),
                                     name in recommended,
                                     attr_values.get(name),
                                     boxes[name].y)

        # 只在「有队友」的科目里选；一个都认不出来才退回全部科目
        pool = with_team or list(boxes)
        name = max(pool, key=rank)
        r = results.get(name) or self.empty_preview()
        self.log_info(
            f"选科目「{name}」（队友 {r['mates']}｜经理 {r['manager']}｜装备 {r['equip']}"
            f"｜满经验 {r['full']}｜金框 {r['gold']}｜得分 {self.subject_score(r):.2f}"
            f"｜当前值 {attr_values.get(name, '?')}"
            f"｜{'推荐' if name in recommended else '非推荐'}）")

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
        """重新 OCR 当前帧，按名字找科目框（选中后按钮会整体上移，不能复用旧坐标）。"""
        subjects = self.texts_in_region(self._ocr_region(), "subject_list")
        return self.match_text(subjects, name)

    def handle_daily_shop(self, texts):
        """训练日主界面：按零用钱余额决定要不要进商店。

        用户口径（2026-10-26，覆盖之前的写法）：
        - 不分流派，**零用钱 ≥ 300** 就进店，只买金/紫；
        - **比赛前一天**（倒计时 1 天）进店，把零用钱花光；
        - 钱不到 300 且不是赛前一天，就不进店，攒着。
        判据：有「星期 + 休息 + 商店」、没有「出行」（有出行是周末首页）。
        """
        if not self.config.get("每天逛商店", True):
            return False
        if self.match_text(texts, "星期") is None or self.match_text(texts, "休息") is None:
            return False
        # 2026-10-27：改期角色入队后，训练日也会出现「出行」入口。
        # 旧写法「有出行就不是训练日」会把这类训练日一整天都挡在商店外。
        # 真正的区别是「休息」：训练日一定有休息/训练按钮，周末首页没有。
        if (self.match_text(texts, "出行") is not None
                and self.match_text(texts, "休息") is None):
            return False
        shop = self.match_text(texts, "商店")
        if shop is None:
            return False
        # 点进去没成功时不要每拍狂点（正常流程一次进店会在店里买完才关）
        if time.monotonic() - self._shop_at < 15:
            return False

        state = self.top_bar()
        key = (state.get("week"), state.get("day"))
        if key[1] is None:
            # 顶栏星期 OCR 偶尔只认出「星期」两个字；用左上角「5天 赢得比赛」的
            # 倒计时兜底 —— 它每天都变，正好可以当日更键。
            banner = "".join(t.name for t in self.texts_in_region(texts, "today_banner"))
            m = re.search(r"(\d+)\s*天", banner)
            if m:
                key = (key[0], f"倒计时{m.group(1)}")
        if key[1] is not None:
            if key == self._shop_day:
                return False
        else:
            # 星期和倒计时都读不到：退化成「至少隔 60 秒再进一次」，
            # 避免买完关店又被同一帧点进去死循环，又不至于整天都不进店。
            if time.monotonic() - self._shop_at < 60:
                return False

        money = self.read_money(texts)
        threshold = int(self.config.get("商店进店余额", 300))
        spend_all = False
        if self.config.get("赛前清空余额", True) and self.is_pre_match_day(texts):
            final_only = self.config.get("只在决战前清空", False)
            is_final = self.match_text(texts, ("决赛", "总决赛", "最后一场")) is not None
            if not final_only or is_final:
                spend_all = True
                self.log_info("今天比赛前一天 → 进店把零用钱花光")
        if not spend_all and (money is None or money < threshold):
            self.log_info(f"零用钱 {money} < {threshold}，今天不进店")
            return False

        self._shop_at = time.monotonic()
        self._shop_key = key
        self._shop_spend_all = spend_all
        self.click_box(shop, after_sleep=0.9)
        self.log_info(f"每日商店：第{key[0]}周 星期{key[1]}｜零用钱 {money}"
                      f"｜{'赛前清空' if spend_all else f'只买金/紫（≥{threshold}）'}")
        return True

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
            # 加载/动画帧读不到数字：不盲点卡片，先按默认选择点「确定」，下一拍再判
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

        if len(rate_cols) != 2 or any(len(bs) > 1 for _c, bs in rate_cols):
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


