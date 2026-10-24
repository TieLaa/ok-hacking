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

# 流派 → 周末去哪（按优先级）
STYLE_PLACES = {
    "训练流": ["公园", "餐厅", "游戏厅", "步行街"],   # 体力优先，保证训练不断
    "商店流": ["餐厅", "步行街", "公园", "游戏厅"],   # 打工赚钱 → 商店消费
    "扭蛋流": ["步行街", "公园", "餐厅", "游戏厅"],   # 扭蛋
    "小游戏流": ["游戏厅", "公园", "餐厅", "步行街"],  # 小游戏
}


class TrainingTask(BaseGirlsTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "养成-训练循环"
        self.description = "自动跑完一局养成：训练 / 周末活动 / 比赛日跳过，循环到赛季结束"
        self.default_config.update({
            "流派": "训练流",
            "训练科目优先级": ["投射训练", "防守训练", "弹跳训练", "运控训练", "体能训练", "意识训练"],
            "比赛日处理": "自动（先跳过，跳不了就进比赛加速）",
            "体力低于多少改休息": 30,
            "单局最大操作数": 400,
            "操作间隔(秒)": 0.8,
            "快进重复间隔(秒)": 0,
        })
        self.config_type.update({
            "流派": {"type": "drop_down", "options": list(STYLE_PLACES.keys())},
            "比赛日处理": {
                "type": "drop_down",
                "options": ["自动（先跳过，跳不了就进比赛加速）", "总是跳过", "总是进比赛（外部战斗脚本）"],
            },
        })
        self.config_description.update({
            "训练科目优先级": "按顺序找第一个可点的训练科目；体力不足时自动改休息",
            "比赛日处理": "没打赢过的比赛不能跳过，只能进比赛后点左下角加速",
            "单局最大操作数": "防止卡死无限点，超过后自动停止",
        })
        self._steps = 0
        self._skip_tries = 0
        self._last_state = None
        self._match_flags = {"manual_fixed": False, "fast_clicked": False, "last_fast_at": 0.0}

    # ---------- 策略 ----------

    @property
    def style(self):
        return self.config.get("流派", "训练流")

    def weekend_priority(self):
        return STYLE_PLACES.get(self.style, STYLE_PLACES["训练流"])

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

    def handle_subject_select(self, texts):
        """训练科目选择页：按配置优先级选，体力不足改休息。"""
        subject = None
        for name in self.config.get("训练科目优先级", []):
            found = self.match_text(texts, name)
            if found:
                subject = found
                break
        if subject is None:
            rest = self.match_text(texts, ("休息", "恢复体力"))
            if rest is None:
                return False
            self.click_box(rest, after_sleep=1.2)
            self.log_info("没有可训练科目，选择休息")
            return True

        state = self.top_bar()
        if self.low_stamina(state):
            rest = self.match_text(texts, ("休息", "恢复体力"))
            if rest:
                self.click_box(rest, after_sleep=1.2)
                self.log_info(f"体力 {state.get('stamina')} 过低，改为休息")
                return True

        self.click_box(subject, after_sleep=1.5)
        self.log_info(f"训练「{subject.name}」")
        return True

    def handle_training_hub(self, texts):
        """训练日主界面：六维属性 + 右下角主按钮。"""
        if not self.match_text(texts, ("投射", "弹跳", "运控")):
            return False
        # 今天有比赛 → 主按钮是「VS 比赛」，交给 prepare 页处理
        if self.match_text(texts, ("今天", "VS")):
            self.click_main_action(after_sleep=1.5)
            return True
        self.click_main_action(after_sleep=1.5)
        return True

    HANDLERS = (
        handle_popup,
        handle_in_match,
        handle_prepare,
        handle_result,
        handle_weekend,
        handle_subject_select,
        handle_training_hub,
    )
