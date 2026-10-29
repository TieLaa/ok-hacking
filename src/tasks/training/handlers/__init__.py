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
    MatchHandlers.handle_match_result,
    MatchHandlers.handle_skill_select,
    TrainingHandlers.handle_boost_training,
    TrainingHandlers.handle_subject_select,
    MatchHandlers.handle_event_choice,
    MatchHandlers.handle_prepare,
    EntryHandlers.handle_dialog_continue,
    EntryHandlers.handle_lobby,
    EntryHandlers.handle_training_entry,
    EntryHandlers.handle_captain,
    EntryHandlers.handle_team,
    EntryHandlers.handle_equipment,
    EntryHandlers.handle_confirm_team,
    # 流派未知时先开日历认队长（用户 2026-10-29）：必须排在训练日主界面之前，
    # 否则第一拍就点「训练/休息」，流派还没定下来。
    TrainingHandlers.handle_style_probe,
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
)
