"""养成（训练）自动化。

一局养成的流程（用户实测描述）：

    选队长 → 第1周：周一~周五训练 / 周六周日周末活动
           → 第2周起：周一比赛（赢了继续）→ 周二~周五训练 → 周六周日周末活动
           → …… 5~6 周结束，人物面板定型

脚本要做的事：把这套流程的每一个界面决策自动化，比赛用「跳过比赛」跳过。

设计照抄 ok-kes 的「单帧单动作 + 优先级链」：
每拍只 OCR 一次，然后按优先级依次交给 handler，第一个返回 True 的消费本拍。
"""

import random
import time

from ok import ConfigOption  # noqa: F401  (保留给全局配置扩展)

from src.tasks.BaseGirlsTask import BaseGirlsTask
from src.tasks.ui import Defs

# 流派 → 周末去哪（按优先级，每天一个）
# 注意顺序：步行街扭蛋「消耗所有精力」，公园野餐「精力++」，
# 所以先扭蛋再野餐，相当于白拿 +48 属性；反过来就把回复浪费了。
STYLE_PLACES = {
    "训练流": ["步行街", "公园"],
    "扭蛋流": ["步行街", "公园"],
    "商店流": ["餐厅", "公园"],
    # 游戏厅只在「强化」里解锁了「可跳过已三星小游戏」之后才有意义，
    # 需要配置里显式打开「允许进游戏厅」；没三星的游戏仍需要真人打。
    "小游戏流": ["游戏厅", "公园"],
}


class TrainingTask(BaseGirlsTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "养成-训练循环"
        self.description = "自动跑完一局养成：训练 / 周末活动 / 比赛日跳过，循环到赛季结束"
        self.default_config.update({
            "流派": "训练流",
            "训练科目优先级": ["投射", "防守", "弹跳", "运控", "体能", "意识"],
            "失败率上限(%)": 50,
            "失败率超限改休息": True,
            "餐厅选项": "当服务员",
            "公园选项": "野餐",
            "扭蛋优先属性": ["投射", "体能", "运控"],
            "允许进游戏厅": False,
            "比赛日处理": "自动（先跳过，跳不了就进比赛加速）",
            "体力低于多少改休息": 30,
            "单局最大操作数": 400,
            "操作间隔(秒)": 0.8,
            "快进重复间隔(秒)": 1.0,
        })
        self.config_type.update({
            "流派": {"type": "drop_down", "options": list(STYLE_PLACES.keys())},
            "餐厅选项": {"type": "drop_down", "options": ["当服务员", "桌游GM"]},
            "公园选项": {"type": "drop_down", "options": ["野餐", "划船", "散步"]},
            "比赛日处理": {
                "type": "drop_down",
                "options": ["自动（先跳过，跳不了就进比赛加速）", "总是跳过", "总是进比赛（外部战斗脚本）"],
            },
        })
        self.config_description.update({
            "训练科目优先级": "按顺序候选；失败率超上限就自动换下一个",
            "失败率上限(%)": "当前科目失败率高于这个值就换科目，全部超限才休息",
            "餐厅选项": "当服务员 = 零用钱++；桌游GM = 零用钱+",
            "公园选项": "野餐 = 精力++；划船 = 属性+；散步 = 零用钱+",
            "扭蛋优先属性": "步行街扭蛋三选一，按顺序找第一个存在的（会消耗所有精力）",
            "比赛日处理": "没打赢过的比赛不能跳过，只能进比赛后点左下角快进",
            "快进重复间隔(秒)": "快进点一次管一段时间，1 秒补点一次即可；0 = 只点一次",
            "单局最大操作数": "防止卡死无限点，超过后自动停止",
        })
        self._steps = 0
        self._skip_tries = 0
        self._last_state = None
        self._picked_subject = False
        self._picked_index = 0
        self._match_flags = {"manual_fixed": False, "fast_clicked": False, "last_fast_at": 0.0}

    # ---------- 策略 ----------

    @property
    def style(self):
        return self.config.get("流派", "训练流")

    def weekend_priority(self):
        places = list(STYLE_PLACES.get(self.style, STYLE_PLACES["训练流"]))
        if not self.config.get("允许进游戏厅", False):
            places = [p for p in places if p != "游戏厅"]
        return places or ["公园"]

    def low_stamina(self, stamina_state):
        current = stamina_state.get("stamina")
        limit = int(self.config.get("体力低于多少改休息", 30))
        return current is not None and current < limit

    # ---------- 单拍决策 ----------

    def run(self):
        self._steps = 0
        max_steps = int(self.config.get("单局最大操作数", 400))
        interval = float(self.config.get("操作间隔(秒)", 0.8))

        while self._steps < max_steps:
            if not self._enabled:
                self.log_info("任务已停止")
                return False
            self._steps += 1

            texts = self._ocr_region()
            if not texts:
                self.log_debug("本帧没有识别到文字，等待")
                self.sleep(1)
                continue

            if self.is_loading():
                self.sleep(1)
                continue

            if self._handle_any(texts):
                self.sleep(interval)
                continue

            # 一帧没认出来：先看看是不是卡在某个界面，按返回兜底
            self.log_info("未识别的界面，尝试返回")
            self.back(after_sleep=interval)
            self.sleep(interval)

        self.log_warning(f"达到最大操作数 {max_steps}，停止")
        return False

    def _handle_any(self, texts):
        for handler in self.HANDLERS:
            try:
                if handler(self, texts):
                    self._last_state = handler.__name__
                    if handler is not TrainingTask.handle_prepare:
                        self._skip_tries = 0
                    if handler is not TrainingTask.handle_in_match:
                        self._match_flags = {"manual_fixed": False, "fast_clicked": False, "last_fast_at": 0.0}
                    if handler is not TrainingTask.handle_subject_select:
                        self._picked_subject = False
                        self._picked_index = 0
                    return True
            except Exception as e:
                self.log_error(f"{handler.__name__} 异常: {type(e).__name__}: {e}")
                return True  # 吞掉这一拍，避免同一异常反复刷屏
        return False

    # ---------- handlers（顺序 = 优先级）----------

    def handle_popup(self, texts):
        """公告 / 领奖 / 月卡 / 提示：先关掉，否则什么都点不动。"""
        target = self.match_text(texts, ("今日不再提示", "关闭", "取消"))
        if target:
            self.click_box(target, after_sleep=0.8)
            self.log_info(f"关闭弹窗「{target.name}」")
            return True
        return False

    def handle_in_match(self, texts):
        """比赛内：左下角三个开关（快进 / 倍速 / 自动·手动）。

        规则（用户实测）：
        - 看到「手动」说明当前是手动，先点一下切成自动，再点「快进」；
        - 看不到「手动」说明已经是自动，直接点「快进」；
        - 比赛里**只允许动这三个开关**，其它地方一律不点（乱点会犯规/丢球）。
        """
        hud = self.texts_in_region(texts, "match_hud")
        if not hud:
            return False
        speed = self.match_text(hud, Defs.SPEED_TEXTS)
        if speed is None:
            return False

        # 1) 手动 → 自动
        manual = self.match_text(hud, "手动")
        if manual is not None and not self._match_flags["manual_fixed"]:
            self._match_flags["manual_fixed"] = True
            self.click_box(manual, after_sleep=0.5)
            self.log_info("比赛：手动切自动")
            return True

        # 2) 快进（默认只点一次，避免把开关又切回去）
        fast = self.match_text(hud, "快进")
        if fast is not None and not self._match_flags["fast_clicked"]:
            self._match_flags["fast_clicked"] = True
            self._match_flags["last_fast_at"] = time.monotonic()
            self.click_box(fast, after_sleep=1.0)
            self.log_info("比赛：点击快进")
            return True

        # 3) 可选：按固定间隔补点快进（有些模式快进会到期）
        repeat = float(self.config.get("快进重复间隔(秒)", 0))
        if fast is not None and repeat > 0:
            if time.monotonic() - self._match_flags["last_fast_at"] >= repeat:
                self._match_flags["last_fast_at"] = time.monotonic()
                self.click_box(fast, after_sleep=1.0)
                self.log_debug("比赛：补点快进")
                return True

        # 仍在比赛内：吃掉本拍，别让其它 handler 误触
        return True

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

        mode = self.config.get("比赛日处理", "")
        if mode.startswith("总是进比赛") and go is not None:
            self.click_box(go, after_sleep=2.0)
            self.log_info("进比赛（等外部战斗脚本接管）")
            return True

        if skip is not None and self._skip_tries == 0:
            self._skip_tries = 1
            self.click_box(skip, after_sleep=1.5)
            self.log_info("尝试跳过比赛")
            return True

        if go is not None:
            self._skip_tries = 0
            self.click_box(go, after_sleep=2.0)
            self.log_info("跳过不可用，进入比赛（只点加速）")
            return True

        self.log_warning("赛前准备页：跳过和前往都没找到")
        return False

    def handle_result(self, texts):
        """比赛/训练结算页：确认、继续、领取。"""
        target = self.match_text(texts, ("确认", "继续", "领取", "下一步"))
        if not target:
            return False
        # 结算页的「确认」可能连点两次
        self.click_box(target, after_sleep=1.2)
        self.log_info(f"结算页「{target.name}」")
        return True

    def choose_option(self, texts, names, after_sleep=0.9):
        """在周末子页面的选项区里选一个，并处理可能的「确定」。"""
        opts = self.texts_in_region(texts, "weekend_options")
        target = self.match_text(opts, names)
        if target is None:
            return False
        self.click_box(target, after_sleep=after_sleep)
        self.log_info(f"选择「{target.name}」")
        confirm = self.match_text(self._ocr_region("confirm_bar"), Defs.CONFIRM_TEXTS)
        if confirm is not None:
            self.click_box(confirm, after_sleep=1.2)
            self.log_info("确定")
        return True

    def handle_restaurant(self, texts):
        """餐厅内页：桌游GM（零用钱+）/ 当服务员（零用钱++）。"""
        if self.match_text(texts, Defs.RESTAURANT_TITLE) is None:
            return False
        pref = self.config.get("餐厅选项", "当服务员")
        order = (pref,) + tuple(x for x in Defs.RESTAURANT_OPTIONS if x != pref)
        return self.choose_option(texts, order)

    def handle_park(self, texts):
        """公园内页：散步（零用钱+）/ 划船（属性+）/ 野餐（精力++）。"""
        if self.match_text(texts, Defs.PARK_TITLE) is None:
            return False
        pref = self.config.get("公园选项", "野餐")
        order = (pref,) + tuple(x for x in Defs.PARK_OPTIONS if x != pref)
        return self.choose_option(texts, order)

    def handle_street(self, texts):
        """步行街扭蛋：三选一（会消耗所有精力），选完按确定。"""
        if self.match_text(texts, Defs.STREET_TITLE) is None:
            return False
        names = []
        for attr in self.config.get("扭蛋优先属性", list(Defs.GACHA_OPTIONS)):
            names.append(f"{attr}专注")
            names.append(attr)
        return self.choose_option(texts, tuple(names), after_sleep=1.0)

    def handle_arcade(self, texts):
        """游戏厅：只处理「已三星 → 可跳过」的小游戏。

        「强化」里解锁后，游戏厅可以跳过已三星的游戏，但仍会触发地狱游戏。
        没三星的小游戏需要真人打（打篮球/拼图），OCR 跟不上，遇到就立刻退出，
        避免白费一个周末日。
        """
        if self.match_text(texts, Defs.ARCADE_TITLE) is None:
            return False
        skip = self.match_text(self.texts_in_region(texts, "weekend_options"), Defs.ARCADE_SKIP)
        if skip is None:
            skip = self.match_text(texts, Defs.ARCADE_SKIP)
        if skip is not None:
            self.click_box(skip, after_sleep=1.0)
            confirm = self.match_text(self._ocr_region("confirm_bar"), Defs.CONFIRM_TEXTS)
            if confirm is not None:
                self.click_box(confirm, after_sleep=1.2)
            self.log_info("游戏厅：跳过已三星小游戏")
            return True
        self.log_warning("游戏厅：没有可跳过的小游戏（未三星），退出")
        self.back(after_sleep=1.2)
        return True

    def handle_weekend(self, texts):
        """周末地图：按流派优先级选地点。"""
        if not self.match_text(texts, Defs.WEEKEND_PLACES):
            return False
        for place in self.weekend_priority():
            target = self.match_text(texts, place)
            if target:
                self.click_box(target, after_sleep=1.5)
                self.log_info(f"周末去「{place}」（{self.style}）")
                return True
        return False

    def read_fail_rate(self, texts):
        """读取当前选中科目的失败率（0~100），读不到返回 None。"""
        joined = "".join(t.name for t in self.texts_in_region(texts, "fail_rate")).replace(" ", "")
        if m := Defs.FAIL_RATE_RE.search(joined):
            return int(m.group(1))
        return None

    def handle_subject_select(self, texts):
        """训练科目选择页：右侧 6 个科目 → 选一个 → 右上「决定」。

        失败率是这里唯一的决策信号：
        - 失败率高于上限时，优先换下一个科目（换科目会改变失败率）；
        - 全部超限才考虑休息；
        - 都不行就直接决定，交给游戏结算（至少不卡死）。
        """
        subjects = self.texts_in_region(texts, "subject_list")
        if self.match_text(subjects, Defs.TRAINING_SUBJECTS) is None:
            return False

        priority = list(self.config.get("训练科目优先级", Defs.TRAINING_SUBJECTS))
        limit = int(self.config.get("失败率上限(%)", 50))
        fail = self.read_fail_rate(texts)
        if fail is not None:
            self.info_set("当前失败率", f"{fail}%")

        # 还没选科目：先按优先级点第一个
        if not self._picked_subject:
            for name in priority:
                target = self.match_text(subjects, name)
                if target is not None:
                    self._picked_subject = True
                    self._picked_index = priority.index(name)
                    self.click_box(target, after_sleep=0.6)
                    self.log_info(f"选择训练科目「{name}」")
                    return True
            return False

        # 已选科目：看失败率是否可接受
        if fail is not None and fail > limit and self._picked_index + 1 < len(priority):
            nxt = priority[self._picked_index + 1]
            target = self.match_text(subjects, nxt)
            if target is not None:
                self._picked_index += 1
                self.click_box(target, after_sleep=0.6)
                self.log_info(f"失败率 {fail}% > {limit}%，换科目「{nxt}」")
                return True

        if fail is not None and fail > limit:
            rest = self.match_text(texts, Defs.REST_TEXTS)
            if rest is not None and self.config.get("失败率超限改休息", True):
                self._picked_subject = False
                self.click_box(rest, after_sleep=1.0)
                self.log_info(f"失败率 {fail}% 过高且无可换科目，改为休息")
                return True
            self.log_warning(f"失败率 {fail}% 超过上限 {limit}%，但仍要继续训练")

        confirm = self.match_text(self.texts_in_region(texts, "confirm"), Defs.CONFIRM_TEXTS)
        if confirm is None:
            confirm = self.match_text(self._ocr_region("confirm"), Defs.CONFIRM_TEXTS)
        if confirm is None:
            self.log_warning("找不到「决定」按钮，放弃本次选择")
            self._picked_subject = False
            return False

        self._picked_subject = False
        self._picked_index = 0
        self.click_box(confirm, after_sleep=1.5)
        self.log_info(f"决定（失败率 {fail}%）")
        return True

    def handle_training_hub(self, texts):
        """训练日主界面：六维属性 + 右下角主按钮。

        只有右下角真的写着「比赛 / VS / 训练」时才点，避免在科目选择页误触。
        """
        action = self.texts_in_region(texts, "main_action")
        if self.match_text(action, ("VS", "比赛", "训练")) is None:
            return False
        self.click_main_action(after_sleep=1.5)
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
            self.click_box(confirm, after_sleep=1.5)
            self.log_info("度假：决定")
            return True
        if self.click_text(("开始训练", "出发", "训练"), after_sleep=1.5, must=False):
            self.log_info("度假：开始训练")
            return True
        self.click_main_action(after_sleep=1.5)
        self.log_info("度假：点主按钮")
        return True

    HANDLERS = (
        handle_popup,
        handle_in_match,
        handle_prepare,
        handle_result,
        handle_restaurant,
        handle_park,
        handle_street,
        handle_arcade,
        handle_weekend,
        handle_subject_select,
        handle_vacation,
        handle_training_hub,
    )
