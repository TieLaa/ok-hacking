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
from src.tasks.training.constants import JPEG_PARAMS, STYLE_PLACES  # noqa: F401
from src.tasks.training.flow import TrainingFlowMixin
from src.tasks.training.parsers import TrainingParsersMixin
from src.tasks.training.scoring import TrainingScoringMixin
from src.tasks.training.handlers import HANDLERS
from src.tasks.training.handlers.popup import PopupHandlers
from src.tasks.training.handlers.training import TrainingHandlers
from src.tasks.training.handlers.weekend import WeekendHandlers
from src.tasks.training.handlers.match import MatchHandlers
from src.tasks.training.handlers.entry import EntryHandlers


class TrainingTask(TrainingFlowMixin, TrainingParsersMixin, TrainingScoringMixin,
                   PopupHandlers, TrainingHandlers, WeekendHandlers,
                   MatchHandlers, EntryHandlers, BaseGirlsTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "养成-训练循环"
        self.description = "自动跑完一局养成：训练 / 周末活动 / 比赛日跳过，循环到赛季结束"
        self.default_config.update({
            "流派": "训练流",
            "失败率上限(%)": 50,
            "失败率超限改休息": True,
            "餐厅选项": "当服务员",
            "公园选项": "野餐",
            "扭蛋优先属性": ["投射", "体能", "运控"],
            "允许进游戏厅": True,
            "允许进步行街": True,
            "允许开始训练": True,
            "休息体力阈值": 40,
            "技能标签优先": ["得分手"],
            "指定队长": "",
            "新一局删档": False,
            "比赛日处理": "自动（先跳过，跳不了就进比赛加速）",
            "购买策略": "只买金和紫",
            "每天逛商店": True,
            "商店进店余额": 300,
            "赛前清空余额": False,
            "只在决战前清空": False,
            "周末先看商店": False,
            "周末野餐阈值": 60,
            "周末第一天优先资源点": True,
            "单局最大操作数": 400,
            "操作间隔(秒)": 0.3,
            "快进重复间隔(秒)": 1.0,
            "卡死判定(秒)": 5,
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
            "指定队长": "填角色名（如「真崎辉世」）就逐张点开找她当队长；留空 = 点第一张（等级最高）",
            "新一局删档": "遇到「训练确认」弹窗时点「删档」开新的一局；只在旧局已经跑完时打开",
            "失败率上限(%)": "训练科目页失败率高于这个值就直接回主界面休息（失败率高说明体力撑不住了）",
            "餐厅选项": "当服务员 = 零用钱++；桌游GM = 零用钱+",
            "公园选项": "野餐 = 精力++；划船 = 属性+；散步 = 零用钱+",
            "购买策略": "只买金和紫 = 只买金卡/紫卡（看到品质色判定，金优先）；只买器材手册+ = 只买涨熟练度的；买最贵 = 旧逻辑；不买 = 只逛不买",
            "每天逛商店": "每天在训练日主界面看一次：零用钱达到「进店余额」才进去（不分流派）",
            "商店进店余额": "零用钱 ≥ 这个数才进店；比赛前一天不受限制（要花光）",
            "赛前清空余额": "比赛前一天进店把零用钱花光（金紫优先，买完再买剩下的）；默认关闭，先把正常流程跑顺",
            "只在决战前清空": "打开后只有决赛前一天才清空（需同时打开「赛前清空余额」；决赛判据还没实测）",
            "周末先看商店": "商店流在周末首页额外先看一眼商店（日常商店已由「每天逛商店」覆盖）",
            "周末第一天优先资源点": "训练流周六优先去步行街（其次游戏厅），周日再回公园补精力",
            "周末野餐阈值": "周日体力低于这个值才野餐；否则选划船（属性+）",
            "扭蛋优先属性": "步行街扭蛋三选一，按顺序找第一个存在的（会消耗所有精力）",
            "比赛日处理": "没打赢过的比赛不能跳过，只能进比赛后点左下角快进",
            "快进重复间隔(秒)": "快进点一次管一段时间，1 秒补点一次即可；0 = 只点一次",
            "单局最大操作数": "防止卡死无限点，超过后自动停止",
            "卡死判定(秒)": "连续这么多秒没有任何点击、界面文字也没变化，就判定卡住：存证 logs/debug/ 后点屏幕中央继续，连续 3 轮则停止",
        })
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
        self._weekend = {"date": None, "shop_checked": False, "bought": False, "park_choice": None}
        self._shop_day = None           # 每天逛一次商店：按（第N周, 星期X）去重
        self._shop_at = 0.0             # 读不到星期时的兜底冷却（秒）
        self._shop_key = None           # 本次进店用的日期键（关店时写回 _shop_day）
        self._shop_spend_all = False    # 赛前一天：把零用钱花光
        # 科目页：记录上次探测结果，防止同一天重复探测
        self._subject_cache = {"date": None, "best": None}
        # 商店：第一次遇到只存证，不买东西（等复盘确认界面）
        self._shop_seen = False
        # 失败率超限时置 True：退出科目页后强制休息
        self._force_rest = False
        # 最近一次点击的墙钟时间：run() 的「N 秒没点击」看门狗用
        self._last_click_at = 0.0

    # 顺序见 training/handlers/__init__.py
    HANDLERS = HANDLERS
