"""出行地图（周末地图）：**决策模块** —— 今天什么流派进什么地点。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：

    **地图模块就是决策层**：它比较各地点「今天值不值得去」，然后点地点名。
    地点模块**不需要**互相知道 ——
    「今天本来该进步行街，但现在在餐厅」这个问题由地图模块回答
    （说明是钱不够，或没有合适的扭蛋）。

所以本模块是**唯一**知道「有哪些地点」的地方 —— 地点模块只知道自己。

三层分工：

    本模块（地图）    决策：今天去哪几个地点、先去哪个
        ↓ 问「你今天值不值得去」
    地点模块          判断：内页认不认、买什么/做什么
        ↓
    通用交互层        动作：选选项、按确定、按坐标退出
"""

from src.tasks.training.constants import WEEKEND_PLAN
from src.tasks.ui import labels


class MapPlace:
    """周末地图的决策与状态。**不点击**，只给判断。"""

    # ---------- 地图页判据 ----------

    def weekend_map_page(self, texts):
        """这是不是周末地图？

        地图上没有「出行」按钮（2026-10-27 修正）：旧判据是「有地点名就是地图」，
        但周末首页的台词也含地点名（「告诉她公园的草坪是假的」），
        结果首页被判成地图、永远不点「出行」。
        """
        if self.match_text(texts, "出行") is not None:
            return False
        return self.match_text(texts, labels.WEEKEND_PLACES) is not None

    # ---------- 今天去哪几个地点 ----------

    def today_places(self, day=None, st=None):
        """今天该去的地点（有序，前一个走完才轮到下一个）。

        判据来自 `constants.WEEKEND_PLAN`（星期 + 流派 → 地点列表）。
        """
        if st is None:
            st = self.weekend_state()
        if day is None:
            day = (st.get("date") or (None, None))[1]
        # 读不到星期时按周六那一行兜底（每个流派都有定义）
        places = list(WEEKEND_PLAN.get((day, self.style))
                      or WEEKEND_PLAN.get(("六", self.style))
                      or ["公园"])
        # 2026-10-30 用户口径：扭蛋流（默认带野上泷，周五也有额外周末）先抽扭蛋，
        # **今天没消费**才再去餐厅打工；已经买到东西就不去了。
        if self.style == "扭蛋流" and day in ("五", "六", "日"):
            places = ["步行街"]
            if not st.get("gacha_spent"):
                places.append("餐厅")
        places = [p for p in places if p != "商店"]
        if not self.config.get("允许进游戏厅"):
            places = [p for p in places if p != "游戏厅"]
        if not self.config.get("允许进步行街"):
            places = [p for p in places if p != "步行街"]
        # 兜底：开关全关/表里没这个组合时总得花掉一天，退化去公园。
        if not places:
            places = ["公园"]
        return places

    # ---------- 先去哪个 ----------

    def prefer_mate_venue(self, texts, places, day=None):
        """训练流周六：步行街 / 游戏厅 二选一（买扭蛋/玩小游戏当天就结束）。

        用户 2026-10-30 口径，优先级：
        1. **只有一个有队友** → 去有队友的；
        2. **都有队友** → 再比**打折**（地点名左边橙色「打折！」爆炸标签），只有一个打折就去那个；
        3. **都没队友、但只有一个在打折** → 去打折的（2026-10-30 用户实机补充）；
        4. 其他情况（都没队友也没打折 / 都打折）→ 保持原顺序
           （步行街 → 游戏厅 → 公园）。

        队友气泡判据见 BaseGirlsTask.venue_has_mate（深藏青色块）；
        打折判据见 venue_on_discount（OCR 文本 + 名字左侧）。
        """
        if day == "六" and self.style == "训练流":
            pair = [p for p in places if p in ("步行街", "游戏厅")]
            mode = "训练流"
        elif self.style == "扭蛋流" and day in ("五", "六", "日"):
            # 野上泷（默认带）：周五额外周末也走同一套
            pair = [p for p in places if p in ("步行街", "餐厅")]
            mode = "扭蛋流"
        else:
            return places
        if len(pair) < 2:
            return places

        info = {}
        for place in pair:
            box = self._venue_box(texts, place)
            info[place] = {"mate": bool(box) and self.venue_has_mate(box),
                           "discount": bool(box) and self.venue_on_discount(texts, box)}

        mates = [p for p in pair if info[p]["mate"]]
        discounted = [p for p in pair if info[p]["discount"]]
        chosen = None
        if mode == "扭蛋流":
            # 2026-10-30 用户截图（第5周周五）：步行街上方出现彩色爆炸标签
            # 「扭蛋增强！」时，**不管有没有队友都先进步行街**。
            # 2026-11-01：判据搬进步行街模块（`places/street.py`），
            # 且连位置一起判 —— 这个标签只贴在步行街左上角，别处出现同样字眼不算。
            if self.has_boost_label(texts, self._venue_box(texts, "步行街")):
                self.log_info("周末地点：步行街有「扭蛋增强！」"
                              "→ 不管队友，先进步行街")
                return ["步行街"] + [p for p in places if p != "步行街"]
            # 用户 2026-10-30 完整口径（扭蛋流的周五/周六/周日）：
            #   0. 钱 > 300 → 先去步行街（钱多就该花）；
            #   1. 钱 <= 300：两边都有队友 → 先进步行街；
            #   2. 只有一边有队友 → 去有队友的那边；
            #   3. 都没队友 → 周五/周六去餐厅赚钱，周日去步行街消费。
            money = self.read_money(texts)
            if money is not None and money > 300:
                chosen = "步行街"
                reason = f"零用钱 {money} > 300，先去步行街消费"
            elif len(mates) == 2:
                chosen = "步行街"
                reason = "两边都有队友，先进步行街"
            elif mates:
                chosen = mates[0]
                reason = f"只有「{mates[0]}」有队友"
            elif day in ("五", "六"):
                chosen = "餐厅"
                reason = "都没队友，周五/周六去餐厅赚钱"
            else:
                chosen = "步行街"
                reason = "都没队友，周日去步行街消费"
            if chosen == "餐厅":
                # 直接去打工：不再绕步行街，今天不用抽扭蛋
                self.log_info(f"周末地点：{info} → {reason}，只去「餐厅」")
                return ["餐厅"]
            return [chosen] + [p for p in places if p != chosen]
        if len(mates) == 1:
            chosen = mates[0]
            reason = "只有一个有队友"
        elif len(mates) == 2:
            only = [p for p in mates if info[p]["discount"]]
            if len(only) == 1:
                chosen = only[0]
                reason = "都有队友，这个在打折"
        elif len(discounted) == 1:
            # 2026-10-30 用户实机：周六两个地点都没队友，游戏厅在打折
            # （左侧橙色「打折！」爆炸标签，venue_on_discount 判 True），
            # 但旧规则只在「都有队友」分支里比较折扣 → 退回原顺序选了步行街。
            # 都没队友时，打折的那个比顺序更值得去。
            chosen = discounted[0]
            reason = "都没有队友，这个在打折"
        if chosen is None:
            self.log_info(f"周末地点：{info} → 没法分出高下，按原顺序 {pair}")
            return places
        self.log_info(f"周末地点：{info} → {reason}，先进「{chosen}」")
        return [chosen] + [p for p in places if p != chosen]

    # ---------- 地点名的框 ----------

    def _venue_box(self, texts, place):
        """地点名对应的文本框（整词优先，其次 ≤4 字的短框）。"""
        box = self.exact_text(texts, (place,))
        if box is not None:
            return box
        return next((t for t in texts
                     if place in t.name and len(t.name.strip()) <= 4), None)
