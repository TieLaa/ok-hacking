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
from src.util.emergency_stop import STOP

# 流派 → 周末去哪（按优先级，每天一个）
# 注意顺序：步行街扭蛋「消耗所有精力」，公园野餐「精力++」，
# 所以先扭蛋再野餐，相当于白拿 +48 属性；反过来就把回复浪费了。
STYLE_PLACES = {
    # 训练流：周末目标是把零用钱换成「有队友加成的黄色道具」，
    # 步行街 → 游戏厅，都没有可买的才回公园补精力（2026-10-26 用户口径）。
    # 注意 允许进步行街 / 允许进游戏厅 仍是显式开关，没开就退化成公园。
    "训练流": ["步行街", "游戏厅", "公园"],
    "扭蛋流": ["步行街", "公园"],
    "商店流": ["商店", "公园"],
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
            "购买策略": "只买金和紫",
            "每天逛商店": True,
            "周末先看商店": False,
            "周末野餐阈值": 60,
            "周末第一天优先资源点": True,
            "单局最大操作数": 400,
            "操作间隔(秒)": 0.3,
            "快进重复间隔(秒)": 1.0,
        })
        self.config_type.update({
            "流派": {"type": "drop_down", "options": list(STYLE_PLACES.keys())},
            "餐厅选项": {"type": "drop_down", "options": ["当服务员", "桌游GM"]},
            "公园选项": {"type": "drop_down", "options": ["野餐", "划船", "散步"]},
            "购买策略": {"type": "drop_down", "options": ["只买金和紫", "只买器材手册+", "买最贵", "不买"]},
            "比赛日处理": {
                "type": "drop_down",
                "options": ["自动（先跳过，跳不了就进比赛加速）", "总是跳过", "总是进比赛（外部战斗脚本）"],
            },
        })
        self.config_description.update({
            "训练科目优先级": "同分时的先后顺序；按 docs/score-model 的权重算总分：队友1 / 道具1.1 / 经理1.2 / 黄框道具暴击1.2 / 经理暴击1.3 / 彩色道具暴击1.3",
            "推荐属性": "队长的两项推荐属性（面板上的绿标），同分时优先",
            "失败率上限(%)": "训练科目页失败率高于这个值就直接回主界面休息（失败率高说明体力撑不住了）",
            "餐厅选项": "当服务员 = 零用钱++；桌游GM = 零用钱+",
            "公园选项": "野餐 = 精力++；划船 = 属性+；散步 = 零用钱+",
            "购买策略": "只买金和紫 = 只买金卡/紫卡（看到品质色判定，金优先）；只买器材手册+ = 只买涨熟练度的；买最贵 = 旧逻辑；不买 = 只逛不买",
            "每天逛商店": "每天在训练日主界面进一次商店（商店每天都能进，不用等周末）",
            "周末先看商店": "商店流在周末首页额外先看一眼商店（日常商店已由「每天逛商店」覆盖）",
            "周末第一天优先资源点": "训练流周六优先去步行街（其次游戏厅），周日再回公园补精力",
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
        self._shop_week = None          # 同一周只逛一次商店（顶栏星期 OCR 抖动时防止重复进店）
        self._shop_day = None           # 每天逛一次商店：按（第N周, 星期X）去重
        self._shop_at = 0.0             # 读不到星期时的兜底冷却（秒）
        # 倍速：每周只尝试一次，避免在未解锁时每拍狂点
        self._speed_week = None
        self._speed_locked = False
        # 科目页：记录上次探测结果，防止同一天重复探测
        self._subject_cache = {"date": None, "best": None}
        # 商店：第一次遇到只存证，不买东西（等复盘确认界面）
        self._shop_seen = False
        # 失败率超限时置 True：退出科目页后强制休息
        self._force_rest = False

    # ---------- 策略 ----------

    @property
    def style(self):
        return self.config.get("流派", "训练流")

    def weekend_priority(self):
        """周末地图上实际要去的地点（顺序 = 优先级）。

        「商店」是周末首页的按钮、不是地图上的地点，这里要过滤掉；
        步行街 / 游戏厅仍是显式开关（没验证过内页，先别默认乱进）。
        """
        places = [p for p in STYLE_PLACES.get(self.style, STYLE_PLACES["训练流"])
                  if p != "商店"]
        if not self.config.get("允许进游戏厅", False):
            places = [p for p in places if p != "游戏厅"]
        if not self.config.get("允许进步行街", False):
            places = [p for p in places if p != "步行街"]
        # 公园永远兜底：周末总得花掉一天
        if "公园" not in places:
            places.append("公园")
        return places

    def low_stamina(self, stamina_state):
        current = stamina_state.get("stamina")
        limit = int(self.config.get("休息体力阈值", 30))
        return current is not None and current < limit

    # ---------- 单拍决策 ----------

    def run(self):
        self._steps = 0
        STOP.ensure_started()
        self.log_info(f"紧急停止热键：{STOP.label or '（未注册成功，用 ok-script 自带 F9）'}")
        max_steps = int(self.config.get("单局最大操作数", 400))
        interval = float(self.config.get("操作间隔(秒)", 0.8))
        stuck_seconds = float(self.config.get("卡死判定(秒)", 10))
        stuck_limit = max(3, int(stuck_seconds / max(interval, 0.05)))
        last_sig, stuck = None, 0
        self._last_dump = 0.0

        while self._steps < max_steps:
            if STOP.consume():
                self.log_warning(f"按下紧急停止键（{STOP.label}）→ 立即中断，鼠标已释放",
                                 notify=True)
                return False
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

    def handle_rest_confirm(self, texts):
        """「休息需要一整天，可恢复体力，是否要休息？」确认框：点「确定」。

        必须排在 handle_popup 之前。旧逻辑把「取消」当通用弹窗关闭词，
        结果点了休息→确认框→点取消→再点休息，永远休息不了（实训 2026-10-26）。
        """
        if self.match_text(texts, Defs.REST_CONFIRM_TEXTS) is None:
            return False
        target = next((t for t in texts if t.name.strip() == "确定"), None)
        if target is None:
            target = next((t for t in texts if t.name.strip() == "休息"), None)
        if target is not None:
            self.click_box(target, after_sleep=0.9)
            self.log_info("休息确认：确定")
            return True
        return False

    def handle_popup(self, texts):
        """公告 / 领奖 / 月卡 / 提示 / 「点击屏幕继续」：先关掉，否则什么都点不动。

        「点击屏幕继续」= 整屏任意位置可点（训练日开场的「新目标」弹窗就是这种），
        所以点**屏幕中央**最稳 —— 点它那行文字反而容易碰到下面的「训练/休息」。
        """
        if self.match_text(texts, "点击屏幕继续") is not None:
            self.click(int(self.width * 0.5), int(self.height * 0.4), after_sleep=0.35)
            self.log_info("弹窗：点击屏幕继续")
            return True
        target = self.match_text(texts, ("今日不再提示", "关闭"))
        if target:
            self.click_box(target, after_sleep=0.35)
            self.log_info(f"关闭弹窗「{target.name}」")
            return True
        # 「取消」只在**没有确定按钮**的纯提示框里点；有确定按钮的确认框
        # 交给具体 handler（休息/购买），否则会把用户想做的操作取消掉。
        if self.match_text(texts, "确定") is None:
            cancel = next((t for t in texts if t.name.strip() == "取消"), None)
            if cancel is not None:
                self.click_box(cancel, after_sleep=0.35)
                self.log_info("关闭弹窗「取消」")
                return True
        # 抽卡/活动横幅：只有「立即前往」和右上角一个 X，没有「关闭」文字。
        # 不处理的话兜底会点屏幕中央，正好点进抽卡页（实训 2026-10-26）。
        if self.match_text(texts, "立即前往") is not None:
            self.click(int(self.width * 0.89), int(self.height * 0.17), after_sleep=0.6)
            self.log_info("关闭活动横幅")
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

    def handle_draw(self, texts):
        """大厅「幸运大抽奖」转盘：点「转一转」抽一次，结果交给弹窗处理。"""
        if self.match_text(texts, ("幸运大抽奖", "普通技能", "强力技能")) is None:
            return False
        target = self.match_text(texts, "转一转")
        if target is not None:
            self.click_box(target, after_sleep=1.2)
            self.log_info("抽奖：转一转")
        else:
            self.click(int(self.width * 0.5), int(self.height * 0.8), after_sleep=1.0)
            self.log_info("抽奖：点结果继续")
        return True

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
        """商店：按「购买策略」买，买完退出。

        实机界面（2026-10-26）：
        - 左侧商品卡：名称 + 剩余数量 + 价格（100 / 60 / 45），**卡片底色 = 品质色**；
        - 右侧是选中商品的详情和**黄色购买条**（价格就在条上）；
        - 右上角 X 关闭；顶栏金币是体力条右边那个独立数字。

        用户口径（2026-10-26）：商店每天都能进（`handle_daily_shop` 负责进店），
        但**只买金卡和紫卡**，蓝/白不买；「有钱就买最贵」是错的。
        所以默认策略是「只买金和紫」，认不出品质色就不买。
        """
        # 周末首页也有「商店」两个字，但同时有「出行」，那是按钮不是商店页
        if self.match_text(texts, "出行") is not None:
            return False
        if self.match_text(texts, "商店") is None:
            return False
        if self.match_text(texts, ("上新时间", "剩余", "能力详情")) is None:
            return False

        policy = self.config.get("购买策略", "只买金和紫")
        money = self.read_money(texts)
        items = self.read_shop_items(texts)
        self.log_info("商店：" + "、".join(
            f"{i['name']}({i['price']}/{i['quality']})" for i in items)
            + f"｜零用钱 {money}｜策略 {policy}")

        if policy == "不买":
            self.log_info("商店：策略是只逛不买，退出")
            self.close_shop(texts)
            return True

        affordable = [i for i in items if money is not None and i["price"] <= money]
        if not affordable:
            self.log_info("商店：没有买得起的商品，退出")
            self.close_shop(texts)
            return True

        if policy == "只买金和紫":
            # 用户口径：只买金卡和紫卡（蓝/白不买），金优先
            pool = [i for i in affordable if i["quality"] in ("金", "紫")]
            if not pool:
                self.log_info("商店：没有买得起的金/紫商品，不买，退出")
                self.close_shop(texts)
                return True
            pick = max(pool, key=lambda i: (1 if i["quality"] == "金" else 0, i["price"]))
        elif policy == "只买器材手册+":
            pool = [i for i in affordable if "器材手册" in i["name"]]
            if not pool:
                self.log_info("商店：没有器材手册，退出")
                self.close_shop(texts)
                return True
            pick = max(pool, key=lambda i: i["price"])
        else:
            pick = max(affordable, key=lambda i: (
                1 if "器材手册" in i["name"] else 0, i["price"]))

        self.click(pick["x"], pick["y"], after_sleep=0.6)
        # 右侧黄色购买条（实机相对位置 0.79, 0.84）
        self.click(int(self.width * 0.79), int(self.height * 0.84), after_sleep=0.8)
        self.log_info(f"商店：买「{pick['name']}」{pick['price']}（{pick['quality']}｜零用钱 {money}）")

        confirm = self.find_text(("确定", "确认"), region_name="confirm_bar")
        if confirm is not None:
            self.click_box(confirm, after_sleep=0.6)
            self.log_info("商店：确认购买")
        self._weekend["bought"] = True
        self.close_shop()
        return True

    def read_money(self, texts):
        """顶栏金币：体力条右边的独立数字（不含斜杠）。"""
        best = None
        for t in texts:
            s = t.name.strip()
            if not s.isdigit() or len(s) > 5:
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if 0.50 <= cx <= 0.62 and cy < 0.09:
                best = int(s)
        return best

    def read_shop_items(self, texts):
        """解析商店左侧商品：名称 + 价格 + 卡片中心点击坐标。"""
        prices = []
        for t in texts:
            s = t.name.strip()
            if not s.isdigit() or not (1 <= int(s) <= 9999):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if 0.15 <= cx <= 0.58 and 0.38 <= cy <= 0.75:
                prices.append((int(s), t))

        names = []
        for t in texts:
            s = t.name.strip()
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (0.15 <= cx <= 0.58 and 0.18 <= cy <= 0.62):
                continue
            if "剩余" in s or "时间" in s or "商店" in s:
                continue
            if not (2 <= len(s) <= 12):
                continue
            if not any("\u4e00" <= ch <= "\u9fff" for ch in s):
                continue
            names.append(t)

        items = []
        for price, pt in prices:
            px = pt.x + pt.width / 2
            near = [n for n in names
                    if n.y + n.height / 2 < pt.y + pt.height / 2
                    and abs((n.x + n.width / 2) - px) < self.width * 0.10]
            if not near:
                continue
            name = max(near, key=lambda n: n.y)
            quality, qh, qs = self.card_quality(name, pt)
            items.append({
                "name": name.name.strip(),
                "price": price,
                "quality": quality,
                "hue": qh,
                "sat": qs,
                # 点名称和价格之间的卡片图，避免点到价格/数量
                "x": int(name.x + name.width / 2),
                "y": int((name.y + name.height / 2 + pt.y) / 2),
            })
        return items

    def card_quality(self, name_box, price_box):
        """读商品卡片的品质色（金 / 紫 / 蓝 / 其他）。

        实机（docs/shots/2026-10-26-shop.png）：卡片底色就是品质色 ——
        紫卡 HSL 偏紫（OpenCV H≈124~132、S≈40、V≈251），
        金卡是暖黄（H≈20~40、S 更高），蓝卡 H≈100~115。
        采样卡片的**左右两条内边**（避开中间图标和文字），取中位色相。
        颜色只用来筛「只买金和紫」，认不出来就按「其他」处理（不买），
        宁可少买也不乱花钱。
        """
        frame = self.frame
        if frame is None:
            return "其他", -1, -1
        h, w = frame.shape[:2]
        x1 = int(name_box.x - w * 0.015)
        x2 = int(name_box.x + name_box.width + w * 0.015)
        y1 = int(name_box.y - h * 0.02)
        y2 = int(price_box.y + price_box.height)
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        if x2 - x1 < 20 or y2 - y1 < 40:
            return "其他", -1, -1

        band_y1 = y1 + int((y2 - y1) * 0.45)
        band_y2 = y2 - int((y2 - y1) * 0.15)
        strips = [frame[band_y1:band_y2, x1 + 4:x1 + 16],
                  frame[band_y1:band_y2, x2 - 16:x2 - 4]]
        pixels = [s.reshape(-1, 3) for s in strips if s.size]
        if not pixels:
            return "其他", -1, -1
        pixels = np.concatenate(pixels)
        hsv = cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
        colored = (hsv[:, 1] >= 20) & (hsv[:, 2] >= 170)
        if colored.sum() < 40:
            return "其他", -1, -1
        hue = float(np.median(hsv[colored, 0]))
        sat = float(np.median(hsv[colored, 1]))
        if 10 <= hue <= 45 and sat >= 55:
            return "金", hue, sat
        if 115 <= hue <= 155:
            return "紫", hue, sat
        if 90 <= hue < 115:
            return "蓝", hue, sat
        return "其他", hue, sat

    def close_shop(self, texts=None):
        """点右上角 X 关商店。"""
        texts = texts or self._ocr_region()
        target = self.match_text(self.texts_in_region(texts, "top_right"), "X")
        if target is not None:
            self.click_box(target, after_sleep=0.8)
        else:
            self.click(int(self.width * 0.94), int(self.height * 0.11), after_sleep=0.8)
        self.log_info("商店：关闭")

    def handle_street(self, texts):
        """步行街扭蛋：三选一（有的会清空精力），选完按确定。

        用户口径（2026-10-26）：「清空精力」的扭蛋**只有体力低的时候才抽** ——
        低体力时反正也要靠休息回，用它换高品质属性是赚的；
        体力高的时候去抽就纯亏（第二天没法训练）。
        注意：不是「体力高就整条街都不进」，高体力照样可以抽不清空的扭蛋。
        左上角那个「返回」会打开训练计划日历，要用**右上角**的返回箭头。
        """
        if self.match_text(texts, Defs.STREET_TITLE) is None:
            return False
        if not self.config.get("允许进步行街", False):
            self.log_info("步行街扭蛋：未开启，退出")
            self.exit_page()
            return True

        stamina = self.top_bar().get("stamina")
        limit = int(self.config.get("休息体力阈值", 40))
        low = stamina is not None and stamina < limit

        opts = self.texts_in_region(texts, "weekend_options")
        opts = [t for t in opts if 2 <= len(t.name.strip()) <= 10]
        clearing = [t for t in opts
                    if any(k in t.name for k in Defs.GACHA_CLEAR_TEXTS)]

        if low and clearing:
            pick = max(clearing, key=lambda t: (t.height, -t.y))
            self.click_box(pick, after_sleep=0.4)
            confirm = self.find_text(("确定", "确认"))
            if confirm is not None:
                self.click_box(confirm, after_sleep=0.45)
            self.log_info(f"步行街：体力 {stamina} < {limit}，抽清空精力的「{pick.name.strip()}」")
            return True

        if clearing and not low:
            self.log_info(f"步行街：体力 {stamina} ≥ {limit}，跳过会清空精力的选项 "
                          + "、".join(t.name.strip() for t in clearing))

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

        # 商店只有「商店流」才进（用户 2026-10-26：训练流的钱优先换步行街/游戏厅的黄色道具）
        if self.style == "商店流":
            st = self.weekend_state()
            day = st["date"][1] if st["date"] else None
            week = st["date"][0] if st["date"] else None
            if week is None:
                # 顶栏 OCR 偶尔读不到周数；沿用上次的周，避免同一周重复进店
                week = self._shop_week
            shop = self.match_text(texts, "商店")
            if (shop is not None and day != "日" and not st["shop_checked"]
                    and self._shop_week != week):
                st["shop_checked"] = True
                self._shop_week = week
                self.click_box(shop, after_sleep=0.9)
                self.log_info("周末：商店流，先去商店")
                return True

        self.click_text("出行", after_sleep=0.6)
        self.log_info("周末：点「出行」进地图")
        return True

    def handle_weekend(self, texts):
        """周末地图：按流派顺序去地点。

        训练流（用户 2026-10-26）：周六先去步行街，其次游戏厅，
        找「有队友加成的黄色道具」；周日再回公园补精力。
        `允许进步行街 / 允许进游戏厅` 没开时这两个地点会被过滤掉，退化成公园。
        """
        if not self.match_text(texts, Defs.WEEKEND_PLACES):
            return False
        places = self.weekend_priority()
        if self.style == "训练流" and self.config.get("周末第一天优先资源点", True):
            st = self.weekend_state()
            day = st["date"][1] if st["date"] else None
            if day == "日":
                places = ["公园"]
            self.log_info(f"周末{'日' if day == '日' else '六'}：顺序 {places}")
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
                self.click(int(self.width * 0.958), int(self.height * 0.060), after_sleep=0.8)
                return True
            self.log_warning(f"失败率 {fail}% 超过上限 {limit}%（配置为不休息，继续）")

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

        def rank(name):
            """按用户 2026-10-26 给的权重算总分（不是拍脑袋的相对分）。

            队友 1.0 / 道具 1.1 / 经理 1.2 /
            黄框道具暴击 1.2 / 经理暴击 1.3 / 彩色道具暴击 1.3。
            同分时：带队长推荐属性的优先，再按配置里的科目顺序。
            """
            r = results.get(name)
            return (self.subject_score(r), 1 if name in recommended else 0,
                    -priority.index(name))

        name = max(boxes, key=rank)
        r = results.get(name) or self.empty_preview()
        self.log_info(
            f"选科目「{name}」（队友 {r['mates']}｜经理 {r['manager']}｜装备 {r['equip']}"
            f"｜满经验 {r['full']}｜金框 {r['gold']}｜得分 {self.subject_score(r):.2f}"
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
        """这个科目的右边有没有小人头像。

        两代 UI 都支持（2026-10-26 实测）：
        - 旧版：头像在按钮**右上角**，中心比文字中心高约 55px；
        - 新版：头像在按钮**右侧居中**，和文字中心几乎同高。
        不能用 ±80 的大范围：相邻两行只差 100px，会同时命中两个科目
        （旧代码就是这么把 5 个科目全当成「有队友」的）。
        """
        if not icons or box is None:
            return False
        cx = box.x + box.width / 2
        cy = box.y + box.height / 2
        old_ui_y = cy - 55 * self.height / 1080.0
        tol_old = 20 * self.height / 1080.0
        tol_new = 35 * self.height / 1080.0
        return any(
            ix > cx and (abs(iy - old_ui_y) < tol_old or abs(iy - cy) < tol_new)
            for ix, iy in icons)

    def probe_subject(self, box):
        """点开一个科目，返回预览信息：队友数 / 装备数 / 满经验数 / 彩虹边框。"""
        self.click_box(box, after_sleep=0.55)
        texts = self._ocr_region()
        self.log_debug(f"场上角色名：{self.scene_names(texts)}")
        return self.analyze_preview(texts)

    # ---------- 科目预览打分（用户 2026-10-26 简化版权重）----------
    # 队友 1 / 道具 1 / 经理 1 / 黄框 +0.1 / 彩框 +0.2
    # （用户分不清「经理暴击」和「道具暴击」，所以不再单独给暴击项加权，
    #   只把黄框、彩框当作同分时的小加成。）
    W_MATE = 1.0
    W_EQUIP = 1.0
    W_MANAGER = 1.0
    W_GOLD_CRIT = 0.1
    W_COLOR_CRIT = 0.2

    @classmethod
    def empty_preview(cls):
        return {"names": [], "mates": 0, "manager": 0, "equip": 0, "full": 0,
                "gold": 0, "crit": False, "color": 0, "manager_crit": 0}

    @classmethod
    def subject_score(cls, r):
        """按权重算总分（全部是正分，直接排序）。"""
        if not r:
            return 0.0
        return (r["mates"] * cls.W_MATE
                + r["equip"] * cls.W_EQUIP
                + r["manager"] * cls.W_MANAGER
                + r["gold"] * cls.W_GOLD_CRIT
                + r["color"] * cls.W_COLOR_CRIT)

    def analyze_preview(self, texts):
        """读一帧科目预览，返回打分要用的全部字段。

        - 队友 / 经理：场景里飘在角色头顶的名字（经理的名字牌就是「球队经理」，
          会被当成队友名，必须单独扣出来）；
        - 器材数 / 满经验：底部器材卡的经验条与 MAX 字样（已实测）；
        - 金框：器材卡**边框**是金色品质（`count_gold_cards`）；
        - 彩色（暴击）：满经验 MAX 卡片 —— 结算会出「暴击训练!」，已验证；
        - 经理暴击：经理在场且这次训练确实出暴击（有满经验或彩虹边框）。
        """
        scene = self.read_scene(texts)
        equip, full, gold = self.read_equipment(texts)
        crit = self.has_crit_border(texts)
        return {
            "names": scene["names"],
            "mates": scene["mates"],
            "manager": scene["manager"],
            "equip": equip,
            "full": full,
            "gold": gold,
            "crit": crit,
            "color": full,
            "manager_crit": 1 if (scene["manager"] and (full > 0 or crit)) else 0,
        }

    def read_scene(self, texts):
        """场上角色：队友数（扣掉队长和经理）+ 经理在不在。"""
        names = self.scene_names(texts)
        manager = 1 if any(any(m in n for m in Defs.MANAGER_TEXTS) for n in names) else 0
        mates = [n for n in names if not any(m in n for m in Defs.MANAGER_TEXTS)]
        return {
            "names": names,
            "manager": manager,
            # 队长总在场，所以队友数 = 场上名字 - 队长（- 经理已经被扣掉）
            "mates": max(0, len(mates) - 1),
        }

    def read_equipment(self, texts):
        """数预览界面底部的器材卡，并判断哪些已经满经验、哪些是金框。

        两种形态：
        - 未满：卡片底部是黑色胶囊经验条，青色部分是进度；
        - 已满：经验条变成「MAX」字样（实测 2026-10-26），且卡片带彩虹边框。
        所以 装备数 = 经验条个数 + MAX 个数；满经验数 = MAX 个数 + 全满的经验条。
        金框另算：数卡片外框的金色占比（紫色/蓝色品质不算）。
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
        return equip, full, self.count_gold_cards()

    def count_gold_cards(self):
        """数预览区里「金框」器材卡的张数。

        实机（docs/shots/2026-10-26-subject-crit.png）：金品质器材卡是亮黄橙外框，
        色相落在 HSV H 12~45、S>120、V>180；紫牌/蓝牌不在此区间。
        做法：直接在 equip_cards 区域取「金色像素」，闭运算补断口，
        再按卡片尺寸（宽高 0.05~0.20 屏宽）和长宽比 0.6~1.6 过滤。

        先试过「高饱和色块」整体找卡片，但卡片会和天空/球场连成一片
        （312x168，长宽比 1.86），过滤不掉，所以改成按颜色直取。
        日志会打印每个候选框，实机跑一次就能拿来校准。
        """
        frame = self.frame
        if frame is None:
            return 0
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = Defs.region("equip_cards")
        roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        if roi.size == 0:
            return 0

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        gold = ((hue >= 12) & (hue <= 45) & (sat > 120) & (val > 180)).astype(np.uint8)
        gold = cv2.morphologyEx(gold, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(gold, 8)

        min_w, max_w = int(w * 0.05), int(w * 0.20)
        count = 0
        for i in range(1, n):
            x, y, ww, hh, area = stats[i]
            if area < 800:
                continue
            if not (min_w <= ww <= max_w and min_w <= hh <= max_w):
                continue
            if not (0.6 <= ww / max(hh, 1) <= 1.6):
                continue
            if area < ww * hh * 0.30:
                continue
            count += 1
            self.log_debug(f"金框器材 @({x + ww // 2},{y + hh // 2}) {ww}x{hh} 实心 {area / (ww * hh):.2f}")
        return count

    def has_crit_border(self, texts=None):
        """选中的科目大按钮有没有彩虹边框（= 有满经验器材，训练会暴击）。

        注意：大按钮的位置随科目行变化（选中的科目会停在它原来的行），
        所以不能写死 y=0.12~0.30；要用「决定」按钮的 y 找到它所在的那一行。
        只采样按钮**边缘一圈**：按钮内部的科目图标（西瓜/鞋）有绿色/红色，
        采样整块会把普通科目误判成彩虹（实训 2026-10-26）。
        """
        frame = self.frame
        if frame is None:
            return False
        texts = texts if texts is not None else self._ocr_region()
        decide = self.match_text(texts, "决定")
        if decide is None:
            return False
        h, w = frame.shape[:2]
        cy = decide.y + decide.height / 2
        x1, x2 = int(w * 0.66), int(w * 0.93)
        y1 = max(0, int(cy - h * 0.06))
        y2 = min(h, int(cy + h * 0.06))
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return False
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        magenta = (sat > 90) & (val > 120) & (hue >= 125) & (hue <= 175)
        green = (sat > 90) & (val > 120) & (hue >= 35) & (hue <= 90)
        ring = np.zeros(roi.shape[:2], dtype=bool)
        t = max(4, int(h * 0.008))
        ring[:t, :] = True
        ring[-t:, :] = True
        ring[:, :t] = True
        ring[:, -t:] = True
        hit = (magenta | green) & ring
        ratio = float(hit.sum()) / max(int(ring.sum()), 1)
        if ratio > 0.02:
            self.log_debug(f"科目大按钮彩虹占比 {ratio:.3f}")
        return ratio > 0.06

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

        # 训练事件 / 只有 ▼ 的剧情页（实机 2026-10-26）：右下角白色 ▼ 是最可靠的信号。
        # 这类页面没有长台词，旧判据认不出来，会一直卡在「没见过」。
        if self.match_text(texts, "训练事件") is not None or self.has_story_arrow():
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
        if self.match_text(texts, "训练核心") is None:
            return False
        self.flow_click("captain_card", after_sleep=0.5)
        self.log_info("选第一位队长（列表按等级降序，第一张最强）")
        # 选完队长后按钮可能移动，重新 OCR 再点「下一步」
        ok = self.click_text_and_expect(
            self._ocr_region(), "下一步", ("选择队友",), timeout=8)
        self.log_info("进入选择队友" if ok else "点「下一步」没反应")
        return True

    def handle_team(self, texts):
        if self.match_text(texts, "选择队友") is None:
            return False
        auto = self.match_text(texts, "自动选择")
        if auto is not None:
            self.click_box(auto, after_sleep=0.7)
            self.log_info("队友：自动选择")
        # 自动选择后队伍从 1 人变 5 人，底部面板会整体下移 40~50px，
        # 必须重新 OCR「下一步」，不能复用点击前的坐标（实训 2026-10-26）。
        ok = self.click_text_and_expect(
            self._ocr_region(), "下一步", ("选择器材",), timeout=8)
        if not ok:
            x, y = self.flow_point("next_step")
            ok = self.click_and_expect(x, y, ("选择器材",), timeout=5)
        self.log_info("进入选择器材" if ok else "点「下一步」没反应")
        return True

    def handle_equipment(self, texts):
        if self.match_text(texts, "选择器材") is None:
            return False
        auto = self.match_text(texts, "自动选择")
        if auto is not None:
            self.click_box(auto, after_sleep=0.7)
            self.log_info("器材：自动选择")
        ok = self.click_text_and_expect(
            self._ocr_region(), "下一步", ("确认编队",), timeout=8)
        if not ok:
            x, y = self.flow_point("next_step")
            ok = self.click_and_expect(x, y, ("确认编队",), timeout=5)
        self.log_info("进入确认编队" if ok else "点「下一步」没反应")
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
        self.log_info("开始训练（已扣体力，不可退）")
        return True

    def handle_daily_shop(self, texts):
        """训练日主界面：每天进一次商店。

        用户口径（2026-10-26）：商店**每天都能进**，不用非等到周末。
        判据：有「星期」+「休息」+「商店」，且**没有「出行」**
        （有「出行」的是周末首页，走 handle_go_out 那条线）。
        按（第N周, 星期X）去重：买完关店回到主界面不会又被点进去。
        """
        if not self.config.get("每天逛商店", True):
            return False
        if self.match_text(texts, "星期") is None or self.match_text(texts, "休息") is None:
            return False
        if self.match_text(texts, "出行") is not None:
            return False
        shop = self.match_text(texts, "商店")
        if shop is None:
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
        self._shop_at = time.monotonic()
        self._shop_day = key
        self.click_box(shop, after_sleep=0.9)
        self.log_info(f"每日商店：第{key[0]}周 星期{key[1]}，进去只买金/紫")
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
                if s in ui_words:
                    continue
                if not (0.30 <= cx <= 0.70 and 0.20 <= cy <= 0.70):
                    continue
                if not (2 <= len(s) <= 10) or not all(
                        "\u4e00" <= ch <= "\u9fff" for ch in s):
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

    def _has_white_band(self, t):
        """文字所在的那条横带是不是白底（事件选项按钮）。

        竖向 3 选 1 的真按钮是「白底彩边」；训练动画的「训练加成/表情加成」
        是深色背景上的文字，白底占比很低。用这一条把两者分开。
        """
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        cy = int(t.y + t.height / 2)
        y1, y2 = max(0, cy - 10), min(h, cy + 10)
        x1, x2 = int(w * 0.22), int(w * 0.78)
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return False
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        bright = (b > 195) & (g > 190) & (r > 180)
        return float(bright.mean()) > 0.50

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
        handle_rest_confirm,
        handle_popup,
        handle_draw,
        handle_in_match,
        handle_training_result,
        handle_match_result,
        handle_skill_select,
        handle_boost_training,
        handle_subject_select,
        handle_event_choice,
        handle_prepare,
        handle_dialog_continue,
        handle_lobby,
        handle_training_entry,
        handle_captain,
        handle_team,
        handle_equipment,
        handle_confirm_team,
        handle_daily_shop,
        handle_training_day,
        handle_training_hub,
        handle_restaurant,
        handle_park,
        handle_street,
        handle_arcade,
        handle_shop,
        handle_go_out,
        handle_weekend,
        handle_vacation,
        handle_result,
        handle_story_arrow,
    )
