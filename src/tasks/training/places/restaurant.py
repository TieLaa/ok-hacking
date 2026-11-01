"""餐厅（打工）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：
每个地点有自己的模块，只管自己的判断（内页判据 + 做什么 + 自己的状态）。

餐厅的规则（用户 2026-10-29 口径，**通用**，不分流派）：
1. 队友多的优先 —— 卡片**左上角**挂着一排队友小头像，数头像个数；
2. 队友一样多 → 选**右边**那张（消耗精力的）；
3. 精力不够右边那张 → 改选**左边**（不消耗精力）。

2026-10-31 补两条：
- 精力取**整帧 OCR**（`top_bar(texts)`）。裁剪顶栏重 OCR 会丢斜杠，
  stamina 恒为 None，这条规则等于没实现（餐厅实跑：23/130 仍去点 25 的卡）；
- `blocked_activities()`：详情弹窗说过「精力不足」或「前往」点不动的卡，
  今天不再选。
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

    # ---------- 页面判据 ----------

    def restaurant_page(self, texts):
        """这是不是餐厅内页？"""
        return self.match_text(texts, labels.RESTAURANT_TITLE) is not None

    # ---------- 选哪张卡（纯判断） ----------

    def restaurant_order(self, texts):
        """打工两张卡的点击顺序：**能选的优先** → 队友多 → 右边（耗精力）。

        返回选项名的元组（空的元组 = 两张都点不动）。

        - 先看这张卡现在**能不能选**（`option_selectable`）：界面提示不可用、
          或精力不够它标的消耗 → 不可选；
        - 可选的两张里：队友多（卡片左上角的小头像数）优先，一样多选右边；
        - 都不可选就按「右边优先」原样返回，让游戏自己拦（下一拍会重判）。
        右边那张默认按 `WORK_STAMINA_COST`（25）算消耗、左边按 0（不消耗），
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
        scored = [(self.mates_on_card(b, icons), b) for b in found]
        scored.sort(key=lambda pair: (-pair[0], -pair[1].x))   # 队友多 → 右边
        ranked = [b for _n, b in scored]
        left = [b for b in ranked if b.name.strip() not in blocked]
        if not left:
            self.log_warning(f"餐厅：{sorted(blocked)} 今天都点不动，没有可选的卡")
            return ()
        right = max(found, key=lambda b: b.x)
        usable = [b for b in left
                  if self.option_selectable(
                      b, texts, stamina,
                      WORK_STAMINA_COST if b is right else 0)]
        detail = "、".join(
            f"{b.name}={n}人{'' if b in usable else '（不可选）'}" for n, b in scored)
        self.log_info(f"餐厅：{detail}｜精力 {stamina}"
                      f"｜今天点不动的 {sorted(blocked) or '无'} → "
                      + ("按可选/队友多/右边选" if usable else "都不可选，按右边优先试"))
        return tuple(b.name for b in (usable or left))

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
