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
            "允许开始训练": True,
            "休息体力阈值": 40,
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
        """在周末子页面的选项区里选一个，并处理可能的「确定」。

        确定按钮位置不固定（扭蛋页在底部中间，餐厅/公园可能在别处），
        先看底部条，再看整屏，避免只在固定区域找不到就反复点同一张卡。
        """
        opts = self.texts_in_region(texts, "weekend_options")
        target = self.match_text(opts, names)
        if target is None:
            return False
        self.click_box(target, after_sleep=after_sleep)
        self.log_info(f"选择「{target.name}」")

        confirm = self.find_text(("确定", "确认"))
        if confirm is not None:
            self.click_box(confirm, after_sleep=1.2)
            self.log_info(f"确定 @({confirm.x},{confirm.y})")
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
        """训练科目页：**同一个科目点两下 = 确定训练**（实机验证）。

        选科目的依据（按重要性）：
        1. **带队友的科目优先** —— 队友数直接抬「同表情」概率，是成型倍率的主要来源；
           且多人同训有协同属性（实测野餐 70 + 20 + 20）。
        2. 其次按配置的属性优先级。
        3. 属性接近收敛线（1150）的科目降权。
        """
        subjects = self.texts_in_region(texts, "subject_list")
        if self.match_text(subjects, Defs.TRAINING_SUBJECTS) is None:
            return False

        fail = self.read_fail_rate(texts)
        if fail is not None:
            self.info_set("当前失败率", f"{fail}%")
        limit = int(self.config.get("失败率上限(%)", 50))
        if fail is not None and fail > limit:
            self.log_warning(f"失败率 {fail}% 超过上限 {limit}%（仍继续，休息需要回主界面）")

        priority = list(self.config.get("训练科目优先级", list(Defs.TRAINING_SUBJECTS)))
        scores = {}
        for name in priority:
            if self.match_text(subjects, name) is None:
                continue
            team = self.teammate_score(name)
            scores[name] = team

        if not scores:
            self.log_warning("认不出任何训练科目")
            return False

        # 队友分档优先，其次保持配置顺序
        best = max(scores.values())
        name = next(n for n in priority if n in scores and scores[n] == best)
        if self.config.get("打印队友检测", True):
            self.log_info("队友检测：" + " ".join(f"{k}={v:.2f}" for k, v in scores.items()))
        self.log_info(f"选科目「{name}」（队友分 {best:.2f}）")

        x, y = Defs.SUBJECT_COORDS.get(name, (1440, 345))
        self.click(x, y, after_sleep=0.9)    # 第一下：选中并预览
        self.click(x, y, after_sleep=2.5)    # 第二下：确认训练
        self.log_info(f"训练「{name}」（失败率 {fail}%）")
        return True

    def teammate_score(self, name):
        """粗算某个科目行右端的「黄色头像」占比，用来判断有没有队友。

        头像是一个小黄圆脸，压在深蓝底上，BGR 大致 (60, 200, 250)。
        只做相对比较（同一帧里谁高谁低），不做绝对判定，容忍阈值漂移。
        """
        frame = self.frame
        coord = Defs.SUBJECT_COORDS.get(name)
        if frame is None or coord is None:
            return 0.0
        h, w = frame.shape[:2]
        cx, cy = coord
        x1, x2 = int(w * 0.78), int(w * 0.86)
        y1, y2 = max(0, cy - 28), min(h, cy + 28)
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return 0.0
        b, g, r = roi[:, :, 0].astype(int), roi[:, :, 1].astype(int), roi[:, :, 2].astype(int)
        yellow = (r > 200) & (g > 150) & (b < 120)
        return float(yellow.mean())

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

    # ---------- 进入养成流程（大厅 → 训练 → 选队长 → 组队 → 器材 → 开始）----------

    def handle_dialog_continue(self, texts):
        """剧情/提示：有「跳过」就点，否则靠点击推进。"""
        skip = self.match_text(texts, ("跳过", "点击屏幕继续"))
        if skip is not None:
            self.click_box(skip, after_sleep=0.8)
            return True
        return False

    def handle_story_arrow(self, texts):
        """剧情对话：右下角有白色 ▼ 就点它推进。

        判定要同时满足两条，否则周末地图这种浅色背景会误判：
        1) 小区域内近白像素占比够高（▼ 本身）；
        2) 周围一圈偏暗（对话条是深色底）。
        """
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = Defs.region("story_arrow")
        roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        if roi.size == 0:
            return False
        white_ratio = float((roi.min(axis=2) > 200).mean())
        if white_ratio < 0.08:
            return False

        # 周围环境必须偏暗，排除浅色地图/白底界面
        bx1, by1, bx2, by2 = Defs.region("story_area")
        around = frame[int(h * by1):int(h * by2), int(w * bx1):int(w * bx2)]
        if around.size == 0:
            return False
        if float(around.mean()) > 135:
            return False

        cx = int(w * (x1 + x2) / 2)
        cy = int(h * (y1 + y2) / 2)
        self.click(cx, cy, after_sleep=1.0)
        self.log_info(f"剧情对话：点 ▼ 推进（白 {white_ratio:.2f} 背景 {around.mean():.0f}）")
        return True

    def handle_lobby(self, texts):
        if self.match_text(texts, ("赛事", "社团GO")) is None:
            return False
        x, y = Defs.FLOW_COORDS["lobby_training"]
        ok = self.click_and_expect(x, y, ("训练剧本",), timeout=8)
        self.log_info("进入训练剧本" if ok else "点「训练」没反应")
        return True

    def handle_training_entry(self, texts):
        if self.match_text(texts, "训练剧本") is None:
            return False
        x, y = Defs.FLOW_COORDS["training_start"]
        ok = self.click_and_expect(x, y, ("训练核心",), timeout=10)
        self.log_info("进入训练核心" if ok else "点「启程」没反应")
        return True

    def handle_captain(self, texts):
        if self.match_text(texts, "训练核心") is None:
            return False
        x, y = Defs.FLOW_COORDS["captain_card"]
        self.click(x, y, after_sleep=0.8)
        self.log_info("选第一位队长（列表按等级降序，第一张最强）")
        nx, ny = Defs.FLOW_COORDS["next_step"]
        ok = self.click_and_expect(nx, ny, ("选择队友",), timeout=8)
        self.log_info("进入选择队友" if ok else "点「下一步」没反应")
        return True

    def handle_team(self, texts):
        if self.match_text(texts, "选择队友") is None:
            return False
        ax, ay = Defs.FLOW_COORDS["auto_select"]
        self.click(ax, ay, after_sleep=1.0)
        self.log_info("队友：自动选择")
        nx, ny = Defs.FLOW_COORDS["next_step"]
        ok = self.click_and_expect(nx, ny, ("选择器材",), timeout=8)
        self.log_info("进入选择器材" if ok else "点「下一步」没反应")
        return True

    def handle_equipment(self, texts):
        if self.match_text(texts, "选择器材") is None:
            return False
        ax, ay = Defs.FLOW_COORDS["auto_select"]
        self.click(ax, ay, after_sleep=1.0)
        self.log_info("器材：自动选择")
        nx, ny = Defs.FLOW_COORDS["next_step"]
        ok = self.click_and_expect(nx, ny, ("确认编队",), timeout=8)
        self.log_info("进入确认编队" if ok else "点「下一步」没反应")
        return True

    def handle_confirm_team(self, texts):
        """确认编队：开始训练会扣体力且中途取消不返还，默认要用户先允许。"""
        if self.match_text(texts, "确认编队") is None:
            return False
        if not self.config.get("允许开始训练", True):
            self.log_info("已禁用自动开始训练，停在这里等你确认")
            return True
        x, y = Defs.FLOW_COORDS["confirm_start"]
        self.click(x, y, after_sleep=3.0)
        self.log_info("开始训练（已扣体力，不可退）")
        return True

    def handle_training_day(self, texts):
        """训练日主界面：右下「训练 / 休息」。"""
        if self.match_text(texts, "星期") is None:
            return False
        if self.match_text(texts, "休息") is None:
            return False
        state = self.top_bar()
        cur = state.get("stamina")
        limit = int(self.config.get("休息体力阈值", 40))
        if cur is not None and cur < limit:
            x, y = Defs.FLOW_COORDS["day_rest"]
            self.click(x, y, after_sleep=2.0)
            self.log_info(f"体力 {cur} 偏低，休息")
        else:
            x, y = Defs.FLOW_COORDS["day_train"]
            self.click(x, y, after_sleep=2.0)
            self.log_info(f"体力 {cur}，进入训练科目")
        return True

    HANDLERS = (
        handle_popup,
        handle_in_match,
        handle_prepare,
        handle_result,
        handle_dialog_continue,
        handle_story_arrow,
        handle_lobby,
        handle_training_entry,
        handle_captain,
        handle_team,
        handle_equipment,
        handle_confirm_team,
        handle_training_day,
        handle_restaurant,
        handle_park,
        handle_street,
        handle_arcade,
        handle_weekend,
        handle_subject_select,
        handle_vacation,
        handle_training_hub,
    )
