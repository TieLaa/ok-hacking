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
        """周末地图：决定今天先去哪个地点。

        **训练流周六**（步行街 / 游戏厅 二选一，买扭蛋/玩小游戏当天就结束），
        用户 2026-10-30 口径，优先级：
        1. **只有一个有队友** → 去有队友的；
        2. **都有队友** → 再比**打折**（地点名左边橙色「打折！」爆炸标签），只有一个打折就去那个；
        3. **都没队友、但只有一个在打折** → 去打折的（2026-10-30 用户实机补充）；
        4. 其他情况（都没队友也没打折 / 都打折）→ 保持原顺序
           （步行街 → 游戏厅 → 公园）。

        **扭蛋流周五/周六/周日**（步行街 / 餐厅）：见下面的分支注释，
        2026-11-03 用户口径 —— 钱 ≤300 直接打工，不看队友。

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
            # 用户 2026-11-03 口径（扭蛋流的周五/周六/周日）：
            #   0. 零用钱读不到 → 去步行街（不拿打工赌今天的消费）；
            #   1. 零用钱 <= 300 → **直接去餐厅打工，不看队友**；
            #   2. 零用钱 > 300：
            #      - 步行街有「扭蛋增强！」**或步行街在打折** → 步行街；
            #      - 两边都有队友 → 步行街；
            #      - 只有一边有队友 → 去有队友的那边；
            #      - 都没队友 → 周五/周六餐厅，周日步行街。
            # 注意：≤300 时「扭蛋增强 / 打折」也不再豁免 —— 钱不够就打工，明天再抽。
            # 增强标签的判据在 `places/street.py`（连位置一起判，只认贴在
            # 步行街左上角的那个，别处出现同样字眼不算）；打折判据见
            # `venue_on_discount`（名字左边同一行有「打折」）。
            money = self.read_money(texts)
            street_tags = self.street_pull_tags(texts, info)
            if money is None:
                chosen = "步行街"
                reason = "零用钱读不到，去步行街"
            elif money <= 300:
                # 直接打工：不再绕步行街，今天不抽扭蛋
                self.log_info(f"周末地点：零用钱 {money} <= 300 → 只去「餐厅」打工")
                return ["餐厅"]
            elif street_tags:
                chosen = "步行街"
                reason = "、".join(street_tags)
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
            self.log_info(f"周末地点：{info} → {reason}，先进「{chosen}」")
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

    # ---------- 步行街今天值不值得优先去 ----------

    def street_pull_tags(self, texts, info):
        """步行街「优先去」的标签：**「扭蛋增强！」或打折都算**（用户 2026-11-03）。

        2026-11-03 用户口径原话：「扭蛋增强或者打折都优先步行街，其他逻辑不变」。
        所以原来只认增强标签的那一档，现在扩成「增强 or 打折」两个标签同一档。

        两条都不要越过「零用钱 ≤300 → 直接去餐厅打工」那道闸门：
        钱不够时增强/打折都不豁免（用户 2026-11-03 同日口径），这是调用方的顺序。

        参数 `info` 是 `prefer_mate_venue` 已经算好的
        `{"步行街": {"mate":…, "discount":…}, …}`；步行街今天不在候选里时
        自然拿不到 discount（那时也不该因为打折改道）。
        """
        tags = []
        if self.has_boost_label(texts, self._venue_box(texts, "步行街")):
            tags.append("有「扭蛋增强！」")
        if (info.get("步行街") or {}).get("discount"):
            tags.append("步行街在打折")
        return tags

    # ---------- 地点名的框 ----------

    def _venue_box(self, texts, place):
        """地点名对应的文本框（整词优先，其次 ≤4 字的短框）。"""
        box = self.exact_text(texts, (place,))
        if box is not None:
            return box
        return next((t for t in texts
                     if place in t.name and len(t.name.strip()) <= 4), None)
