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

from src.tasks.ui import labels

# 页面 ID → 优先级（数字越小越先；和 HANDLERS 的顺序保持同一口径）
PRIORITY = {
    "game_title": 10,
    "construction": 20,
    "register_player": 30,
    "training_subject": 40,
    "event_choice": 50,
    "weekend_map": 60,
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


def _is_event_choice(task, texts):
    """事件选项：至少两个成行的黄色选项按钮，且不是邻居页面。

    这些排除项都是**页面级强特征**，不是正文词：
    科目页有「失败率」、结算页有「下一步」、首页/内页有「出行」、
    地图有 ≥2 个整框地点名、赛程页有「继续匹配 / 暂时退出」。
    """
    if len(_choice_buttons(task, texts)) < 2:
        return False
    if task.match_text(texts, "失败率") is not None:
        return False
    if task.match_text(texts, "下一步") is not None:
        return False
    if task.match_text(texts, "出行") is not None:
        return False
    if task.match_text(texts, ("继续匹配", "暂时退出")) is not None:
        return False
    if len(_short_place_names(texts)) >= 2:
        return False
    return True


def _is_weekend_map(task, texts):
    if task.match_text(texts, "出行") is not None:
        return False
    return len(_short_place_names(texts)) >= 2


_ANCHORS = {
    "game_title": _is_game_title,
    "construction": _is_construction,
    "register_player": _is_register_player,
    "training_subject": _is_training_subject,
    "event_choice": _is_event_choice,
    "weekend_map": _is_weekend_map,
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
