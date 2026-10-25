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

import cv2
import numpy as np

from ok import ConfigOption  # noqa: F401  (保留给全局配置扩展)

from src.tasks.BaseGirlsTask import BaseGirlsTask
from src.tasks.ui import Defs

# 流派 → 周末去哪（按优先级，每天一个）
# 注意顺序：步行街扭蛋「消耗所有精力」，公园野餐「精力++」，
# 所以先扭蛋再野餐，相当于白拿 +48 属性；反过来就把回复浪费了。
STYLE_PLACES = {
    # 训练流：周末优先回精力。步行街扭蛋会**清空精力**，第二天就没法训练，所以挪到最后。
    "训练流": ["公园", "步行街"],
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
            "训练科目优先级": ["弹跳", "运控", "投射", "防守", "体能", "意识"],
            "推荐属性": ["弹跳", "运控"],
            "失败率上限(%)": 50,
            "失败率超限改休息": True,
            "餐厅选项": "当服务员",
            "公园选项": "野餐",
            "扭蛋优先属性": ["投射", "体能", "运控"],
            "允许进游戏厅": False,
            "允许进步行街": False,
            "允许开始训练": True,
            "休息体力阈值": 40,
            "技能标签优先": ["得分手"],
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
            "训练科目优先级": "同分时的先后顺序；选科目实际按 队友数 > 推荐属性 > 这个顺序",
            "推荐属性": "队长的两项推荐属性（面板上的绿标），会额外加分",
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
        if not self.config.get("允许进步行街", False):
            places = [p for p in places if p != "步行街"]
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

            if self.is_loading(texts):
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
                    self.log_debug(f"命中 {handler.__name__}（第 {self._steps} 步）")
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

        # 2) 快进是**纯图标没有文字**，只能按实测位置点；连点才会一段段推进
        x = int(self.width * Defs.MATCH_FAST_FORWARD[0])
        y = int(self.height * Defs.MATCH_FAST_FORWARD[1])
        self.click(x, y, after_sleep=0.30)
        self.log_debug("比赛：连点快进")
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
        """步行街扭蛋：三选一（会消耗所有精力），选完按确定。

        训练流默认**不进步行街**（会把精力清空，第二天没法训练），此时直接退出。
        注意：左上角那个「返回」会打开训练计划日历，要用**右上角**的返回箭头。
        """
        if self.match_text(texts, Defs.STREET_TITLE) is None:
            return False
        if not self.config.get("允许进步行街", False):
            self.log_info("步行街扭蛋：未开启，退出")
            self.exit_page()
            return True
        names = []
        for attr in self.config.get("扭蛋优先属性", list(Defs.GACHA_OPTIONS)):
            names.append(f"{attr}专注")
            names.append(attr)
        return self.choose_option(texts, tuple(names), after_sleep=1.0)

    def exit_page(self, after_sleep=1.2):
        """点右上角返回箭头退出当前页面。

        坑：左上角 (70,55) 那个按钮在训练日会打开「训练计划」日历，不是返回。
        """
        self.click(int(self.width * 0.958), int(self.height * 0.060), after_sleep=after_sleep)

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

    def handle_go_out(self, texts):
        """周末出门页：右下角黄色「出行」按钮 → 进入地点选择地图。

        实机（2026-10-25 周六）：这个页面只有「出行」两个字，没有任何地点名，
        所以 handle_weekend 认不出来，必须先点这一下。
        """
        if self.match_text(texts, Defs.WEEKEND_PLACES) is not None:
            return False          # 已经在地图上了，交给 handle_weekend
        if self.match_text(texts, "出行") is None:
            return False
        self.click_text("出行", after_sleep=2.5)
        self.log_info("周末：点「出行」进地图")
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
        # 扭蛋/加成页的说明文字里也会出现「体能」「投射」等词（例：「所有小游戏额外获得20点体能」），
        # 不排除掉就会被当成科目页，然后疯狂点卡片、点确定（实训 2026-10-25 死循环）。
        if self.match_text(texts, ("扭蛋", "本局训练中", "小游戏强化", "器材熟练度", "熟练度。")) is not None:
            return False

        fail = self.read_fail_rate(texts)
        if fail is not None:
            self.info_set("当前失败率", f"{fail}%")
        limit = int(self.config.get("失败率上限(%)", 50))
        if fail is not None and fail > limit:
            self.log_warning(f"失败率 {fail}% 超过上限 {limit}%（仍继续，休息需要回主界面）")

        priority = list(self.config.get("训练科目优先级", list(Defs.TRAINING_SUBJECTS)))
        recommended = set(self.config.get("推荐属性", []))
        boxes = {}
        for name in priority:
            box = self.match_text(subjects, name)
            if box is not None:
                boxes[name] = box

        if not boxes:
            self.log_warning("认不出任何训练科目")
            return False

        # 橙色小人只表示「有队友」，不表示几个；数量必须点开看。
        # 所以先找出有队友的科目，只点开这几个（通常 3 个，不是 6 个）。
        icons = self.find_teammate_icons()
        with_team = [n for n in priority if n in boxes and self.has_teammate_icon(boxes[n], icons)]
        self.log_info("有队友的科目：" + ("、".join(with_team) if with_team else "无（按推荐属性选）"))

        counts = {}
        best = None
        for name in with_team:
            counts[name] = self.probe_teammates(boxes[name])
            if counts[name] >= 4:      # 默认满员 4 个（大师赛 5 个），不用再比
                best = name
                break
        if counts:
            self.log_info("点开探测：" + " ".join(f"{k}={v}" for k, v in counts.items()))

        # 打分：队友数 > 推荐属性 > 配置顺序（没探测到的科目队友数算 0）
        def rank(name):
            return (counts.get(name, 0),
                    1 if name in recommended else 0,
                    -priority.index(name))

        name = best or max(boxes, key=rank)
        self.log_info(
            f"选科目「{name}」（队友 {counts.get(name, 0)}｜"
            f"{'推荐' if name in recommended else '非推荐'}）"
        )

        # 直接点 OCR 框，不用硬编码像素：窗口缩放/分辨率变化都不怕
        target = boxes[name]
        self.click_box(target, after_sleep=0.9)    # 第一下：选中并预览
        self.click_box(target, after_sleep=2.5)    # 第二下：确认训练
        self.log_info(f"训练「{name}」（失败率 {fail}%）")
        return True

    def find_teammate_icons(self):
        """在右侧科目区找「有队友」的橙色小人头像，返回中心点列表。

        实测（2026-10-25）：小人是 42x42 的纯橙圆，主色 (255,145,0)；
        右上角「决定」按钮是亮黄 (255,237,75)，**G 通道高 90**，所以卡 g<190 就能排除。
        再用连通域尺寸过滤（42px 上下），后期按钮图标换色也不影响。
        """
        frame = self.frame
        if frame is None:
            return []
        h, w = frame.shape[:2]
        x0 = int(w * 0.78)                       # 只在右侧科目区找
        roi = frame[:, x0:]
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        mask = ((r > 225) & (g > 100) & (g < 190) & (b < 70)).astype(np.uint8)
        if not mask.any():
            return []
        n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
        size = 42 * w / 1920.0                   # 按分辨率缩放
        icons = []
        for i in range(1, n):
            _x, _y, ww, hh, area = stats[i]
            if not (size * 0.6 <= ww <= size * 1.6 and size * 0.6 <= hh <= size * 1.6):
                continue
            if area < size * size * 0.3:
                continue
            icons.append((cent[i][0] + x0, cent[i][1]))
        return icons

    def has_teammate_icon(self, box, icons):
        """这个科目的右上角有没有小人头像（横向在文字右边，纵向同一行）。"""
        if not icons or box is None:
            return False
        cx = box.x + box.width / 2
        cy = box.y + box.height / 2
        tol = 80 * self.height / 1080.0
        return any(ix > cx and abs(iy - cy) < tol for ix, iy in icons)

    def probe_teammates(self, box):
        """点开科目看场上站了几个人，返回队友数。

        人数判定：数**角色头顶的名字**（实测「凌奈」「闪堂」这类 2~4 字中文浮在人物上方），
        减去队长（队长总是在场），剩下就是队友。
        注意：装备也算队友但不会显示，所以这个数是**下界**，用于横向比较足够。
        """
        self.click_box(box, after_sleep=1.0)
        names = self.scene_names(self._ocr_region())
        self.log_debug(f"场上角色名：{names}")
        return max(0, len(names) - 1)

    def scene_names(self, texts):
        """过滤出训练场景里飘在角色头顶的名字。"""
        x1, y1, x2, y2 = Defs.region("scene_names")
        found = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            s = t.name.strip()
            if 2 <= len(s) <= 4 and all("\u4e00" <= ch <= "\u9fff" for ch in s):
                found.append(s)
        return found

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
        """剧情/提示：右上角有「跳过」就点，否则交给 handle_story_arrow 连点推进。"""
        skip = self.match_text(self.texts_in_region(texts, "top_right"), ("跳过", "点击屏幕继续"))
        if skip is not None:
            self.click_box(skip, after_sleep=0.8)
            return True
        return False

    def handle_story_arrow(self, texts):
        """剧情对话：连点画面推进（有「跳过」的话 handle_dialog_continue 先处理）。

        判据几经修正（2026-10-25 实机）：
        1. 不能只靠「白色 ▼」——**很多剧情页根本没有 ▼**，点屏幕任意位置就推进；
        2. 也不能靠「背景必须暗」——浅色场景的剧情页大量存在；
        3. 也不能靠「白色像素够多」——扭蛋页的黄色按钮/白底卡片会误判（实训死循环）；
        4. 唯一可靠的特征是**底部台词条里的一行长中文**：y 0.72~0.96、8 字以上。

        训练日/科目页/赛程页底部也有文字（属性、按钮），所以先排除这些关键词。
        """
        if self.match_text(texts, ("失败率", "决定", "休息", "赢得比赛", "VS")) is not None:
            return False

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

        white_hit = False
        if not lines:
            return False

        cx = int(self.width * 0.5)
        cy = int(self.height * 0.70)
        for _ in range(3):
            self.click(cx, cy, after_sleep=0.45)
        self.log_info(f"剧情对话：连点 3 下推进（{lines[0][:16] if lines else '白字兜底'}）")
        return True
        self.log_info(f"剧情对话：连点 3 下推进（白 {white_ratio:.3f} 背景 {around.mean():.0f}）")
        return True

    def handle_lobby(self, texts):
        if self.match_text(texts, ("赛事", "社团GO")) is None:
            return False
        x, y = self.flow_point("lobby_training")
        ok = self.click_and_expect(x, y, ("训练剧本",), timeout=8)
        self.log_info("进入训练剧本" if ok else "点「训练」没反应")
        return True

    def handle_training_entry(self, texts):
        if self.match_text(texts, "训练剧本") is None:
            return False
        x, y = self.flow_point("training_start")
        ok = self.click_and_expect(x, y, ("训练核心",), timeout=10)
        self.log_info("进入训练核心" if ok else "点「启程」没反应")
        return True

    def handle_captain(self, texts):
        if self.match_text(texts, "训练核心") is None:
            return False
        self.flow_click("captain_card", after_sleep=0.8)
        self.log_info("选第一位队长（列表按等级降序，第一张最强）")
        x, y = self.flow_point("next_step")
        ok = self.click_and_expect(x, y, ("选择队友",), timeout=8)
        self.log_info("进入选择队友" if ok else "点「下一步」没反应")
        return True

    def handle_team(self, texts):
        if self.match_text(texts, "选择队友") is None:
            return False
        self.flow_click("auto_select", after_sleep=1.0)
        self.log_info("队友：自动选择")
        x, y = self.flow_point("next_step")
        ok = self.click_and_expect(x, y, ("选择器材",), timeout=8)
        self.log_info("进入选择器材" if ok else "点「下一步」没反应")
        return True

    def handle_equipment(self, texts):
        if self.match_text(texts, "选择器材") is None:
            return False
        self.flow_click("auto_select", after_sleep=1.0)
        self.log_info("器材：自动选择")
        x, y = self.flow_point("next_step")
        ok = self.click_and_expect(x, y, ("确认编队",), timeout=8)
        self.log_info("进入确认编队" if ok else "点「下一步」没反应")
        return True

    def handle_confirm_team(self, texts):
        """确认编队：开始训练会扣体力且中途取消不返还，默认要用户先允许。"""
        if self.match_text(texts, "确认编队") is None:
            return False
        if not self.config.get("允许开始训练", True):
            self.log_info("已禁用自动开始训练，停在这里等你确认")
            return True
        self.flow_click("confirm_start", after_sleep=3.0)
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
            self.flow_click("day_rest", after_sleep=2.0)
            self.log_info(f"体力 {cur} 偏低，休息")
        else:
            self.flow_click("day_train", after_sleep=2.0)
            self.log_info(f"体力 {cur}，进入训练科目")
        return True

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
            self.click_box(manual, after_sleep=0.8)
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
        """
        if self.match_text(texts, ("失败率", "决定", "休息")) is not None:
            return False
        if self.match_text(texts, (Defs.RESULT_TITLE_RE,)) is None:
            return False
        nxt = self.match_text(texts, "下一步")
        if nxt is not None:
            self.click_box(nxt, after_sleep=1.5)
            self.log_info("结算：下一步")
            return True
        if self.match_text(texts, "点击屏幕继续") is not None:
            self.click(int(self.width_of_screen(0.5)), int(self.height_of_screen(0.9)), after_sleep=1.5)
            self.log_info("结算：点击继续")
            return True
        self.click(int(self.width_of_screen(0.5)), int(self.height_of_screen(0.5)), after_sleep=1.2)
        return True

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
        self.click(int(self.width_of_screen(cx)), int(self.height_of_screen(cy)), after_sleep=0.9)
        confirm = self.find_text(("确定", "确认"))
        if confirm is not None:
            self.click_box(confirm, after_sleep=1.5)
            self.log_info("技能：确定")
        return True

    def handle_event_choice(self, texts):
        """突发事件二/三选一：右上「跳过」是**灰的、点不动**，必须点其中一个选项。

        判据（实机 2026-10-25）：
        1. 选项按钮在屏幕**底部**（y 0.72~0.96）；
        2. 选项文字**短**（2~8 字、纯中文）——标题/正文/效果说明都更长或含数字；
        3. 必须**先排除**地点页（公园/餐厅/步行街）：那些页面底部是六维属性面板，
           「投射/体能/防守」同样是 2 字纯中文，会被误当成选项，点了没反应就死循环。

        选哪个：随便，取最左/最上那条。
        """
        if self.match_text(texts, ("在公园", "在餐厅", "野餐", "散步", "划船",
                                   "当服务员", "桌游", "扭蛋")) is not None:
            return False
        # 主界面（训练日/比赛日）右下角有「VS 比赛」「训练」大按钮，底部是六维属性面板，
        # 属性名同样是 2 字纯中文，必须排除掉，否则会被当成事件选项乱点。
        if self.match_text(self.texts_in_region(texts, "main_action"),
                          ("VS", "比赛", "训练")) is not None:
            return False

        picks = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            s = t.name.strip()
            if not (0.05 <= cx <= 0.95 and 0.72 <= cy <= 0.96):
                continue
            if not (2 <= len(s) <= 8) or not all("\u4e00" <= ch <= "\u9fff" for ch in s):
                continue
            picks.append(t)
        if len(picks) < 2:
            return False
        target = sorted(picks, key=lambda t: (round(t.y / 30), t.x))[0]
        self.log_info("事件选项：" + "、".join(t.name for t in picks) + f" → 选「{target.name}」")
        self.click_box(target, after_sleep=1.2)
        return True

    def handle_boost_training(self, texts):
        """加强训练二选一（蓝 100%/+12 vs 紫 80%/+36）。

        按**期望值**选：收益 × 成功率。实测 36×0.8=28.8 远高于 12×1.0=12。
        点卡片上半部分（百分比文字下方）再点「确定」。
        """
        if self.match_text(texts, "加强训练") is None:
            return False

        rates = [t for t in texts if Defs.PERCENT_RE.match(t.name.strip())]
        gains = [t for t in texts if Defs.GAIN_RE.match(t.name.strip())]
        if not rates:
            self.log_warning("加强训练：认不出成功率，随便点左边")
            self.click(int(self.width * 0.37), int(self.height * 0.48), after_sleep=0.8)
            self.click_text("确定", after_sleep=2.0)
            return True

        scored = []
        for r in rates:
            rx = r.x + r.width / 2
            gain = min(gains, key=lambda g: abs((g.x + g.width / 2) - rx)) if gains else None
            p = int(Defs.PERCENT_RE.match(r.name.strip()).group(1))
            v = int(Defs.GAIN_RE.match(gain.name.strip()).group(1)) if gain else p
            scored.append((v * p / 100.0, r, v, p))
        scored.sort(key=lambda x: -x[0])

        _, card, v, p = scored[0]
        self.log_info("加强训练：" + "  ".join(f"{pp}%/+{vv}={ee:.0f}" for ee, _, vv, pp in scored)
                      + f" → 选 {p}%/+{v}")
        self.click(int(card.x + card.width / 2), int(self.height * 0.48), after_sleep=0.8)
        self.click_text("确定", after_sleep=2.0)
        return True

    def _card_texts(self, texts, idx):
        x1 = 0.05 + idx * 0.31
        x2 = x1 + 0.28
        return [t for t in texts if x1 <= (t.x + t.width / 2) / self.width <= x2]

    def _card_quality(self, frame, idx):
        """判断技能卡品质，返回 3=彩色 / 2=金色 / 1=白色。

        评分公式里技能价值差一倍（75 / 50 / 25），所以必须先分清金色和白色。
        判据用卡片上部色带的**暖色像素的平均 G 通道**：
        橙色 (255,145,0) 的 G≈145 → 彩色；金色 (255,215,0) 的 G≈215 → 金色；
        白/蓝卡没有暖色带 → 白色。
        实测日志里会打印 G 均值，方便以后校准。
        """
        if frame is None:
            return 1
        h, w = frame.shape[:2]
        x1 = int(w * (0.07 + idx * 0.32))
        x2 = int(w * (0.07 + idx * 0.32 + 0.24))
        y1, y2 = int(h * 0.20), int(h * 0.34)
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return 1
        b, g, r = roi[:, :, 0].astype(int), roi[:, :, 1].astype(int), roi[:, :, 2].astype(int)
        warm = (r > 185) & (b < 120) & (g > 90)
        ratio = float(warm.mean())
        if ratio < 0.12:
            return 1
        mean_g = float(g[warm].mean())
        self.log_debug(f"技能卡{idx + 1} 暖色 {ratio:.2f} G均值 {mean_g:.0f}")
        return 3 if mean_g < 185 else 2

    # 顺序 = 优先级：
    # 1. 强判据（区域限定很死）的界面处理放前面；
    # 2. 训练日主界面 / 比赛日主界面（右下角主按钮）必须排在 handle_event_choice 之前 ——
    #    它们的底部六维属性面板（投射/体能/…）会被 event_choice 当成选项（实训 2026-10-25）；
    # 3. 剧情页判据最弱，永远放最后兜底。
    HANDLERS = (
        handle_popup,
        handle_in_match,
        handle_match_result,
        handle_skill_select,
        handle_boost_training,
        handle_prepare,
        handle_result,
        handle_dialog_continue,
        handle_lobby,
        handle_training_entry,
        handle_captain,
        handle_team,
        handle_equipment,
        handle_confirm_team,
        handle_training_day,
        handle_training_hub,
        handle_restaurant,
        handle_park,
        handle_street,
        handle_arcade,
        handle_go_out,
        handle_weekend,
        handle_subject_select,
        handle_vacation,
        handle_event_choice,
        handle_story_arrow,
    )
