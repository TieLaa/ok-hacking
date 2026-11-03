"""handler 注册表：**顺序就是优先级**，唯一来源。

规则：
1. 强判据（区域/关键词独特）放前面；
2. 共用「下一步/确定」这类通用词的，必须排在后面的专属 handler 之后；
3. 新 handler 先写进对应子模块，再在这里决定位置；
4. `tools/check_handlers.py` 会逐张图比对 `docs/shots/expected.json`，
   顺序改错会直接报红。
"""

from .popup import PopupHandlers
from .training import TrainingHandlers
from .weekend import WeekendHandlers
from .match import MatchHandlers
from .entry import EntryHandlers
from .fishing import FishingHandlers
from .theme import ThemeHandlers
from .cooking import CookingHandlers
from .wheel import WheelHandlers
from .unmarked import UnmarkedHandlers

HANDLERS = (
    # 注册球员收尾流程必须排在 handle_popup 之前：它有「关闭」按钮，
    # 排在后面会被 popup 抢走（用户 2026-10-27 提供的 6 屏流程）。
    MatchHandlers.handle_register_player,
    PopupHandlers.handle_resume_training,
    PopupHandlers.handle_rest_confirm,
    PopupHandlers.handle_popup,
    PopupHandlers.handle_draw,
    # 训练计划（日历）整页：必须排在所有地点 handler 之前 ——
    # 这一页带「野餐/划船/转动扭蛋/训练」，旧版被 handle_street / handle_park 抢走
    # （2026-10-27 00:54 当成扭蛋页退出并误记「步行街已去」；15:17 那局被点了「野餐」）。
    TrainingHandlers.handle_training_plan,
    MatchHandlers.handle_match_intro,
    MatchHandlers.handle_in_match,
    TrainingHandlers.handle_training_result,
    # 笔记/球星卡「学习」页（开局装备的笔记触发，不可跳过动画 + 右下「学习」）。
    # 必须排在 handle_street / handle_event_choice 之前：这页带「评分」，
    # 旧版被 handle_street 当成扭蛋详情页点了右上角「返回」（2026-11-02 01:22）。
    TrainingHandlers.handle_note_study,
    MatchHandlers.handle_match_result,
    MatchHandlers.handle_skill_select,
    # 佐久间雨乃的天赋「钓鱼」：钓鱼场是三选一、带「确定」，
    # 必须排在通用「确定/下一步」类 handler 之前（旧版落到 unknown 兜底，连点 3 次卡死）。
    FishingHandlers.handle_fishing_ground,
    # 转盘类事件（钓鱼啦！/ 开始做饭！……）：**一个 handler 共用**。
    # 用户 2026-11-03 口径：转盘内容随机、不影响决策；「转一转」按钮的位置和文案
    # 在所有转盘页都是通用的（两页实测按钮框都是 715,781,111,47）。
    # 判据卡「底部居中」那一条，所以大厅「玩法目录」里那个「转一转」入口不会误伤。
    WheelHandlers.handle_spin_wheel,
    TrainingHandlers.handle_boost_training,
    # SSR 角色天赋触发的「请选择一个题材」三选一（2026-11-02）：
    # 页面右上角有「跳过」，必须排在 handle_dialog_continue / handle_story_arrow 之前，
    # 否则又变成「点快进图标，什么都不推进」（同炒股页那次事故）。
    ThemeHandlers.handle_theme_select,
    # 同一事件的收尾页「WARD文档」里的「交稿」：也带「跳过」快进图标，
    # 同样必须排在 handle_dialog_continue / handle_story_arrow 之前。
    ThemeHandlers.handle_manuscript_submit,
    # 角色天赋特殊事件「做饭」的食材三选一（2026-11-03 实机）：标题条按品质上色，
    # 按 彩>金>紫>蓝 → 队友多 选一张再点「确定」。必须排在 handle_event_choice
    # / 通用「确定」类 handler 之前（旧版没人认领，一直落 unknown 点屏幕中央）。
    CookingHandlers.handle_ingredient_select,
    TrainingHandlers.handle_subject_select,
    MatchHandlers.handle_event_choice,
    # 比赛日进店必须排在「赛前准备」之前：比赛日主界面可能直接就是
    # 「跳过比赛/前往比赛」，否则没机会点商店（2026-10-29 第 5/6 周漏进）
    TrainingHandlers.handle_pre_match_shop,
    MatchHandlers.handle_prepare,
    # 炒股页（天赋「股神!」）：必须在 handle_dialog_continue 之前。
    # 2026-10-30 事故：股票走势面板右上角有「跳过」（快进图标），
    # handle_dialog_continue 先命中就反复点它，什么都推进不了，
    # 3 轮「点不动」后任务直接停。炒股页自己的「卖出/下一步」永远轮不到。
    WeekendHandlers.handle_stock_pick,
    WeekendHandlers.handle_stock_result,
    EntryHandlers.handle_dialog_continue,
    EntryHandlers.handle_lobby,
    EntryHandlers.handle_training_entry,
    EntryHandlers.handle_captain,
    EntryHandlers.handle_team,
    EntryHandlers.handle_note_select,
    EntryHandlers.handle_equipment,
    EntryHandlers.handle_confirm_team,
    # 2026-11-01：识别队长/流派改成**主动**做（daycycle._resolve_style_step）：
    # 接手别人的局时点返回回到「带日历的页面」→ 点日历 → 抓队长名字 →
    # 查表定流派；认不出就弹窗「未能识别到队长」，**不许保底**。
    # 原来的 handle_style_probe 只在训练日触发（要求「星期」+「休息」），
    # 接手的局在周末永远点不了日历 → 扭蛋流跑成训练流（2026-11-01 19:19 事故）。
    TrainingHandlers.handle_training_day,
    TrainingHandlers.handle_training_hub,
    WeekendHandlers.handle_activity_go,
    WeekendHandlers.handle_restaurant,
    WeekendHandlers.handle_park,
    WeekendHandlers.handle_street,
    WeekendHandlers.handle_arcade,
    WeekendHandlers.handle_shop,
    WeekendHandlers.handle_go_out,
    WeekendHandlers.handle_weekend,
    TrainingHandlers.handle_vacation,
    MatchHandlers.handle_tap_to_continue,
    MatchHandlers.handle_result,
    EntryHandlers.handle_club_members,
    EntryHandlers.handle_story_arrow,
    # ---- 兜底：**没登记的品质卡页面**（彩>金>紫>蓝 → 队友多）+ logs/unmark 存证 ----
    # 必须在整张表的最后：任何专属 handler 认领过的页面都轮不到它。
    # 用户 2026-11-03 口径：不认识的事件页也不能只点屏幕中央，按品质猜一张、
    # 把这一帧存进 logs/unmark/，人工看过就能补成正式 handler。
    UnmarkedHandlers.handle_unmarked_cards,
)

# handler 的**瞬时状态**声明：谁的状态谁声明。
# 命中别的 handler 时，这些字段自动清零（flow._reset_handler_leave_states）。
#
# 为什么要有这张表：以前的写法是在 flow._handle_any 里
#     if name != "handle_prepare": self._skip_tries = 0
# 用 handler 名字**字符串**表达「handler 之间有依赖」，于是每加一个 handler
# 都要回来改一次 flow（2026-11-01 重构前的三处：handle_prepare / handle_in_match /
# handle_subject_select）。现在依赖只存在这一张表里，flow 不认识任何 handler 名字；
# tools/check_handlers.py 会禁止 src/ 其它地方再出现 handler 名字字符串。
#
# 值可以是常量，也可以是工厂函数（可变默认值必须用 lambda，否则所有实例共享同一个 dict）。
LEAVE_RESETS = {
    # 跳过比赛只试一次：换个界面就重来
    "handle_prepare": {"_skip_tries": 0},
    # 比赛内的「手动修正/快进」标记：离开比赛界面就清空
    "handle_in_match": {
        "_match_flags": lambda: {"manual_fixed": False, "fast_clicked": False,
                                 "last_fast_at": 0.0},
    },
    # 训练科目页的选择缓存：离开科目页就清空
    "handle_subject_select": {"_picked_subject": False, "_picked_index": 0},
    # 训练日主界面的「第几拍」和「等出行按钮」计数：离开这一页就清零。
    # `_hub_ticks` 是「头一拍不决策」的依据（见 HUB_SETTLE_TICKS），
    # 不清就会把第二天的页面当成「已经看过好几拍」→ 又回到淡入那一拍就决策。
    # `_lounge_snapshot_done`：进休息室的六维/精力/零用钱快照，**每次进这一页只拍一次**
    # （2026-11-03 用户口径），所以离开时必须清掉，第二天才会重新拍。
    "handle_training_day": {"_hub_ticks": 0, "_outing_wait": 0,
                            "_lounge_snapshot_done": False},
    # 笔记学习页的等待/点击计数：离开这页就清零（下次遇到重新等、重新点）
    "handle_note_study": {"_note_study_wait": 0, "_note_study_clicks": 0},
    # 题材三选一的「已点确定」时间戳：离开这页就清掉，下次重新判颜色
    "handle_theme_select": {"_theme_confirmed_at": 0.0},
    # 「WARD文档」交稿的点击锁：离开这页就清掉
    "handle_manuscript_submit": {"_submit_confirmed_at": 0.0},
    # 「做饭」食材三选一：已点确定的时间戳 + 存证限流（离开这页就清）
    "handle_ingredient_select": {"_cook_confirmed_at": 0.0, "_cook_dumped_at": 0.0},
    # 转盘的点击锁：离开这一页就清（下次遇到重新计时）
    "handle_spin_wheel": {"_wheel_clicked_at": 0.0},
    # 未登记品质卡页面的存证限流 / 确定锁：换界面就清
    "handle_unmarked_cards": {"_unmark_dumped_at": 0.0, "_unmark_confirmed_at": 0.0},
    # 加强训练页的「第几拍 / 已点确定的时刻」：离开这页就清零，
    # 否则下一张加强训练卡会被当成「已经看过好几拍」→ 又回到淡入那一拍就点（空点击）
    "handle_boost_training": {"_boost_ticks": 0, "_boost_confirmed_at": 0.0},
    # 训练结果浮层的点击锁：离开就清，免得下次这页第一拍被误判成「刚点过」
    "handle_training_result": {"_result_click_at": 0.0},
}
