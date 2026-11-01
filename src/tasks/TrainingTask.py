"""养成（训练）自动化的任务入口。

分层（详见 docs/ENGINEERING.md）：

    training/flow.py      单拍状态机、存证、卡死检测
    training/parsers.py   只读解析（OCR/取色），不点击
    training/scoring.py   纯打分
    training/handlers/    按界面域分的 handler，__init__.py 里的 HANDLERS 是唯一优先级表

本文件只负责：任务元数据、默认配置、状态初始化。
"""

from collections import deque

from src.tasks.BaseGirlsTask import BaseGirlsTask
from src.tasks.training import config_spec
from src.tasks.training.constants import JPEG_PARAMS  # noqa: F401
from src.tasks.training.flow import TrainingFlowMixin
from src.tasks.training.parsers import TrainingParsersMixin
from src.tasks.training.scoring import TrainingScoringMixin
from src.tasks.training.daycycle import DayCycleMixin
from src.tasks.training.systemui import SystemUIHandlers
from src.tasks.training.interactions import InteractionsMixin
from src.tasks.training.places import (ArcadePlace, MapPlace, ParkPlace,
                                       RestaurantPlace, ShopPlace, StreetPlace)
from src.tasks.training.handlers import HANDLERS
from src.tasks.training.handlers.popup import PopupHandlers
from src.tasks.training.handlers.training import TrainingHandlers
from src.tasks.training.handlers.weekend import WeekendHandlers
from src.tasks.training.handlers.match import MatchHandlers
from src.tasks.training.handlers.entry import EntryHandlers
from src.tasks.training.handlers.fishing import FishingHandlers


class TrainingTask(TrainingFlowMixin, TrainingParsersMixin, TrainingScoringMixin,
                   DayCycleMixin, SystemUIHandlers, InteractionsMixin,
                   MapPlace, StreetPlace, RestaurantPlace, ParkPlace,
                   ArcadePlace, ShopPlace,
                   PopupHandlers, TrainingHandlers, WeekendHandlers,
                   MatchHandlers, EntryHandlers, FishingHandlers, BaseGirlsTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "养成-训练循环"
        self.description = "自动跑完一局养成：训练 / 周末活动 / 比赛日跳过，循环到赛季结束"
        self.default_config.update(config_spec.build_default_config())
        self.config_type.update(config_spec.build_config_type())
        self.config_description.update(config_spec.build_description())
        self._steps = 0
        self.init_match_state()
        self._last_state = None
        self._picked_subject = False
        self._picked_index = 0
        self._match_flags = {"manual_fixed": False, "fast_clicked": False, "last_fast_at": 0.0}
        # 「点了没反应」检测：用户口径 —— 连续点 3 次界面没变化就要开始 debug
        self._recent_actions = deque(maxlen=10)
        self._no_change = 0             # 连续几次点击后界面指纹没变
        self._sig_after_click = None    # 上一次点击后那一帧的指纹
        self._stuck_rounds = 0          # 已经被判定「点不动」的轮数
        # 周末状态：按「第N周+星期X」区分，跨天自动重置
        self._weekend = {"date": None, "shop_checked": False, "bought": False,
                         "park_choice": None}
        # 进店只在「比赛日、进比赛之前」（训练流/商店流各一次）；
        # _shop_day 记当天已经逛过的日期键，_shop_spend_all = 赛前把钱花光。
        self._shop_day = None
        self._shop_at = 0.0
        self._shop_spend_all = False
        # 科目页：记录上次探测结果，防止同一天重复探测
        self._subject_cache = {"date": None, "best": None}
        # 商店：第一次遇到只存证，不买东西（等复盘确认界面）
        self._shop_seen = False
        # 失败率超限时置 True：退出科目页后强制休息
        self._force_rest = False
        # 最近一次点击的墙钟时间：run() 的「N 秒没点击」看门狗用
        self._last_click_at = 0.0
        # 当前流派（用户 2026-10-29）：**只由队长决定**。配置里填了队长，
        # run()/点开始训练时直接查表写入；留空则由第一个训练日点日历解析。
        self._style = None
        self._style_name = None         # 日历上读到的队长名（日志/复盘用）
        # 2026-11-01：识别队长的计数器搬到 daycycle（_style_return_tries /
        # _style_calendar_tries），旧的 _style_probe_* 随 handle_style_probe 删了。

    # 顺序见 training/handlers/__init__.py
    HANDLERS = HANDLERS
