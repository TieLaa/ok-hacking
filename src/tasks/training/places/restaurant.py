"""餐厅（打工）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：
每个地点有自己的模块，只管自己的判断（内页判据 + 做什么 + 自己的状态）。

餐厅的规则（用户口径，**通用**，不分流派）：
1. 默认选**当服务员**（右边、耗精力、零用钱++）；
2. **桌游GM**（左边、不耗精力）的队友比当服务员**多两个及以上** → 优先桌游GM
   （2026-11-03 用户口径；差 1 个仍然选当服务员）；
3. 体力/精力不够那张 → 改选另一张（`option_selectable`）。

2026-10-31 补两条：
- 精力取**整帧 OCR**（`top_bar(texts)`）。裁剪顶栏重 OCR 会丢斜杠，
  stamina 恒为 None，这条规则等于没实现（餐厅实跑：23/130 仍去点 25 的卡）；
- `blocked_activities()`：详情弹窗说过「精力不足」或「前往」点不动的卡，
  今天不再选。
- 队友个数按**脸**数（`parsers.mate_face_count`，2026-11-03）：现版本的队友条是
  「深蓝胶囊 + chibi 头像」，旧兜底只能给「有/没有」，2 人 vs 1 人被读成平局。
"""

import re

from src.tasks.training.constants import WORK_STAMINA_COST
from src.tasks.ui import labels

# 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "餐厅"

# 退出餐厅的兜底坐标（右上角返回）。通用层不认识这一页长什么样。
EXIT_COORDS = (0.945, 0.040)


class RestaurantPlace:
    """餐厅的判断与状态。**不点击**，只给判断。"""

    # 桌游GM 比当服务员多几个人才改选左边（用户 2026-11-03 口径：多两个）。
    MATE_MARGIN = 2

    # ---------- 页面判据 ----------

    def restaurant_page(self, texts):
        """这是不是餐厅内页？"""
        return self.match_text(texts, labels.RESTAURANT_TITLE) is not None

    # ---------- 选哪张卡（纯判断） ----------

    def restaurant_order(self, texts):
        """打工两张卡的点击顺序：**能选的优先** → 默认当服务员 → 桌游GM 多 2 人才改。

        返回选项名的元组（空的元组 = 两张都点不动）。

        - 先看这张卡现在**能不能选**（`option_selectable`）：界面提示不可用、
          或精力不够它标的消耗 → 不可选；
        - 两张都可选时：默认选**当服务员**（右边、零用钱++）；只有**桌游GM**
          的队友比当服务员多 `MATE_MARGIN`（2）个及以上时，才改成桌游GM 优先
          （用户 2026-11-03 口径，差 1 个仍然选当服务员）；
        - 都不可选就按上面的默认顺序原样返回，让游戏自己拦（下一拍会重判）。
        当服务员默认按 `WORK_STAMINA_COST`（25）算消耗、桌游GM 按 0（不耗精力），
        卡片上写明消耗时以卡片为准。
        """
        opts = self.texts_in_region(texts, "weekend_options")
        found = []
        for name in labels.RESTAURANT_OPTIONS:
            box = self.exact_text(opts, (name,)) or self.match_text(opts, (name,))
            if box is not None:
                found.append(box)
        if not found:
            return labels.RESTAURANT_OPTIONS

        stamina = self.top_bar(texts).get("stamina")
        blocked = self.blocked_activities()
        icons = self.find_teammate_icons(0.0, 1.0)
        counts = [self.card_mate_count(b, icons) for b in found]
        ranked, why = self.restaurant_preference(found, counts)
        free = [b for b in ranked if b.name.strip() not in blocked]
        if not free:
            self.log_warning(f"餐厅：{sorted(blocked)} 今天都点不动，没有可选的卡")
            return ()
        usable = [b for b in free
                  if self.option_selectable(
                      b, texts, stamina, self.restaurant_card_cost(b, found))]
        detail = "、".join(
            f"{b.name}={counts[found.index(b)]}人"
            f"{'' if b in usable else '（不可选）'}"
            for b in ranked)
        if not usable:
            tail = "都不可选，按同一顺序试"
        elif usable[0] is ranked[0]:
            tail = f"→ {usable[0].name}"
        else:
            tail = f"→ {ranked[0].name}不可选，改 {usable[0].name}"
        self.log_info(f"餐厅：{detail}｜精力 {stamina}"
                      f"｜今天点不动的 {sorted(blocked) or '无'}"
                      f"｜{why} {tail}")
        return tuple(b.name for b in (usable or free))

    def restaurant_preference(self, found, counts):
        """两张卡的优先顺序（`counts` 与 `found` 同序）→ (顺序, 选它的一句话原因)。

        用户 2026-11-03 口径：**默认当服务员**；桌游GM 的队友比当服务员多
        `MATE_MARGIN` 个及以上才把桌游GM 排到前面（「左边的队友比右边的多两个
        优先选左边」）。名字认不出时按位置理解：右边 = 当服务员、左边 = 桌游GM。
        """
        servant = next((b for b in found if "当服务员" in b.name), None)
        gm = next((b for b in found if "桌游GM" in b.name), None)
        if servant is None or gm is None:
            servant = max(found, key=lambda b: b.x)     # 右边默认当服务员
            gm = min(found, key=lambda b: b.x)
        if gm is None or servant is None or gm is servant:
            return list(found), "只有一张卡"
        diff = counts[found.index(gm)] - counts[found.index(servant)]
        if diff >= self.MATE_MARGIN:
            return ([gm] + [b for b in found if b is not gm],
                    f"桌游GM 队友多 {diff} 人")
        return ([servant] + [b for b in found if b is not servant],
                "队友一样多" if diff == 0 else f"桌游GM 只多 {diff} 人，不够 {self.MATE_MARGIN} 人")

    def restaurant_card_cost(self, box, found):
        """这张卡没写明消耗时的默认精力消耗：当服务员耗、桌游GM 不耗。"""
        if "桌游GM" in box.name:
            return 0
        if "当服务员" in box.name:
            return WORK_STAMINA_COST
        right = max(found, key=lambda b: b.x)
        return WORK_STAMINA_COST if box is right else 0

    def card_mate_count(self, box, icons):
        """这张卡挂了几个队友头像：先数脸（现版本胶囊），数不到再用旧的「有/没有」判据。"""
        n = self.mate_face_count(box)
        if not n:
            n = self.mates_on_card(box, icons)
        return n

    def option_selectable(self, box, texts, stamina=None, default_cost=0):
        """这张打工卡现在能不能选。

        两个判据都来自界面，而不是硬写一个精力门槛：
        1. 卡片附近写明「精力不足 / 无法选择 / 不可」→ 不可选；
        2. 卡片自己标了消耗（`精力-30`）时，当前精力不够它 → 不可选；
           卡片没写消耗时按 `default_cost` 估（右边 = `WORK_STAMINA_COST` 25，
           左边 = 0，因为不耗精力的是左边那张）。
        """
        if box is None:
            return False
        for t in texts:
            s = t.name.replace(" ", "")
            if not any(k in s for k in ("不足", "不可", "无法")):
                continue
            if abs((t.y + t.height / 2) - (box.y + box.height / 2)) < box.height * 2.5:
                return False
        cost = self.card_stamina_cost(box, texts)
        if cost is None:
            cost = default_cost
        return stamina is None or stamina >= cost

    def card_stamina_cost(self, box, texts):
        """卡片上写的精力消耗（「精力-20」/「消耗20精力」），读不到返回 None。

        「精力+30」「精力恢复20」这类是**收益**不是消耗，必须排除 ——
        否则会给一张回精力的卡判出「要花 30 精力」，把好选项挡掉。
        """
        if box is None:
            return None
        best = None
        for t in texts:
            s = t.name.replace(" ", "")
            if "精力" not in s:
                continue
            if any(k in s for k in labels.GACHA_CLEAR_EXCLUDE) or "+" in s:
                continue
            if abs((t.y + t.height / 2) - (box.y + box.height / 2)) > box.height * 2.5:
                continue
            if abs((t.x + t.width / 2) - (box.x + box.width / 2)) > box.width * 1.2:
                continue
            m = (re.search(r"精力\D{0,3}(\d{1,3})", s)
                 or re.search(r"(\d{1,3})\D{0,3}精力", s))
            if m:
                best = int(m.group(1))
        return best
