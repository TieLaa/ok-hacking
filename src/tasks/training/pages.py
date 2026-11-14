"""页面分类（Page Registry）——**旁路阶段**：只认页面，不点击、不改控制流。

为什么要有这一层（2026-11-13 全局方案）：
    现在 30+ 个 handler 各自在内部判断「这是不是我的页面」，判据散落、
    互不知道对方的边界；冲突只能靠 `HANDLERS` 顺序和一条条排除条件消化。
    结果是每撞一次事故就补一条排除（2026-11-13「在公园」正文让事件页
    被跳过、又被当成周末地图，就是典型）。

    这里先把「页面 → 锚点」集中到一张表，**旁路运行**：
    每拍算一个 PageID 写日志，和实际命中的 handler 对照。
    等差异清零，再把 `_handle_any` 切到「先分类、再分派」。

判据约定（和 docs/ENGINEERING.md §3.1 一致）：
- 锚点只用 **UI 词/形态**（整框按钮、黄色边框、整框地点名），
  正文长句不参与判页。
- 一个页面至少有一个独有锚点；没有独有锚点时先不登记。
- 多个页面同时命中时按 `PRIORITY` 取第一个，但把 candidates 一起返回——
  旁路日志会把它打出来，这就是「判据重叠」的可观测入口。

只登记了首批高频/出过事的页面；其余页面返回 None，仍走现有 handler。
"""

from src.tasks.ui import Defs, labels

# 页面 ID → 优先级（数字越小越先；和 HANDLERS 的顺序保持同一口径）
PRIORITY = {
    "game_title": 10,
    "construction": 20,
    "register_player": 30,
    "result": 35,
    "training_subject": 40,
    "event_choice": 50,
    "weekend_map": 60,
    "restaurant": 70,
    "street": 80,
    "park": 90,
    "shop": 100,
    "story_arrow": 120,
}


def _short_place_names(texts):
    """地图上的整框地点名（允许「公园+」这种带加号的短标签）。"""
    return [t for t in texts
            if len(t.name.strip()) <= 4
            and t.name.strip().rstrip("+") in labels.WEEKEND_PLACES]


# ---------- 每个页面的锚点 ----------

def _is_game_title(task, texts):
    if task.exact_text(texts, (labels.GAME_TITLE_ENTER,)) is None:
        return False
    return task.match_text(texts, labels.GAME_TITLE_MARKS) is not None


def _is_construction(task, texts):
    return (task.match_text(texts, labels.CONSTRUCTION_TITLE) is not None
            and task.exact_text(texts, (labels.CONSTRUCTION_START,)) is not None)


def _is_register_player(task, texts):
    """注册球员页：标题「注册球员」+ 流程词（下一步 / 记录笔记 / 编队 / 保存调整）。

    注意：结算页（恭喜获得 / 训练奖励）**也算**这一页 ——
    `handlers/match.py: handle_register_player` 在这一页上读五个位置的评分
    （达标判据），所以它优先于 `handle_result`。分类器要和 handler 一致，
    否则阶段 2 切换分派时会改变行为。
    """
    if task.match_text(texts, "注册球员") is None:
        return False
    return task.match_text(texts, ("下一步", "记录笔记", "编队", "保存调整")) is not None


def _is_training_subject(task, texts):
    if task.match_text(texts, "失败率") is None:
        return False
    return task.exact_text(texts, labels.BUTTONS["decide"]) is not None


def _choice_buttons(task, texts):
    """黄色选项按钮：成行、尺寸相近、框内有非数字名字。

    和 `handlers/match.py: handle_event_choice` 的黄框分支同源（阶段 2 合并）。
    只看黄框数量不够：扭蛋详情页的「100」价格也是黄框，训练剧本页的
    「东部大赛」标题卡 + 「启程」按钮也各是一个黄框（2026-11-13 回归集误判）。
    """
    buttons = task._yellow_buttons()
    if len(buttons) < 2:
        return []

    def center(b):
        return b[0] + b[2] / 2, b[1] + b[3] / 2

    def similar(a, b):
        return (max(a[2], b[2]) / max(min(a[2], b[2]), 1) < 2.6
                and max(a[3], b[3]) / max(min(a[3], b[3]), 1) < 2.2)

    row = []
    for b in sorted(buttons, key=lambda b: b[1]):
        group = [b]
        for c in buttons:
            if c is b:
                continue
            if (abs(center(b)[1] - center(c)[1]) < task.height * 0.05
                    and similar(b, c)):
                group.append(c)
        if len(group) > len(row):
            row = group
    if len(row) < 2:
        return []

    names = []
    for bx, by, bw, bh in row:
        inside = [t for t in texts
                  if bx <= (t.x + t.width / 2) <= bx + bw
                  and by <= (t.y + t.height / 2) <= by + bh]
        cand = [t for t in inside
                if 2 <= len(t.name.strip()) <= 12
                and not t.name.strip().isdigit()
                and t.name.strip() not in ("确定", "取消", "关闭", "下一步")]
        if cand:
            names.append(max(cand, key=lambda t: t.height))
    return names if len(names) >= 2 else []


def _has_yellow_choices(task, texts):
    return len(_choice_buttons(task, texts)) >= 2


def _has_centered_choices(task, texts):
    """居中竖排的选项组（`choice_candidates` 一列，中心 0.38~0.62）。

    训练动画页的「训练加成/表情加成」是横向三张，分组后每组只有 1 个；
    周结算页的「控卫/分卫…」是贴边一列，中心 0.26 —— 都被这条排除。

    还要排除「地点内页的属性面板」：公园/餐厅页底部那六行属性
    （投射/弹跳/防守…）也是居中一列的短词，但不是选项。
    """
    group = task.choice_candidates(texts)
    if len(group) < 2 or len(group) > 3:
        return False
    centers = [(t.x + t.width / 2) / task.width for t in group]
    if max(centers) - min(centers) > 0.06:
        return False
    if not 0.38 <= sum(centers) / len(centers) <= 0.62:
        return False
    # 属性面板：六维名 + 数值（分开两个框，数值那列更靠右）。
    if any(any(a in t.name for a in labels.TRAINING_SUBJECTS) for t in group):
        return False
    return True


def _is_event_choice(task, texts):
    """事件选项：**成行的黄色选项按钮**（≥2 个），且不是邻居页面。

    2026-11-14 实测：竖排分支（`choice_candidates` 居中一列）会和
    公园/餐厅的属性面板、选股页的卡片列重叠 —— 同一形态在多个页面出现。
    阶段 1 只保留**最可靠**的黄框形态，竖排事件页等阶段 2 再用
    「页面级标题」单独登记。

    排除项都是**页面级强特征**，不是正文词：
    科目页有「失败率」、结算页有「下一步」、首页/内页有「出行」、
    地图有 ≥2 个整框地点名、赛程页有「继续匹配 / 暂时退出」、
    选股页有「请选择你要投资的股票」。
    """
    if not (_has_yellow_choices(task, texts)
            or _has_centered_choices(task, texts)
            or len(task.options_not_paragraph(texts)) >= 2):
        return False
    if task.match_text(texts, "失败率") is not None:
        return False
    if task.match_text(texts, "下一步") is not None:
        return False
    if task.match_text(texts, "出行") is not None:
        return False
    if task.match_text(texts, ("继续匹配", "暂时退出")) is not None:
        return False
    if task.match_text(texts, labels.STOCK_PICK_TITLE) is not None:
        return False
    if len(_short_place_names(texts)) >= 2:
        return False
    # 地点内页的底部按钮（野餐/划船、桌游GM/当服务员）也是居中一列短词，
    # 形态和竖排事件选项一样 —— 用**页面标题**区分（2026-11-14 实测）。
    if _is_park(task, texts) or _is_restaurant(task, texts):
        return False
    return True


def _is_weekend_map(task, texts):
    if task.match_text(texts, "出行") is not None:
        return False
    return len(_short_place_names(texts)) >= 2


def _is_restaurant(task, texts):
    """餐厅内页：标题是「在餐厅 / 桌游GM / 当服务员」之一。

    但**详情页**（点开某个打工项）也算餐厅流程 —— 那页有「前往当服务员」，
    同样属于餐厅 handler 的工作范围。
    """
    if task.exact_text(texts, labels.RESTAURANT_TITLE) is not None:
        return True
    return task.match_text(texts, ("前往当服务员", "前往桌游GM")) is not None


def _is_street(task, texts):
    """步行街扭蛋页：标题含「请选择1个扭蛋」或「扭蛋」。

    要排除周末地图（地图上有地点名、也有「扭蛋增强」标签）。
    """
    if task.match_text(texts, labels.STREET_TITLE) is None:
        return False
    return len(_short_place_names(texts)) < 2


def _is_park(task, texts):
    """公园内页：标题含「在公园 / 干点什么好呢 / 野餐」。

    必须**整框**：事件正文里的「在公园打街球」（2026-11-13 事故）
    会命中包含匹配；整框 + 短标签才不会。
    """
    if task.match_text(texts, "出行") is not None:
        return False
    if len(_short_place_names(texts)) >= 2:
        return False
    for name in labels.PARK_TITLE:
        if task.exact_text(texts, (name,)) is not None:
            return True
    return False


def _is_shop(task, texts):
    """商店内页：有「商店」且有「上新时间 / 剩余 / 能力详情」之一。"""
    if task.match_text(texts, "出行") is not None:
        return False
    if task.match_text(texts, "商店") is None:
        return False
    return task.match_text(texts, ("上新时间", "剩余", "能力详情")) is not None


def _is_result(task, texts):
    """结算页：标题含「恭喜获得 / 训练奖励 / 训练评分」等。"""
    if task.match_text(texts, labels.SQUAD_AWARD_MARKS) is not None:
        return True
    return task.match_text(texts, labels.TRAINING_SCORE_MARKS) is not None


def _is_story_arrow(task, texts):
    """剧情/事件推进页：底部台词条有 12 字以上长中文，且右下角有白色 ▼。

    和 `handlers/entry.py: handle_story_arrow` 同源：光靠像素的 ▼ 太宽
    （几乎每页右下角都有白像素，实测会把 11 张回归图误判成剧情页），
    必须再叠一条「底部台词条里有长中文」。
    """
    if task.match_text(texts, ("失败率", "决定", "休息", "赢得比赛", "VS")) is not None:
        return False
    if len(_short_place_names(texts)) >= 2:
        return False
    # 有其它页面的强特征就不是剧情页
    if task.exact_text(texts, labels.RESTAURANT_TITLE) is not None:
        return False
    if task.match_text(texts, labels.STREET_TITLE) is not None:
        return False
    if task.match_text(texts, "商店") is not None:
        return False
    if task.match_text(texts, "训练剧本") is not None:
        return False
    if not task.has_story_arrow():
        return False
    x1, y1, x2, y2 = Defs.region("dialog_text")
    for t in texts:
        cx = (t.x + t.width / 2) / task.width
        cy = (t.y + t.height / 2) / task.height
        if not (x1 <= cx <= x2 and y1 <= cy <= y2):
            continue
        s = t.name.strip()
        if len(s) < 12:
            continue
        if any(ch.isdigit() for ch in s) or "/" in s:
            continue
        if any("\u4e00" <= ch <= "\u9fff" for ch in s):
            return True
    return False


_ANCHORS = {
    "game_title": _is_game_title,
    "construction": _is_construction,
    "register_player": _is_register_player,
    "training_subject": _is_training_subject,
    "event_choice": _is_event_choice,
    "weekend_map": _is_weekend_map,
    "restaurant": _is_restaurant,
    "street": _is_street,
    "park": _is_park,
    "shop": _is_shop,
    "result": _is_result,
    "story_arrow": _is_story_arrow,
}


def classify_page(task, texts):
    """返回 `(page_id, candidates)`。

    `page_id` 是优先级最高的命中页；没有命中时是 None。
    `candidates` 是**所有**命中的页面（按优先级），用来发现判据重叠。
    """
    hits = [name for name, check in _ANCHORS.items() if check(task, texts)]
    if not hits:
        return None, ()
    hits.sort(key=lambda name: PRIORITY[name])
    return hits[0], tuple(hits)
