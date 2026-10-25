"""养成（训练）自动化。

一局养成的流程（用户实测描述）：

    选队长 → 第1周：周一~周五训练 / 周六周日周末活动
           → 第2周起：周一比赛（赢了继续）→ 周二~周五训练 → 周六周日周末活动
           → …… 5~6 周结束，人物面板定型

脚本要做的事：把这套流程的每一个界面决策自动化，比赛用「跳过比赛」跳过。

设计照抄 ok-kes 的「单帧单动作 + 优先级链」：
每拍只 OCR 一次，然后按优先级依次交给 handler，第一个返回 True 的消费本拍。
"""

import os
import random
import re
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
            "周末先看商店": True,
            "周末野餐阈值": 60,
            "单局最大操作数": 400,
            "操作间隔(秒)": 0.3,
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
            "训练科目优先级": "同分时的先后顺序；选科目实际按 队友数+装备数 > 满经验暴击 > 推荐属性",
            "推荐属性": "队长的两项推荐属性（面板上的绿标），会额外加分",
            "失败率上限(%)": "当前科目失败率高于这个值就换科目，全部超限才休息",
            "餐厅选项": "当服务员 = 零用钱++；桌游GM = 零用钱+",
            "公园选项": "野餐 = 精力++；划船 = 属性+；散步 = 零用钱+",
            "周末先看商店": "训练流周六先去商店看一眼有没有买得起的高级器材，再决定公园项目",
            "周末野餐阈值": "周日体力低于这个值才野餐；否则选划船（属性+）",
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
        # 周末状态：按「第N周+星期X」区分，跨天自动重置
        self._weekend = {"date": None, "shop_checked": False, "bought": False, "park_choice": None}
        # 倍速：每周只尝试一次，避免在未解锁时每拍狂点
        self._speed_week = None
        self._speed_locked = False
        # 科目页：记录上次探测结果，防止同一天重复探测
        self._subject_cache = {"date": None, "best": None}
        # 商店：第一次遇到只存证，不买东西（等复盘确认界面）
        self._shop_seen = False

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
        stuck_seconds = float(self.config.get("卡死判定(秒)", 10))
        stuck_limit = max(3, int(stuck_seconds / max(interval, 0.05)))
        last_sig, stuck = None, 0
        self._last_dump = 0.0

        while self._steps < max_steps:
            if not self._enabled:
                self.log_info("任务已停止")
                return False
            self._steps += 1

            texts = self._ocr_region()
            if not texts:
                self.log_debug("本帧没有识别到文字，等待")
                self.sleep(0.3)
                continue

            if self.is_loading(texts):
                self.sleep(0.3)
                continue

            # 界面指纹：同一页面每拍都一样，用来判「卡住了没」
            sig = self._signature(texts)
            if sig == last_sig:
                stuck += 1
            else:
                stuck, last_sig = 0, sig

            # ---- 卡死检测必须**优先于所有 handler** ----
            # 否则会出现「handler 一直命中、但点了完全没反应」的死循环：
            # 例如剧情页的「跳过」是灰的，handle_dialog_continue 每拍都返回 True，
            # 于是连点 70+ 次、把 400 步全耗光（实训 2026-10-26）。
            if stuck >= stuck_limit:
                self.dump_unknown(texts, "stuck")
                self.log_warning(f"连续 {stuck + 1} 拍没变化 → 强制点一下继续")
                self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)
                stuck, last_sig = 0, None
                continue

            if self._handle_any(texts):
                self.sleep(interval)
                continue

            # ---- 兜底：不认识的界面也**让流程继续走**，不要卡死 ----
            # 存证限流：同一段时间最多 3 秒存一张，避免动画期间刷出几十张图
            if stuck == 0 and time.monotonic() - self._last_dump > 3:
                self.dump_unknown(texts, "unknown")
                self._last_dump = time.monotonic()
                self.log_warning("没见过这个界面 → 已存证，点一下继续")

            # 点画面中央：对话页能推进、空白处能关弹窗、大部分过场也吃这一下
            self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)

        self.log_warning(f"达到最大操作数 {max_steps}，停止")
        return False

    @staticmethod
    def _signature(texts):
        """界面指纹：只看**文字内容**，不看位置。

        坑（2026-10-26）：之前把坐标也算进指纹，动画/滚动时文字位置抖动 20px 就变，
        结果每一拍都被当成"新界面"，疯狂存证、stuck 永远为 0。
        """
        return hash("|".join(sorted(t.name for t in texts)))

    def dump_unknown(self, texts, tag):
        """把不认识的界面存证下来：截图 + OCR 文本，事后复盘补判据用。

        产物在 logs/unknown/（logs 已被 .gitignore 忽略，不会污染仓库）。
        """
        try:
            folder = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "logs", "unknown")
            os.makedirs(folder, exist_ok=True)
            name = f"{time.strftime('%m%d-%H%M%S')}_{tag}_s{self._steps}"
            frame = self.frame
            if frame is not None:
                cv2.imwrite(os.path.join(folder, name + ".png"), frame)
            with open(os.path.join(folder, name + ".txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(
                    f"{t.name}\t({t.x},{t.y},{t.width},{t.height})" for t in texts))
            self.log_info(f"存证 logs/unknown/{name}.png")
        except Exception as e:
            self.log_debug(f"存证失败: {type(e).__name__}: {e}")

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
        """公告 / 领奖 / 月卡 / 提示 / 「点击屏幕继续」：先关掉，否则什么都点不动。

        「点击屏幕继续」= 整屏任意位置可点（训练日开场的「新目标」弹窗就是这种），
        所以点**屏幕中央**最稳 —— 点它那行文字反而容易碰到下面的「训练/休息」。
        """
        if self.match_text(texts, "点击屏幕继续") is not None:
            self.click(int(self.width * 0.5), int(self.height * 0.4), after_sleep=0.35)
            self.log_info("弹窗：点击屏幕继续")
            return True
        target = self.match_text(texts, ("今日不再提示", "关闭", "取消"))
        if target:
            self.click_box(target, after_sleep=0.35)
            self.log_info(f"关闭弹窗「{target.name}」")
            return True
        return False

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
            self.click_box(go, after_sleep=0.6)
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
        self.click_box(target, after_sleep=0.45)
        self.log_info(f"结算页「{target.name}」")
        return True

    def choose_option(self, texts, names, after_sleep=0.4):
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
            self.click_box(confirm, after_sleep=0.45)
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
        """公园内页：散步（零用钱+）/ 划船（属性+）/ 野餐（精力++）。

        训练流的新逻辑（用户 2026-10-26）：
        - **不再无脑野餐**（之前满体力也吃野餐，浪费了 6 次属性机会）；
        - 周六先选划船（属性+，精力也会回到 50 多）；
        - 周日看体力：低于阈值才野餐，否则继续划船（回到 90 多）。
        """
        if self.match_text(texts, Defs.PARK_TITLE) is None:
            return False

        if self.style != "训练流":
            pref = self.config.get("公园选项", "野餐")
            order = (pref,) + tuple(x for x in Defs.PARK_OPTIONS if x != pref)
            return self.choose_option(texts, order)

        st = self.weekend_state()
        day = st["date"][1] if st["date"] else None
        if st.get("park_choice") is None:
            if day == "日":
                stamina = self.top_bar().get("stamina")
                threshold = int(self.config.get("周末野餐阈值", 60))
                if stamina is not None and stamina < threshold:
                    st["park_choice"] = "野餐"
                    self.log_info(f"周日体力 {stamina} < {threshold} → 野餐补精力")
                else:
                    st["park_choice"] = "划船"
                    self.log_info(f"周日体力 {stamina} ≥ {threshold} → 划船（属性+）")
            else:
                st["park_choice"] = "划船"
                self.log_info("周六公园：划船（属性+，不再满体力吃野餐）")

        order = (st["park_choice"],) + tuple(
            x for x in Defs.PARK_OPTIONS if x != st["park_choice"])
        return self.choose_option(texts, order)

    def weekend_state(self):
        """按「第N周 + 星期X」维护周末状态：周六是否看过商店、买了没、公园选什么。"""
        state = self.top_bar()
        key = (state.get("week"), state.get("day"))
        if self._weekend.get("date") != key:
            self._weekend = {
                "date": key,
                "shop_checked": False,
                "bought": False,
                "park_choice": None,
            }
        return self._weekend

    def handle_shop(self, texts):
        """商店：第一次进只存证、不盲买，随后退出。

        购买逻辑要等看到真实界面再写 —— 商店里误点「购买」会直接花钱，
        所以第一版只做「看一眼 + 存证」，绝不猜着点。
        """
        # 周末首页也有「商店」两个字，但同时有「出行」，那是按钮不是商店页
        if self.match_text(texts, "出行") is not None:
            return False
        if self.match_text(texts, ("购买", "出售", "货架", "商品", "道具店")) is None:
            return False
        if not self._shop_seen:
            self._shop_seen = True
            self.dump_unknown(texts, "shop")
            self.log_warning("第一次进商店 → 已存证 logs/unknown/shop_*.png，先退出（暂不购买）")
        else:
            self.log_info("商店：暂未实现购买，退出")
        self.exit_page(after_sleep=0.8)
        return True

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
        return self.choose_option(texts, tuple(names), after_sleep=0.4)

    def exit_page(self, after_sleep=0.45):
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
            self.click_box(skip, after_sleep=0.4)
            confirm = self.match_text(self._ocr_region("confirm_bar"), Defs.CONFIRM_TEXTS)
            if confirm is not None:
                self.click_box(confirm, after_sleep=0.45)
            self.log_info("游戏厅：跳过已三星小游戏")
            return True
        self.log_warning("游戏厅：没有可跳过的小游戏（未三星），退出")
        self.back(after_sleep=0.45)
        return True

    def handle_go_out(self, texts):
        """周末首页：周六先点「商店」看一眼，再点「出行」进地图。

        实机（2026-10-25 周六）：出行页只有「出行」两个字，没有任何地点名，
        所以 handle_weekend 认不出来，必须先点这一下。
        """
        if self.match_text(texts, Defs.WEEKEND_PLACES) is not None:
            return False          # 已经在地图上了，交给 handle_weekend
        if self.match_text(texts, "出行") is None:
            return False

        if self.style == "训练流" and self.config.get("周末先看商店", True):
            st = self.weekend_state()
            day = st["date"][1] if st["date"] else None
            shop = self.match_text(texts, "商店")
            if shop is not None and day != "日" and not st["shop_checked"]:
                st["shop_checked"] = True
                self.click_box(shop, after_sleep=0.9)
                self.log_info("周末：先看一眼商店有没有买得起的高级器材")
                return True

        self.click_text("出行", after_sleep=0.6)
        self.log_info("周末：点「出行」进地图")
        return True

    def handle_weekend(self, texts):
        """周末地图：训练流固定去公园（项目由 handle_park 按体力决定）。"""
        if not self.match_text(texts, Defs.WEEKEND_PLACES):
            return False
        if self.style == "训练流":
            places = ["公园"]
        else:
            places = self.weekend_priority()
        for place in places:
            target = self.match_text(texts, place)
            if target:
                self.click_box(target, after_sleep=0.5)
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

        # 橙色小人只表示「有队友」；数量/装备必须点开看。
        icons = self.find_teammate_icons()
        with_team = [n for n in priority if n in boxes and self.has_teammate_icon(boxes[n], icons)]
        self.log_info("有队友的科目：" + ("、".join(with_team) if with_team else "无（按推荐属性选）"))

        # 当前已经选中的科目（右边有「决定」的那一行）不要点：点它有直接开练的风险。
        # 它的预览信息直接读当前帧（彩虹边框在开页第一帧就有，器材卡随后出现）。
        current = self.current_subject(texts)
        results = {}
        if current in boxes:
            names = self.scene_names(texts)
            equip, full = self.read_equipment(texts)
            crit = self.has_crit_border()
            results[current] = {
                "mates": max(0, len(names) - 1), "names": names,
                "equip": equip, "full": full, "crit": crit,
            }
            self.log_info(
                f"当前已选「{current}」：队友 {results[current]['mates']}"
                f"｜装备 {equip}｜满经验 {full}｜彩虹 {int(crit)}")

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
                f"探测「{name}」：队友 {r['mates']}｜装备 {r['equip']}"
                f"｜满经验 {r['full']}｜彩虹 {int(r['crit'])}")

        def rank(name):
            r = results.get(name)
            base = (r["mates"] + r["equip"] + 2 * r["full"] + 2 * int(r["crit"])) if r else 0
            return (base, 1 if name in recommended else 0, -priority.index(name))

        name = max(boxes, key=rank)
        r = results.get(name, {"mates": 0, "equip": 0, "full": 0, "crit": False})
        self.log_info(
            f"选科目「{name}」（队友 {r['mates']}｜装备 {r['equip']}"
            f"｜满经验 {r['full']}｜{'推荐' if name in recommended else '非推荐'}）")

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

    def current_subject(self, texts):
        """当前已选中的科目：右边有「决定」的那一行。"""
        decide = self.match_text(texts, "决定")
        if decide is None:
            return None
        dy = decide.y + decide.height / 2
        cands = []
        for t in self.texts_in_region(texts, "subject_list"):
            if t.name.strip() not in Defs.TRAINING_SUBJECTS:
                continue
            cy = t.y + t.height / 2
            if abs(cy - dy) < 45 * self.height / 1080.0:
                cands.append(t)
        return max(cands, key=lambda t: t.height).name.strip() if cands else None

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
        """这个科目的右上角有没有小人头像。

        坑（2026-10-26）：图标中心比文字中心**高约 55px**（1080p），旧代码用
        `abs(iy - cy) < 80`，结果一个图标能同时命中上下两行，5 个科目全被当成
        「有队友」，于是每个标签都点了一遍。现在按 55px 的固定偏移 + 20px 容差。
        """
        if not icons or box is None:
            return False
        cx = box.x + box.width / 2
        cy = box.y + box.height / 2
        expect_y = cy - 55 * self.height / 1080.0
        tol = 20 * self.height / 1080.0
        return any(ix > cx and abs(iy - expect_y) < tol for ix, iy in icons)

    def probe_subject(self, box):
        """点开一个科目，返回预览信息：队友数 / 装备数 / 满经验数 / 彩虹边框。"""
        self.click_box(box, after_sleep=0.55)
        texts = self._ocr_region()
        names = self.scene_names(texts)
        equip, full = self.read_equipment(texts)
        crit = self.has_crit_border()
        self.log_debug(f"场上角色名：{names}")
        return {
            "mates": max(0, len(names) - 1),      # 减去队长（队长总在场）
            "names": names,
            "equip": equip,
            "full": full,
            "crit": crit,
        }

    def read_equipment(self, texts):
        """数预览界面底部的器材卡，并判断哪些已经满经验。

        两种形态：
        - 未满：卡片底部是黑色胶囊经验条，青色部分是进度；
        - 已满：经验条变成「MAX」字样（实测 2026-10-26），且卡片带彩虹边框。
        所以 装备数 = 经验条个数 + MAX 个数；满经验数 = MAX 个数 + 全满的经验条。
        """
        frame = self.frame
        equip = full = 0
        if frame is not None:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = Defs.region("equip_bars")
            roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
            if roi.size:
                b = roi[:, :, 0].astype(int)
                g = roi[:, :, 1].astype(int)
                r = roi[:, :, 2].astype(int)
                dark = ((b < 90) & (g < 90) & (r < 90)).astype(np.uint8)
                n, lab, stats, _cent = cv2.connectedComponentsWithStats(dark, 8)
                min_w, max_w = int(w * 0.04), int(w * 0.10)
                for i in range(1, n):
                    x, y, ww, hh, _area = stats[i]
                    if not (min_w <= ww <= max_w and 8 <= hh <= 30):
                        continue
                    equip += 1
                    cb = b[y:y + hh, x:x + ww]
                    cg = g[y:y + hh, x:x + ww]
                    cr = r[y:y + hh, x:x + ww]
                    cyan = (cb > 150) & (cg > 110) & (cr < 160)
                    if float(cyan.mean()) > 0.75:
                        full += 1
        # MAX 字样（满经验卡没有经验条）
        bar_texts = self.texts_in_region(texts, "equip_bars")
        joined = "".join(t.name for t in bar_texts).upper().replace(" ", "")
        max_count = joined.count(Defs.EQUIP_MAX_TEXT)
        if max_count:
            equip += max_count
            full += max_count
        return equip, full

    def has_crit_border(self):
        """右上角选中科目的大按钮有没有彩虹边框（= 有满经验器材，训练会暴击）。

        正常 UI 是蓝白配色；彩虹边框里有高饱和的绿(H≈35~90)和品红(H≈125~175)，
        这两个色相在正常界面几乎不出现，所以按占比判定。阈值保守，日志会打印实测值。
        """
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = Defs.region("subject_header")
        roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        if roi.size == 0:
            return False
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        magenta = (sat > 90) & (val > 120) & (hue >= 125) & (hue <= 175)
        green = (sat > 90) & (val > 120) & (hue >= 35) & (hue <= 90)
        ratio = float((magenta | green).mean())
        if ratio > 0.005:
            self.log_debug(f"科目大按钮彩虹占比 {ratio:.3f}")
        return ratio > 0.015

    def scene_names(self, texts):
        """过滤出训练场景里飘在角色头顶的名字。"""
        x1, y1, x2, y2 = Defs.region("scene_names")
        skip = {"失败率", "决定", "训练", "休息", "比赛", "商店", "出行", "跳过"}
        found = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            s = t.name.strip()
            if s in skip:
                continue
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

    # ---------- 进入养成流程（大厅 → 训练 → 选队长 → 组队 → 器材 → 开始）----------

    def handle_dialog_continue(self, texts):
        """剧情/提示：右上角有「跳过」就点，否则交给 handle_story_arrow 连点推进。"""
        skip = self.match_text(self.texts_in_region(texts, "top_right"), ("跳过", "点击屏幕继续"))
        if skip is not None:
            self.click_box(skip, after_sleep=0.35)
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
        self.flow_click("captain_card", after_sleep=0.35)
        self.log_info("选第一位队长（列表按等级降序，第一张最强）")
        x, y = self.flow_point("next_step")
        ok = self.click_and_expect(x, y, ("选择队友",), timeout=8)
        self.log_info("进入选择队友" if ok else "点「下一步」没反应")
        return True

    def handle_team(self, texts):
        if self.match_text(texts, "选择队友") is None:
            return False
        self.flow_click("auto_select", after_sleep=0.4)
        self.log_info("队友：自动选择")
        x, y = self.flow_point("next_step")
        ok = self.click_and_expect(x, y, ("选择器材",), timeout=8)
        self.log_info("进入选择器材" if ok else "点「下一步」没反应")
        return True

    def handle_equipment(self, texts):
        if self.match_text(texts, "选择器材") is None:
            return False
        self.flow_click("auto_select", after_sleep=0.4)
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
        self.flow_click("confirm_start", after_sleep=0.8)
        self.log_info("开始训练（已扣体力，不可退）")
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
        if cur is not None and cur < limit:
            self.flow_click("day_rest", after_sleep=0.6)
            self.log_info(f"体力 {cur} 偏低，休息")
        else:
            self.flow_click("day_train", after_sleep=0.6)
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
        """
        if self.match_text(texts, ("失败率", "决定", "休息")) is not None:
            return False
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
        if self.match_text(self.texts_in_region(texts, "main_action"),
                           ("VS", "比赛", "训练")) is not None:
            return False

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

    def _yellow_buttons(self):
        """找屏幕下半部分的黄色按钮框（事件选项 / 确认按钮）。

        事件按钮是「白底 + 黄色圆角边框」，所以用黄色 mask 找外框，
        再做闭运算把文字/图标造成的断口补上。返回 (x, y, w, h) 列表。
        """
        frame = self.frame
        if frame is None:
            return []
        h, w = frame.shape[:2]
        y0 = int(h * 0.60)
        roi = frame[y0:, :]
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        mask = ((r > 200) & (g > 150) & (b < 120)).astype(np.uint8)
        kx = max(3, w // 240)
        ky = max(3, h // 240)
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)))
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
        boxes = []
        for i in range(1, n):
            x, y, ww, hh, _area = stats[i]
            if ww < w * 0.12 or hh < h * 0.045:
                continue
            if ww > w * 0.95 or hh > h * 0.35:
                continue
            boxes.append((int(x), int(y + y0), int(ww), int(hh)))
        return boxes

    @staticmethod
    def _effect_ev(effect_texts):
        """从效果文本里算期望值：例如「精力+10」=10，'50%概率 精力+30'=15。

        找不到可解析的 +N 返回 None。
        """
        joined = "".join(t.name for t in effect_texts).replace(" ", "")
        best = None
        for m in re.finditer(r"(?:(\d{1,3})%概率)?[^+\d]{0,8}\+(\d{1,3})", joined):
            p = int(m.group(1)) if m.group(1) else 100
            v = int(m.group(2))
            ev = v * p / 100.0
            if best is None or ev > best:
                best = ev
        return best

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
            self.click(int(self.width * 0.37), int(self.height * 0.48), after_sleep=0.35)
            self.click_text("确定", after_sleep=0.6)
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
        self.click(int(card.x + card.width / 2), int(self.height * 0.48), after_sleep=0.35)
        self.click_text("确定", after_sleep=0.6)
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
    # 1. 强判据（区域/关键词很独特）的界面处理放前面；
    # 2. event_choice 必须排在 dialog_continue 之前 —— 事件页的「跳过」是灰的，
    #    点它没反应，会被 dialog_continue 一直"命中但无效"地空转；
    # 3. 训练日 / 比赛日主界面必须排在 event_choice 之前 ——
    #    它们的底部六维属性面板（投射/体能/…）会被 event_choice 当成选项；
    # 4. handle_result 只靠「下一步/确认」这种通用词判断，必须排在
    #    组队/器材/确认编队之后，否则会把它们的「下一步」抢走（实训 2026-10-26）；
    # 5. 剧情页判据最弱，永远放最后兜底。
    HANDLERS = (
        handle_popup,
        handle_in_match,
        handle_match_result,
        handle_skill_select,
        handle_boost_training,
        handle_event_choice,
        handle_prepare,
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
        handle_shop,
        handle_go_out,
        handle_weekend,
        handle_subject_select,
        handle_vacation,
        handle_result,
        handle_story_arrow,
    )
