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

2026-10-29 用户口径：**钱够就买金/紫**。旧版这里还有一道
「零用钱 < 商店进店余额(300) 就不买」的门槛，跟这条直接冲突
（实跑：120 零用钱、店里有 100 的紫卡，被门槛拦掉），已删除。

2026-11-05 用户口径：第 7 周以后**看到入口上方的「新品上市」提示才进店**
（判据 `shop_fresh_badge`），进去按「购买策略」正常顺序买，**不清空**；
第 7 周以后**不买「精力饮料」**（免费的照样拿），在 `pick_shop_item` 里过滤。
"""

from src.tasks.ui import labels
from src.tasks.training.constants import LATE_SHOP_FROM_WEEK

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

    def pick_shop_item(self, items, money, policy):
        """选哪件商品？返回 (商品, 原因) 或 (None, 原因)。

        `policy` = 配置项「购买策略」：只买彩金紫 / 只买器材手册+ / 买最贵 / 不买。
        2026-11-05 用户口径：**任何日子都不清空**（比赛日前一天也不清空），
        所以这里只有「策略」一条口径，没有「把钱花光」这个分支。

        2026-11-05 用户口径：第 7 周以后**不买「精力饮料」**——前期随便买，
        后期精力不缺，买了占商店格子还花钱。免费（0 元）的精力饮料照样拿。
        周数读不到时**不拦**（顶栏 OCR 抖一下不能让商店整拍罢工）。
        """
        week = self.current_day()[0]
        no_drink = week is not None and week >= LATE_SHOP_FROM_WEEK
        if no_drink:
            items = [i for i in items
                     if i["price"] == 0 or "精力饮料" not in i["name"]]
        # 免费商品（白神时久：每天多一件 0 元商品）：不花零用钱，
        # 余额不够、甚至钱读不到时都应该拿。
        free = [i for i in items if i["price"] == 0]
        if policy == "不买":
            return None, "策略是只逛不买"

        affordable = free + [i for i in items
                             if i["price"] > 0 and money is not None
                             and i["price"] <= money]
        if not affordable:
            return None, ("没有买得起的商品了"
                          + ("（零用钱读不到，不盲买）" if money is None else ""))

        if free:
            # 免费优先：0 元不花钱，品质只影响先拿哪张
            order = {"金": 2, "紫": 1}
            pick = max(free, key=lambda i: (order.get(i["quality"], 0), -i["y"]))
            return pick, "免费商品优先"
        if policy == "只买彩金紫":
            # 2026-11-06 用户口径：策略名从「只买金和紫」改过来，白名单补上「彩」
            # ——全局品质口径是 彩 > 金 > 紫，没有蓝（商店目前没有彩卡，留着缺口，
            # 解锁后自动生效）。只有蓝/白时必须停手，不是「退而买蓝」。
            pool = [i for i in affordable if i["quality"] in ("彩", "金", "紫")]
            if not pool:
                return None, "没有买得起的彩/金/紫商品"
            pick = max(pool, key=lambda i: ({"彩": 2, "金": 1}.get(i["quality"], 0),
                                           i["price"]))
            return pick, "彩金紫优先"
        if policy == "只买器材手册+":
            pool = [i for i in affordable if "器材手册" in i["name"]]
            if not pool:
                return None, "没有器材手册"
            pick = max(pool, key=lambda i: i["price"])
            return pick, "器材手册优先"
        pick = max(affordable, key=lambda i: (
            1 if "器材手册" in i["name"] else 0, i["price"]))
        return pick, "买最贵"
