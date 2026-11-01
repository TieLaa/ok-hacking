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
from src.tasks.training.constants import JPEG_PARAMS  # noqa: F401
from src.tasks.training.flow import TrainingFlowMixin
from src.tasks.training.parsers import TrainingParsersMixin
from src.tasks.training.scoring import TrainingScoringMixin
from src.tasks.training.handlers import HANDLERS
from src.tasks.training.handlers.popup import PopupHandlers
from src.tasks.training.handlers.training import TrainingHandlers
from src.tasks.training.handlers.weekend import WeekendHandlers
from src.tasks.training.handlers.match import MatchHandlers
from src.tasks.training.handlers.entry import EntryHandlers
from src.tasks.training.handlers.fishing import FishingHandlers


class TrainingTask(TrainingFlowMixin, TrainingParsersMixin, TrainingScoringMixin,
                   PopupHandlers, TrainingHandlers, WeekendHandlers,
                   MatchHandlers, EntryHandlers, FishingHandlers, BaseGirlsTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "养成-训练循环"
        self.description = "自动跑完一局养成：训练 / 周末活动 / 比赛日跳过，循环到赛季结束"
        self.default_config.update({
            "失败率上限(%)": 50,
            "失败率超限改休息": True,
            "公园选项": "野餐",
            "扭蛋优先属性": ["投射", "体能", "运控"],
            # 2026-11-01 用户口径：训练等级提升后解锁了**彩色**扭蛋，
            # 品质优先级 彩色 > 金色 > 紫色；同分先「增强款」再有队友（都相同比价格）。
            "扭蛋品质优先": ["彩", "金", "紫"],
            "允许进游戏厅": True,
            "游戏厅付费": True,
            "允许进步行街": True,
            "允许开始训练": True,
            "休息体力阈值": 40,
            "技能标签优先": ["得分手"],
            "指定队长": "",
            # 监控页「分数预测」用：技能品质数量（彩/金/白），这里填**开局自带**的数量，
            # 本局三选一拿到的由 info["skill_gained"] 实时累加。
            # 评分 = 六维总和 + 75×彩 + 50×金 + 25×白（docs/score-model）
            # 2026-10-31：开局自带 = 1 彩（角色专属大招）+ 1 白（角色被动），
            # score-model 里 N=2 的样本全是「1 彩 + 1 白」（+100 分），所以白也要填 1。
            "技能-彩色": 1,
            "技能-金色": 0,
            "技能-白色": 1,
            "无队长不接开局": True,
            "新一局删档": False,
            "比赛日处理": "自动（先跳过，跳不了就进比赛加速）",
            "购买策略": "只买金和紫",
            "赛前清空余额": False,
            "只在决战前清空": False,
            "周末野餐阈值": 60,
            "单局最大操作数": 400,
            "操作间隔(秒)": 0.3,
            "快进重复间隔(秒)": 1.0,
            "卡死判定(秒)": 5,
            "空闲等待(秒)": 3,
        })
        self.config_type.update({
            "公园选项": {"type": "drop_down", "options": ["野餐", "划船", "散步"]},
            "购买策略": {"type": "drop_down", "options": ["只买金和紫", "只买器材手册+", "买最贵", "不买"]},
            "比赛日处理": {
                "type": "drop_down",
                "options": ["自动（先跳过，跳不了就进比赛加速）", "总是跳过", "总是进比赛（外部战斗脚本）"],
            },
        })
        self.config_description.update({
            "指定队长": "填角色名（如「真崎辉世」「佐久间」）→ 按这个角色的流派跑，开局就定好、不点日历；"
                        "留空 → 快速接手：进训练后点一次日历，按「XX的训练计划」里队长的流派跑",
            "无队长不接开局": "指定队长留空时生效：只跑你手动开好的局，遇到大厅/启程/选人/笔记/器材/"
                             "确认编队这些开局页面就停下，不替你开局。勾上队长后自动恢复",
            "新一局删档": "遇到「训练确认」弹窗时点「删档」开新的一局；只在旧局已经跑完时打开",
            "失败率上限(%)": "训练科目页失败率高于这个值就直接回主界面休息（失败率高说明体力撑不住了）",
            "公园选项": "野餐 = 精力++；划船 = 属性+；散步 = 零用钱+",
            "公园选项": "野餐 = 精力++；划船 = 属性+；散步 = 零用钱+",
            "购买策略": "只买金和紫 = 只买金卡/紫卡（看到品质色判定，金优先）；只买器材手册+ = 只买涨熟练度的；买最贵 = 旧逻辑；不买 = 只逛不买",
            "赛前清空余额": "比赛前一天进店把零用钱花光（金紫优先，买完再买剩下的）；默认关闭，先把正常流程跑顺",
            "只在决战前清空": "打开后只有决赛前一天才清空（需同时打开「赛前清空余额」；决赛判据还没实测）",
            "游戏厅付费": "游戏厅没有「跳过」时，花钱（≥64 零用钱）买一个「自动」小游戏换属性；关掉就沿用旧逻辑直接退出",
            "周末野餐阈值": "周日体力低于这个值才野餐；否则选划船（属性+）",
            "扭蛋优先属性": "步行街扭蛋三选一，按顺序找第一个存在的（会消耗所有精力）",
            "扭蛋品质优先": "扭蛋只买这些品质，靠前的优先：默认 彩 > 金 > 紫（彩色是训练等级提升后解锁的）；"
                            "同等情况先「增强款」、再有队友加成的，都相同才买便宜的；蓝卡/其他一律不买",
            "比赛日处理": "没打赢过的比赛不能跳过，只能进比赛后点左下角快进",
            "快进重复间隔(秒)": "快进点一次管一段时间，1 秒补点一次即可；0 = 只点一次",
            "单局最大操作数": "防止卡死无限点，超过后自动停止",
            "卡死判定(秒)": "连续这么多秒没有任何点击、界面文字也没变化，就判定卡住：存证 logs/debug/ 后点屏幕中央继续，连续 3 轮则停止",
            "空闲等待(秒)": "你动鼠标或敲键盘时脚本立刻停手，等你停手这么多秒后才重新拉起游戏继续挂机（期间绝不抢前台）。0 = 不等（用户操作时也照抢）",
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
        self._style_probe_tries = 0     # 开日历试了几次（最多 3 次）
        self._style_probe_at = 0.0      # 上次开日历的时间（3 秒冷却）

    # 顺序见 training/handlers/__init__.py
    HANDLERS = HANDLERS
