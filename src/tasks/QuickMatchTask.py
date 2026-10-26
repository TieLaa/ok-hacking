"""常驻「快速比赛」：任何模式的比赛都能跳过 / 加速 / 收尾。

为什么要单独做一个 TriggerTask：

1. 所有比赛都走同一套界面：赛前准备（跳过比赛 / 前往比赛）→ 开场 ▶▶ →
   跳球 → 比赛内 HUD（自动/手动 + 快进）→ 结算「下一步」。这套流程跟养成模式无关，
   赛事、大厅 VS、剧情比赛都会遇到。
2. `TaskExecutor.next_task()` 里 **onetime 任务优先**，只有没有 onetime 任务在跑时
   才会轮到 TriggerTask。所以养成任务运行时它自动让位，不会抢点击。
3. 默认关闭（TriggerTask 的 `_enabled` 默认 False）：用户在其它模式手动打比赛时，
   不想被脚本接管；需要在任务页手动启用。

用法：
    在任务页启用「快速比赛」，之后任何比赛都会自动跳过 / 进比赛点 ▶▶ / 收结算。
"""

from ok import TriggerTask

from src.tasks.BaseGirlsTask import BaseGirlsTask
from src.tasks.training.handlers.match import MatchHandlers
from src.tasks.training.parsers import TrainingParsersMixin
from src.tasks.training.scoring import TrainingScoringMixin


class QuickMatchTask(MatchHandlers, TrainingParsersMixin, TrainingScoringMixin,
                     BaseGirlsTask, TriggerTask):

    # 顺序即优先级：先认比赛类界面，赛前准备放最后（它的「跳过/前往」是唯一的入口）
    HANDLERS = (
        MatchHandlers.handle_match_intro,
        MatchHandlers.handle_in_match,
        MatchHandlers.handle_match_result,
        MatchHandlers.handle_skill_select,
        MatchHandlers.handle_event_choice,
        MatchHandlers.handle_prepare,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "快速比赛"
        self.description = "常驻监听比赛：赛前准备自动跳过/进比赛，开场点 ▶▶，结算点下一步（其它模式也能用）"
        self.trigger_interval = 0.5
        self.default_config.update({
            "比赛日处理": "自动（先跳过，跳不了就进比赛加速）",
            "比赛内自动选技能和事件": True,
            "技能标签优先": ["得分手"],
        })
        self.config_type.update({
            "比赛日处理": {
                "type": "drop_down",
                "options": ["自动（先跳过，跳不了就进比赛加速）",
                            "总是跳过", "总是进比赛（外部战斗脚本）"],
            },
        })
        self.config_description.update({
            "比赛日处理": "自动 = 先试跳过，跳不了就进比赛点 ▶▶；总是跳过 = 跳不了也不进",
            "比赛内自动选技能和事件": "比赛内出现「选择技能 / 事件二选一」时自动点；自动模式下游戏自己会处理，可关闭",
            "技能标签优先": "同品质时优先的技能标签",
        })
        self.init_match_state()

    def run(self):
        texts = self._ocr_region()
        if not texts or self.is_loading(texts):
            return False
        auto_pick = self.config.get("比赛内自动选技能和事件", True)
        for handler in self.HANDLERS:
            if (not auto_pick
                    and handler in (MatchHandlers.handle_skill_select,
                                    MatchHandlers.handle_event_choice)):
                continue
            if handler(self, texts):
                self._last_state = handler.__name__
                self.log_debug(f"命中 {handler.__name__}")
                return True
        return False
