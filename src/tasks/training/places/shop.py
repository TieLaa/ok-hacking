"""商店（买器材/手册）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：
每个地点有自己的模块，只管自己的判断。

界面（实机 2026-10-26）：
- 左侧商品卡：名称 + 剩余数量 + 价格（100 / 60 / 45），**卡片底色 = 品质色**；
- 右侧是选中商品的详情和**黄色购买条**（价格就在条上）；
- 右上角 X 关闭；顶栏金币是专注条右边那个独立数字。

用户口径（2026-10-26）：
- 平时只买金卡和紫卡（蓝/白不买）；
- **不清空**（2026-11-05 用户口径：比赛日也不清空，不再有「赛前把钱花光」这条）。

所以这里一拍只买**一件**：买完不关店，下一拍重新 OCR 再决定；
按策略买不动了 / 钱花光了才关店。

2026-11-05 用户口径：进了店就按「商店购买策略」正常顺序买，**不清空**。

2026-11-11 用户口径（**取代**「第 7 周前只买 0 元」和「彩金紫」两套旧规则）：
- 商店内顺序**对所有流派相同**：现金 → 牛奶 → 精力 → 教科书 → 器材 → 属性
  （`constants.SHOP_PRIORITY`）。流派只决定「进不进店、带多少钱」，不决定买什么；
- **训练流 / 商店流**：进店就按顺序买，不设周数限制；
- **扭蛋流 / 小游戏流**：前 6 周零花钱留给周末的步行街/游戏厅，只拿必拿；
- 现金类（老板的馈赠 / 大乐透）永远最先，纯赚档不看余额；
- 苦味/怪味、神之庇佑、同心护符：当前策略下不买；
- 精力商品仍过三类门槛（见 `shop_stamina_gate`）。
"""

from src.tasks.ui import labels
from src.tasks.training.constants import (LATE_SHOP_FROM_WEEK, SHOP_ITEMS,
                                          SHOP_ITEM_PREFIX,
                                          SHOP_NEVER_BUY, SHOP_PRIORITY,
                                          SHOP_SEASON_WEEKS, SHOP_STAMINA_ITEMS,
                                          SHOP_TRAINING_WEEKDAYS)

# 星期 → 数字（用来算剩余训练日）
WEEKDAY_INDEX = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                 "六": 6, "日": 7, "天": 7}

# 时限商品至少还要有这么多训练日才值得买（兑现不了就不买）
SHOP_MIN_DAYS_LEFT = {"milk": 3, "textbook": 2, "equipment_boost": 2}

# 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "商店"

# 关店的兜底坐标（右上角 X）。实测帧里 OCR 的 X 框中心偏下约 8% 框高，
# 所以先走文字框、按坐标兜底时用 y=0.11。
EXIT_COORDS = (0.94, 0.11)


class ShopPlace:
    """商店的判断与状态。**不点击**，只给判断。"""

    # ---------- 页面判据 ----------

    def shop_page(self, texts):
        """这是不是商店内页？

        周末首页也有「商店」两个字，但同时有「出行」—— 那是按钮不是商店页。
        还要有「上新时间/剩余/能力详情」之一才认（别把别的页面的「商店」当商店）。
        """
        if self.match_text(texts, "出行") is not None:
            return False
        if self.match_text(texts, "商店") is None:
            return False
        return self.match_text(texts, ("上新时间", "剩余", "能力详情")) is not None

    # ---------- 「新品上市」提示：第 7 周以后要不要进店 ----------

    def _has_fresh_mark(self, name):
        """这段文字是不是商店入口上方的「上新」标签？"""
        name = (name or "").strip()
        if not name or any(x in name for x in labels.SHOP_FRESH_EXCLUDE):
            return False
        return any(x in name for x in labels.SHOP_FRESH_MARKS)

    def shop_fresh_badge(self, texts, shop_box):
        """商店按钮**头顶**有没有「新品上市」提示？返回 (提示框, 原因)。

        实测（用户 2026-11-05 截图，第 3 周 星期一 比赛日主界面，1552×871）：
        橙色标签「新品上市」中心 ≈(0.920, 0.156)，「商店」按钮中心 ≈(0.920, 0.230)
        —— 同一列、按钮上方约 0.07 屏高。

        判据**连位置一起判**（沿用「扭蛋增强！」的教训：全屏出现同样字眼就生效，
        别处台词一提「上新」就会误进店）：
        - 提示中心必须落在按钮**正上方**：横向对齐（|Δcx| ≤ 0.15）、纵向在按钮上方
          一小段（Δcy ∈ [0.01, 0.15]，Δcy 为负 = 在下面）；
        - 提示和按钮被 OCR 读成一整块（框名里就带「新品上市」）也算 —— 那种情况
          中心会和按钮重合，位置判据判不了，按文字认；
        - 长度限制在 2~8 字，别把正文当标签。
        """
        if not texts:
            return None, "没读到文字"
        # 提示和按钮被 OCR 读成一整块（比如整个框叫「新品上市 商店」）
        if self._has_fresh_mark(getattr(shop_box, "name", "")):
            return shop_box, "按钮框本身就是「%s」" % shop_box.name.strip()
        bx = (shop_box.x + shop_box.width / 2) / self.width
        by = (shop_box.y + shop_box.height / 2) / self.height
        best, best_dy, best_dx = None, None, None
        for t in texts:
            name = t.name.strip()
            if not self._has_fresh_mark(name):
                continue
            if not (2 <= len(name) <= 8):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            dx = abs(cx - bx)
            dy = by - cy                    # > 0 = 在按钮上方
            if dx > 0.15:
                continue                    # 不在按钮这一列（左边/右边/正文）
            if dy < 0.01 or dy > 0.15:
                continue                    # 同一行（dy≈0）或离太远都算「没贴在头上」
            if best is None or (dy, dx) < (best_dy, best_dx):
                best, best_dy, best_dx = t, dy, dx
        if best is None:
            return None, "商店按钮上方没有「新品上市」提示"
        return best, "「%s」在商店按钮上方" % best.name.strip()

    # ---------- 买哪件（纯判断） ----------

    def shop_items(self, texts):
        """读出商品列表（`read_shop_items` 的封装）。"""
        return self.read_shop_items(texts)

    def shop_stamina_gate(self, item, stamina, stamina_max):
        """这件恢复精力商品过不过三类门槛？返回拒因字符串，None = 能买。

        2026-11-08 用户口径（商店版，数据来自登记表；扭蛋侧同一套口径在
        `places/street.py: recover_rejected`，只是数据来源是卡面效果文本）：
          ① 立刻恢复 N：当前 + N > 上限 → 溢出，拒买
          ② 全部恢复：上限 − 当前 ≤ 100 → 缺口太小，拒买
          ③ 接下来几日恢复：当前 / 上限 ≥ 1/2 → 精力还够，拒买
        登记表里恢复量是 None（还没采集到）→ **不拦，只垫底**；读不到
        精力/上限 → 不拦（同「周数读不到不过滤」口径，OCR 抖一帧不罢工）。
        """
        name = item.get("name") or ""
        hit = None
        for word, effect in SHOP_STAMINA_ITEMS.items():
            if word in name and (hit is None or len(word) > len(hit[0])):
                hit = (word, effect)
        if hit is None:
            return None
        kind, amount = hit[1]
        if stamina is None or stamina_max is None or stamina_max <= 0:
            return None
        room = max(stamina_max - stamina, 0)
        if kind == "full":
            if room <= 100:
                return f"缺口 {room} ≤ 100"
            return None
        if kind == "immediate":
            if amount is None:
                return None                    # 恢复量未登记：只垫底，不拦
            if stamina + amount > stamina_max:
                return f"立刻 {amount} 会溢出（{stamina}+{amount}>{stamina_max}）"
            return None
        if kind == "daily":
            if stamina / stamina_max >= 0.5:
                return f"精力 {stamina}/{stamina_max} ≥ 一半"
            return None
        return None

    # ---------- 商品表 / 流派优先级 ----------

    def shop_category(self, name):
        """商品类别：先查表，查不到按名字前缀兜底，再不认识归「属性」。

        归「属性」是保守兜底：它仍然受周数、余额、上限余量约束，
        不会像 `never` 那样被无条件丢掉，也不会像 `cash` 那样无条件优先。
        """
        hit = SHOP_ITEMS.get(name)
        if hit is not None:
            return hit[0]
        for prefix, cat in SHOP_ITEM_PREFIX:
            if prefix in name:
                return cat
        return "attribute"

    def shop_tier(self, category):
        """该类商品在商店里的档位；不在表里返回 None（不参与购买）。

        2026-11-11 用户口径：**所有流派共用同一条顺序**，商店里不按流派筛选。
        """
        try:
            return SHOP_PRIORITY.index(category)
        except ValueError:
            return None

    def shop_value(self, item):
        """同档内比大小用的数值；表外商品按品质色兜底（彩75/金50/紫25）。"""
        hit = SHOP_ITEMS.get(item["name"])
        if hit is None:
            return {"彩": 75, "金": 50, "紫": 25}.get(
                item.get("quality"), 0)
        cat, _price, val = hit
        if val is None:
            return 0
        if cat == "milk":
            return val[0] * 10 + val[1]
        if cat == "textbook":
            return val[1]
        if cat == "equipment_boost":
            return val[1] if isinstance(val, tuple) else 0
        if cat == "stamina" and isinstance(val, tuple):
            return val[0]
        return val if isinstance(val, (int, float)) else 0

    def save_money_for_weekend(self):
        """这个流派的零花钱要留给周末自己的场所吗？

        2026-11-11 用户口径：
        - 扭蛋流（步行街）/ 小游戏流（游戏厅）要留 360，前 6 周只拿必拿；
        - 训练流 / 商店流不设这道限制，进店就按顺序买；
        - 流派未知时按保守处理（当作要留钱）。
        """
        if not self.style_known:
            return True
        return self.style in ("扭蛋流", "小游戏流")

    def shop_targets(self):
        """本局要重点买的科目/属性集合；None = 不筛选（目前默认）。

        将来按队长定位算：
          队长名 → `constants.POSITION_BY_NAME` → 位置/推荐属性 → 科目集合
        返回集合后，非目标科目的教科书/属性商品会降一个子档（不剔除，
        避免刷不出目标时什么都不买）。
        """
        return None

    def _target_penalty(self, item):
        """非目标科目的教科书/属性商品 → 1；其余或未指定目标 → 0。"""
        targets = self.shop_targets()
        if not targets:
            return 0
        if self.shop_category(item["name"]) not in ("textbook", "attribute"):
            return 0
        return 0 if any(t in item["name"] for t in targets) else 1

    def training_days_left(self):
        """本局剩余训练日（周一~周五）；读不到日期返回 None（不拦）。"""
        week, day = self.current_day()
        idx = WEEKDAY_INDEX.get(day or "")
        if week is None or idx is None:
            return None
        this_week = max(0, SHOP_TRAINING_WEEKDAYS - idx + 1) if idx <= 5 else 0
        full_weeks = max(0, SHOP_SEASON_WEEKS - week)
        return this_week + full_weeks * SHOP_TRAINING_WEEKDAYS

    def pick_shop_item(self, items, money, policy, stamina=None, stamina_max=None):
        """选哪件商品？返回 (商品, 原因) 或 (None, 原因)。

        2026-11-11 重构（用户口径）：**一条管道** admit → tier → rank，
        不再有「彩金紫」和「精力垫底」的硬编码分支。

        顺序：
          1. 永不买（苦味/怪味/庇佑/护符）直接剔；
          2. 「不买」策略总否决；
          3. 现金类（老板的馈赠/大乐透）无条件优先；
          4. 第 7 周前只买 0 元；
          5. 精力类过三类门槛；
          6. 余额不够的不买；
          7. 时限商品要剩余训练日够；
          8. 按 `constants.SHOP_PRIORITY` 的档位排序：同档比数值、再比价格。

        流派差异**只体现在 `SHOP_PRIORITY` 那张表里**，代码不分叉：
        训练流 cash→milk→stamina→textbook→equipment→attribute；
        其他流派 cash→attribute→stamina，牛奶/教科书/器材不在它们的考虑范围。
        """
        week = self.current_day()[0]
        items = [i for i in items
                 if self.shop_category(i["name"]) != "never"
                 and not any(w in i["name"] for w in SHOP_NEVER_BUY)]
        if policy == "不买":
            return None, "策略是只逛不买"

        # 现金类：必拿。价格以**表里登记的售价**为准（OCR 把 0 元读成 200
        # 也照拿）；表里没有的才看 OCR 价格。
        cash = [i for i in items if self.shop_category(i["name"]) == "cash"]
        for i in cash:
            hit = SHOP_ITEMS.get(i["name"])
            if hit is not None:
                i["table_price"] = hit[1]
        free_cash = [i for i in cash
                     if i.get("table_price", i["price"]) == 0]
        paid_cash = [i for i in cash
                     if i.get("table_price", i["price"]) > 0
                     and money is not None
                     and i.get("table_price", i["price"]) <= money]
        if free_cash or paid_cash:
            pick = max(free_cash or paid_cash, key=lambda i: -i["y"])
            return pick, "必定优先拿（%s）" % pick["name"]

        # 扭蛋流 / 小游戏流：前 6 周零花钱要留周末，只拿必拿（现金类，
        # 上面已经返回）；训练流 / 商店流不设周数限制，进店就按顺序买。
        if (week is not None and week < LATE_SHOP_FROM_WEEK
                and self.save_money_for_weekend()):
            return None, "第 7 周前扭蛋/小游戏只买必拿（零花钱留周末）"

        # 精力门槛：按 `SHOP_STAMINA_ITEMS` 登记表判（不是按类别），
        # 免费的不拦；登记表外的商品 `shop_stamina_gate` 直接返回 None。
        kept, rejected = [], []
        for i in items:
            if i["price"] == 0:
                kept.append(i)
                continue
            why = self.shop_stamina_gate(i, stamina, stamina_max)
            if why:
                i["reject"] = why
                rejected.append("%s（%s）" % (i["name"], why))
            else:
                kept.append(i)
        if not kept:
            return None, "恢复精力商品不满足门槛，关店：" + "、".join(rejected)

        # 余额
        affordable = [i for i in kept
                      if i["price"] == 0
                      or (money is not None and i["price"] <= money)]
        if not affordable:
            return None, ("没有买得起的商品了"
                          + ("；零用钱读不到，不盲买" if money is None else ""))

        # 时限商品：剩余训练日不够就不买
        days_left = self.training_days_left()
        if days_left is not None:
            affordable = [
                i for i in affordable
                if days_left >= SHOP_MIN_DAYS_LEFT.get(
                    self.shop_category(i["name"]), 0)]

        # 排序：档位 → 数值 → 价格 → 屏幕靠上
        ranked = []
        for i in affordable:
            cat = self.shop_category(i["name"])
            tier = self.shop_tier(cat)
            if tier is None:
                continue
            ranked.append((tier, self._target_penalty(i),
                           -self.shop_value(i), i["price"], i["y"], i))
        if not ranked:
            return None, "当前流派不买这些商品"
        ranked.sort(key=lambda r: r[:5])
        pick = ranked[0][5]
        return pick, "按流派优先级购买（%s）" % self.shop_category(pick["name"])
