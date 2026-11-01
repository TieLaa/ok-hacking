"""步行街（扭蛋）：**地点模块**。

用户 2026-11-01 的架构（见 `docs/ARCHITECTURE.md`）：

    每个地点有自己的模块，只管自己的判断：
    **内页判据**（这是不是我的页面）+ **买什么/做什么** + **自己的状态**。

这个模块提供三样东西，handler 只负责「照它说的做」：

    1. `street_page(texts)`  —— 这是不是步行街扭蛋页？
    2. `choose_gacha_card(...)` —— 三张卡里买哪张？（纯函数，不点击）
    3. `street_state()` —— 我自己的状态（试过没有 / 买成了没有）

**它不需要知道别的地点**。「现在在餐厅但今天本来该进步行街」
这种问题由**地图模块**回答（说明是钱不够或没有合适的扭蛋）。

品质优先级（用户 2026-10-26，2026-11-01 更新）：

    1. 品质优先级 **彩色 > 金色 > 紫色**（彩色是训练等级提升后解锁的最高品质）；
       蓝色、其他一律不买（宁可退出）；
    2. 同等情况下**先「增强款」，再有队友加成的**（都相同才比价格）；
    3. 「消耗/清空精力」的卡：只有体力低于阈值（默认 30）才买；
    4. 没有合格卡就退出，不点确定。

历史事故（都写在这里，避免又被改坏）：

    - 2026-10-26：旧版只按 rank*10 打分，蓝卡也有 10 分，结果 72 体力买了
      「精力转换-意识（消耗所有精力）」，体力归 0，周日只能划船回 30。
      现在清空卡在体力够时必须排除。
    - 2026-11-01：彩色卡（`体能增加+++`，卡头是彩虹渐变）被 `card_quality_at`
      按中位色相判成「紫」（彩虹带的中位色相正好落在紫区），于是金卡 300 压过
      它 200 → 又买了金卡（用户：「中间的是彩色，你已经点错了一次了」）。
      现在 `card_band_quality` 先认彩虹（彩），再退回单色判据。
    - 2026-11-01 15:09：卡面文字先渲染、价格药丸后渲染，中间那一拍读不到卡，
      旧代码当成「页面是空的」→ 退出 → 白丢一次扭蛋。现在读卡有兜底定位、
      价格未出不算「稳定」、空结果等 4 拍。
"""

from src.tasks.ui import labels

    # 地点名（地图上的按钮文字 / 状态里的键）
PLACE = "步行街"




class StreetPlace:
    """步行街的判断与状态。**不点击**，只给判断。"""

    # 退出扭蛋页的兜底坐标（右上角 ↩）。
    # 2026-10-27 实测（logs/debug/1027-000428_nochange3）：第一次进店的
    # 「请选择1个扭蛋」页底部只有「能力详情 / 确定」，**没有「放弃」**，
    # 旧兜底坐标 (0.346w, 0.896h) 正好落在两个按钮中间的空白，
    # 连续 9 拍点不动 → 任务被判「连续 3 轮点不动」停止。
    # 右上角 ↩ (0.957w, 0.047h) 实测有效，退回周末地图页。
    #
    # 坐标放在**地点模块**里：通用层不认识这一页长什么样。
    EXIT_COORDS = (0.957, 0.047)

    # ---------- 页面判据 ----------

    def street_page(self, texts):
        """这是不是步行街扭蛋页？返回 True/False。

        2026-10-31 实跑（野上泷额外周末，第3周星期五）：**周末地图**上步行街
        那条奖励提示这次写的是「扭蛋增强」（平时是「零用钱+」），`STREET_TITLE`
        里的「扭蛋」用包含匹配把地图判成了扭蛋页 → 三张卡当然读不出来 →
        退出 → 又回到训练日主界面，当天的外出白跑（用户：「进了出行地图然后
        就退回来了，又点了训练」）。

        地图上有 ≥2 个地点名，扭蛋页只有一个，用它区分。
        """
        if self.match_text(texts, labels.STREET_TITLE) is None:
            return False
        places = [t for t in texts
                  if t.name.strip() in labels.WEEKEND_PLACES]
        return len(places) < 2

    def street_enabled(self):
        """配置开关「允许进步行街」开了吗？"""
        return bool(self.config.get("允许进步行街"))

    # ---------- 买哪张卡（纯判断，不点击） ----------

    def gacha_candidates(self, cards, stamina, stamina_max, money):
        """三张卡里哪些值得买、买哪张。

        返回 (best, detail, all_cards)：
        - `best` 是要买的那张（None = 没有可买的）
        - `detail` 是给人看的候选清单（日志用）
        - `all_cards` 是全部三张（不管买不买）

        参数里 `stamina` / `money` 可能是 None（读不到）——
        读不到零用钱或价格时**不买**（买不起点「确定」游戏不扣钱、页面也不关，
        旧代码会一直「选卡 → 确定 → 没反应」，2026-10-26 实测）。
        """
        clear_limit = int(self.config.get("扭蛋清空体力阈值"))
        allow_clear = stamina is not None and stamina < clear_limit

        # 品质优先级（配置项「扭蛋品质优先」，默认 彩色 > 金色 > 紫色）：
        # 列表 = 从高到低的品质名，分数按位置给（第一位 400、第二位 300……），
        # 间隔远大于同品质内的任何差别，保证「品质永远是第一权重」；
        # 不在列表里的品质（蓝/其他）直接不当候选。
        order = list(self.config.get("扭蛋品质优先") or ["彩", "金", "紫"])
        base_of = {q: (len(order) - i) * 100 for i, q in enumerate(order)}

        def score(c):
            s = base_of.get(c["quality"], 0)
            s += 1 if "+" in c["name"] else 0
            # 2026-10-27（用户截图 45/130 三选一）：「精力恢复++」
            # （立刻恢复90，之后每天30×4天，金卡+队友）和「运控增加++」
            # 同为 141 分，max() 取第一个把恢复卡丢掉了。
            # 恢复量按「这次真能补上的体力」折半计分：满体力时 room=0 不加分，
            # 不会为了满体力白花钱。
            if c.get("recover") and stamina is not None:
                room = max(stamina_max - stamina, 0)
                s += int(min(c["recover"], room) * 0.5)
            return s

        ok = []
        for c in cards:
            if c["clear"]:
                # 清空卡只在体力低于阈值时买（这时是拿体力换属性）
                if not allow_clear:
                    continue
            elif c["quality"] not in base_of:
                continue
            # 读不到价格就不买：买不起时点「确定」游戏不扣钱、页面也不关，
            # 旧代码会一直「选卡 → 确定 → 没反应」（2026-10-26 实测）。
            # 2026-11-01 15:09：卡面先渲染、价格后渲染的那一拍价格是 None，
            # 这时宁可退出等下一轮，也不能按「猜的价格」买。
            if c["price"] is None:
                continue
            # 买不起就跳过，看下一张（2026-10-26：零用钱 50、卡片 60，
            # 旧代码照样点确定 → 停留在扭蛋页反复点）。
            if money is not None and c["price"] > money:
                continue
            c["score"] = score(c)
            ok.append(c)

        detail = "、".join(
            f"{c['name']}({c['quality']}{'+队友' if c['mate'] else ''}"
            f"{'·增强' if c.get('enhanced') else ''}"
            f"{'·恢复' + str(c['recover']) if c.get('recover') else ''}"
            f"{'·清空' if c['clear'] else ''}"
            f"{'/' + str(c['price']) + '币' if c['price'] is not None else '/价格未出'}"
            f"/{score(c)})" for c in cards)
        if not ok:
            return None, detail, cards
        # 同分时的取舍（用户 2026-11-01：「同分排序 增强款 > 队友」，都相同再比价格）：
        # score（品质/恢复/名字）打平以后，先「增强款」，再「有队友」，最后「便宜的」。
        # 2026-10-30 的老口径就是这个顺序；这里只是把它从 score 挪到同分比较里，
        # 免得「增强款 +30 / 队友 +20」把恢复精力的加成压掉。
        best = max(ok, key=lambda c: (c["score"], bool(c.get("enhanced")),
                                      c["mate"], -c["price"]))
        return best, detail, cards

    # ---------- 地图侧：值不值得来 ----------

    def has_boost_label(self, texts, label_box):
        """步行街名字框的**左上方**有没有「扭蛋增强！」标签？

        2026-11-01 修：原来只 `match_text(texts, ("扭蛋增强","扭蛋加强"))` ——
        全屏出现这四个字就生效，不看它贴在哪。于是别处只要出现同样的字
        （比如队友台词、别的界面的说明文字），就会误判成「步行街有加成」
        → 不管钱和队友直进步行街。

        现在连**位置**一起判（和 `venue_on_discount` 同一套路）：
        标签必须贴着步行街名字的左上方（Δcx ≤ 0.20、Δcy ≤ 0.12、且在左边）。
        实测 10 张真实帧的 Δcx=0.105~0.107 / Δcy=0.048~0.049，阈值留了 2 倍余量。
        """
        if label_box is None:
            return False
        cx = label_box.x + label_box.width / 2
        cy = label_box.y + label_box.height / 2
        for t in texts or []:
            name = t.name.strip()
            if not any(k in name for k in labels.STREET_BOOST_TEXTS):
                continue
            tcx = t.x + t.width / 2
            tcy = t.y + t.height / 2
            if tcx >= cx:                      # 必须在名字左边
                continue
            if abs(tcx - cx) / self.width > self.STREET_BOOST_DX:
                continue
            if abs(tcy - cy) / self.height > self.STREET_BOOST_DY:
                continue
            return True
        return False

    # 「扭蛋增强！」标签与「步行街」名字框的相对位置阈值（比例）。
    # 实测 10 张真实地图帧（1549x871）：标签 cx=0.457 cy=0.809、
    # 步行街 cx=0.564 cy=0.858 → Δcx=0.105~0.107、Δcy=0.048~0.049。
    STREET_BOOST_DX = 0.20
    STREET_BOOST_DY = 0.12

    # ---------- 我自己的状态 ----------

    def street_state(self):
        """步行街自己的状态（试过没有 / 买成了没有）。

        三个状态分开（2026-11-01 15:09 事故的教训）：
        - `visited`     今天**进过**步行街（进过就算来过，防止反复进）
        - `gacha_tried` 今天**试过**抽扭蛋（买没买成都算）
        - `gacha_spent` 今天**买成了**

        地图上按 `gacha_tried` 排除步行街（试过了就不再试），
        按 `gacha_spent` 决定还要不要去餐厅打工。

        为什么不干脆只记一个「进过」：15:09 那次没买到，如果完全不登记，
        回到地图 todo 里还留着步行街 → 又点进去 → 又失败 → **无限循环**。
        """
        return self.day_state()

    def mark_tried(self):
        """今天试过抽扭蛋了（进过页面就算）。"""
        self.street_state().setdefault("gacha_tried", set()).add(PLACE)

    def mark_spent(self):
        """今天买成了（点「确定」成功才算）。"""
        self.street_state()["gacha_spent"] = True

    def tried_today(self):
        return PLACE in self.street_state().get("gacha_tried", set())

    def spent_today(self):
        return bool(self.street_state().get("gacha_spent"))
