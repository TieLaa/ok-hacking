"""一天的周期：新一天的**两个入口** + 全局日期。

用户 2026-11-01 定下的架构（见 `docs/ARCHITECTURE.md`）：

    **一天只有一个标识：顶栏的「第N周 星期X」。**
    游戏里「打工 / 消费 / 划船 / 买扭蛋」只要发生交互就过一天，所以
    日期变了就是新的一天，没变就是同一天 —— 不需要另外维护「今天过没过」。

    新一天有**两个入口**，它们是同级的：
      ① 训练日主界面（右下「训练 / 休息」）
      ② 出行前跟队长说话的界面（「出行」按钮所在的那页）

    这两个入口放在**同一个模块**（本文件），全局日期也写在这里 ——
    其他模块不自己读顶栏，只向这里要日期。

为什么不能各模块自己读顶栏（2026-11-01 的真实 bug）：

    `top_bar()` 会被 20 多个地方调用，而同一帧里 OCR 可能读到不同的值。
    `weekend_state()` 原来按「日期键变了就重建整个状态」，
    于是顶栏抖一帧 → `visited` / `gacha_spent` 全被清空 →
    当天「已经买过扭蛋」的事实丢了，又要去餐厅打工一次。

所以本模块提供**唯一的**日期读取入口，并且**每帧只读一次**。
"""

import time

from src.tasks.training.constants import (JPEG_PARAMS, PROJECT_ROOT,  # noqa: F401
                                          STYLE_BY_NAME)  # noqa: F401


class DayCycleMixin:
    """全局日期 + 一天的周期状态。

    状态（按「第N周 + 星期X」重置）都挂在这里，别的模块通过
    `day_state()` 拿，**不直接碰** `self._weekend`。
    """

    # ---------- 全局日期（每帧只读一次） ----------

    def current_day(self, texts=None):
        """当前是「第N周 星期X」，返回 (week, day)；读不到返回 (None, None)。

        每帧只读一次：同一帧里 OCR 结果固定，但不同模块各读一次会
        各自触发一次 OCR，可能读到不同的值（斜杠被吃、图标被读成假汉字…）。
        缓存键是**帧对象**，所以下一帧自动重读。

        读不到完整日期键（周或星期缺一）时返回 (None, None) ——
        调用方要当「顶栏 OCR 抖动」处理，**不能**当成「换了一天」。
        """
        frame = getattr(self, "_tick_frame", None)
        cache = getattr(self, "_day_cache", None)
        if isinstance(cache, dict) and cache.get("frame") is frame:
            return cache["value"]
        state = self.top_bar(texts)
        value = (state.get("week"), state.get("day"))
        self._day_cache = {"frame": frame, "value": value}
        return value

    def day_name(self, texts=None):
        """「星期五」这样的星期几；读不到返回 None。"""
        return self.current_day(texts)[1]

    def is_weekend(self, texts=None):
        """今天是周末（六/日）吗？读不到日期时返回 False（不猜）。"""
        return self.current_day(texts)[1] in ("六", "日")

    # ---------- 一天的状态（按日期键重置） ----------

    def day_state(self):
        """今天的共享状态（`visited` / `gacha_spent` / `outing_done` …）。

        **重置的唯一触发条件是日期变了** —— 因为游戏里交互就过天，
        所以「日期没变」=「还在同一天」= 状态不该变。

        2026-11-01 修：原来只要 key 变了就整个重建，顶栏 OCR 抖一帧
        （扭蛋页只读到体力、没读到「第1周 星期五」）就会把当天状态清空。
        现在读不到完整日期键时**沿用旧状态**。
        """
        week, day = self.current_day()
        prev = self._weekend.get("date")
        if week is None or day is None:
            # 顶栏 OCR 抖动，不是「换了一天」→ 沿用旧状态
            if prev is not None:
                self.log_debug(f"顶栏没读到周/星期（{week}/{day}）→ 沿用 {prev} 的当天状态")
            self._ensure_day_state_keys()
            return self._weekend
        key = (week, day)
        if prev != key:
            if prev is not None:
                self.log_info(f"日期键 {prev} → {key}（新的一天，今日行程重置）")
            self._weekend = self._new_day_state(key)
        return self._weekend

    @staticmethod
    def _new_day_state(key):
        """新的一天的状态。"""
        return {
            "date": key,
            "shop_checked": False,
            "bought": False,
            "park_choice": None,
            "visited": set(),
            # 今天在步行街**试过**抽扭蛋（买没买成都算试过）。
            # 地图上按它排除步行街，防止「买不到 → 又点进去」的无限循环。
            "gacha_tried": set(),
            # 今天在步行街抽到过扭蛋（扭蛋流决定要不要再去餐厅打工）
            "gacha_spent": False,
            # 训练日里出现「出行」时先外出（野上泷周五也能出行），一天只做一次
            "outing_done": False,
        }

    def _ensure_day_state_keys(self):
        """补齐 `_weekend` 里缺的键。

        2026-11-01：`_weekend` 初始值只有 4 个键（TrainingTask.__init__），
        其它键要等重建时才补上。顶栏读不到日期时不重建 → 那些键就永远缺，
        `task._weekend["visited"]` 会 KeyError。
        """
        defaults = self._new_day_state(self._weekend.get("date"))
        for k, v in defaults.items():
            if k not in self._weekend:
                self._weekend[k] = v
        return self._weekend

    # ---------- 一天的入口 ①：训练日主界面 ----------

    def training_day_action(self, texts):
        """训练日主界面：今天练还是休？返回 ("训练"/"休息", 理由) 或 (None, 理由)。

        判据都在这里（体力阈值、失败率上限、强制休息…），
        别的地方不重复判「今天该不该休息」。
        """
        state = self.top_bar(texts)
        cur = state.get("stamina")
        day = state.get("day")
        limit = self.rest_threshold(day)
        if self._force_rest:
            self._force_rest = False
            return "休息", "失败率过高，强制休息"
        if cur is None:
            return "训练", f"体力读不到 → 照常训练"
        if cur < limit:
            return "休息", f"体力 {cur} < {limit}（{self.style}·星期{day}）→ 休息"
        return "训练", f"体力 {cur}，进入训练科目"

    # ---------- 一天的入口 ②：出行前（跟队长说话那页） ----------

    def outing_offer(self, texts, block_if_used=True):
        """这一页要不要外出？返回 ("点按钮", 理由) / ("推进对话", 理由) / (None, 理由)。

        **两个入口同级**：训练日主界面和周末首页都能外出，判断放一起。

        `block_if_used`：今天的外出机会用过了还让不让点？
        - 训练日（`handle_training_day`）：用过了就不点（野上泷周五的**额外**外出，一天一次）；
        - 周末首页（`handle_go_out`）：**不**拦（周末出行是主流程，不是额外机会）。

        2026-10-31 修正：只认**按钮**，而且点到了才记「今天用过了」。
        旧版 `click_text("出行")` 会把对话框台词「今天可以出行哦！要不」当按钮点
        （官方日志 `left_click 今天可以出行哦！要不 (950, 182)`），按钮一下没点到，
        外出标记却已经置上，下一拍直接进训练（用户：「为什么没有点出行，
        反而默认进了训练」）。
        """
        if self.match_text(texts, "出行") is None:
            return None, "这一页没有「出行」"
        if block_if_used and self.outing_used():
            return None, "今天的额外出行已经用过了"
        button = self.find_go_out_button(texts)
        if button is not None:
            return "点按钮", "有「出行」按钮 → 外出（一天一次）"
        line = self.match_text(texts, "出行")
        if line is not None and len(line.name.strip()) > 4:
            # 按钮还没出来，这一拍只推进对话（不算用掉当天机会）
            return "推进对话", "对话框提到「出行」但还没按钮 → 先推进对话"
        return None, "「出行」按钮认不出来"

    def outing_used(self):
        """今天的额外出行机会用过了吗？"""
        return bool(self.day_state().get("outing_done"))

    def mark_outing_used(self):
        """标记今天的额外出行机会已用（**点到了才算**）。"""
        self.day_state()["outing_done"] = True

