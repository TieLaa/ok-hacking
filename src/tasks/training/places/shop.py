"""商店（买器材/手册）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：
每个地点有自己的模块，只管自己的判断。

界面（实机 2026-10-26）：
- 左侧商品卡：名称 + 剩余数量 + 价格（100 / 60 / 45），**卡片底色 = 品质色**；
- 右侧是选中商品的详情和**黄色购买条**（价格就在条上）；
- 右上角 X 关闭；顶栏金币是专注条右边那个独立数字。

用户口径（2026-10-26）：
- 平时只买金卡和紫卡（蓝/白不买）；
- **赛前一天**（左上角倒计时「1天」）进店把零用钱花光，金紫优先、
  买完金紫还有钱就继续买别的（钱不带进下一场，留着没用）。

所以这里一拍只买**一件**：买完不关店，下一拍重新 OCR 再决定；
金紫买不起了 / 钱花光了才关店。

2026-10-29 用户口径：**钱够就买金/紫**。旧版这里还有一道
「零用钱 < 商店进店余额(300) 就不买」的门槛，跟这条直接冲突
（实跑：120 零用钱、店里有 100 的紫卡，被门槛拦掉），已删除。
"""

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

    # ---------- 买哪件（纯判断） ----------

    def shop_items(self, texts):
        """读出商品列表（`read_shop_items` 的封装）。"""
        return self.read_shop_items(texts)

    def pick_shop_item(self, items, money, policy, spend_all=False):
        """选哪件商品？返回 (商品, 原因) 或 (None, 原因)。

        `policy` = 配置项「购买策略」：只买金和紫 / 只买器材手册+ / 买最贵 / 不买。
        `spend_all` = 赛前清空余额（金紫买完也继续买别的，反正带不走）。
        """
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
        if policy == "只买金和紫":
            pool = [i for i in affordable if i["quality"] in ("金", "紫")]
            if spend_all and not pool:
                # 赛前一天：金紫买完了，剩下的钱继续买别的，反正带不走
                pool = affordable
                self.log_info("赛前清空：金紫已买完，继续买剩余商品")
            if not pool:
                return None, "没有买得起的金/紫商品"
            pick = max(pool, key=lambda i: (1 if i["quality"] == "金" else 0,
                                           i["price"]))
            return pick, "金紫优先"
        if policy == "只买器材手册+":
            pool = [i for i in affordable if "器材手册" in i["name"]]
            if not pool:
                return None, "没有器材手册"
            pick = max(pool, key=lambda i: i["price"])
            return pick, "器材手册优先"
        pick = max(affordable, key=lambda i: (
            1 if "器材手册" in i["name"] else 0, i["price"]))
        return pick, "买最贵"
