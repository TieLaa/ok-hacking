"""商店、公园、步行街、游戏厅、外出。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import random
import re
import time

import os
import threading
from collections import deque

import cv2
import numpy as np

from src.tasks.ui import labels
from src.tasks.training.places import activities
from src.tasks.training.constants import (JPEG_PARAMS, PROJECT_ROOT,
                                          WORK_STAMINA_COST)  # noqa: F401


class WeekendHandlers:

    # 扭蛋页「连续几拍读不到卡就放弃」的阈值。
    # 2026-11-01 15:09 实跑：卡面文字先画出来、价格药丸后画出来，中间那一拍
    # 旧判据（2 拍）正好卡在「卡面在、价格没有」这个中间态上 → 退出，白丢一次扭蛋。
    # 现在放宽到 4 拍（≈3.2 秒），末尾还有「再等一拍 1.2 秒」的兜底。
    GACHA_EMPTY_LIMIT = 4

    # 地点详情弹窗的「前往XXX」：公园点「划船」只是打开详情，还要再点这个。
    # 2026-11-01：原来是一个正则塞了 9 个地点名（住在通用层里 → 新增地点就要改
    # 通用层，而且「前往游戏厅」这种从没见过的组合也会被匹配）。
    # 现在每个地点模块给自己的正则（`places/activities.py`），这里只管找。
    ACTIVITY_GO = activities.PARK_GO
    ACTIVITY_GO_ALL = (activities.PARK_GO, activities.RESTAURANT_GO,
                       activities.ARCADE_GO, activities.SHOP_GO,
                       activities.STREET_GO)

    def handle_activity_go(self, texts):
        """地点详情弹窗：右下「前往划船 / 前往野餐」才是真正进入。

        坑（2026-10-26）：公园点「划船」只弹出详情（推荐属性 / 随机属性+10 /
        前往划船），必须再点「前往划船」。旧流程没有这个二级确认，
        于是停在详情页、每拍点屏幕中央都没反应，最后被卡死检测停掉。

        坑（2026-10-31 12:13 实跑，餐厅）：精力只剩 23/130，右卡「当服务员」
        的详情弹窗写着「精力不足 ⚡25」，「前往当服务员」是**灰的**。旧代码把它
        当普通按钮连点 4 分钟（每拍 OCR 有细抖动，`_no_change` 计数被复位，
        连「3 轮点不动就停任务」都没触发）。现在两级判据：弹窗自己说不足、
        或同一个「前往」点过一次画面没变 → **不点**，退出弹窗并把这张卡记成
        今天不可选；下一拍回到卡片页会自动改选另一张（桌游GM 不耗精力）。
        """
        target = self.find_go_button(texts, self.ACTIVITY_GO_ALL)
        if target is None:
            self._activity_go_key = None
            return False
        reason = self.activity_go_blocked(target, texts)
        if reason:
            name = self.block_activity(target.name)
            self.log_warning(
                f"「{target.name.strip()}」点不动（{reason}）→ 退出详情，改选「{name}」以外的卡",
                notify=True)
            self.close_activity_detail()
            return True
        self.click_box(target, after_sleep=0.9)
        self.log_info(f"地点确认：{target.name.strip()}")
        return True

    # 详情弹窗里「这个按钮现在点不动」的文字证据（2026-10-31 餐厅实测「精力不足」）。
    BLOCKED_HINTS = ("精力不足", "体力不足", "条件不足", "无法选择", "无法前往",
                     "不可选择", "尚未满足")

    def activity_go_blocked(self, target, texts):
        """这个「前往XXX」现在点不点得动：点得动返回 None，否则返回原因。"""
        for t in texts:
            s = t.name.replace(" ", "")
            if not any(k in s for k in self.BLOCKED_HINTS):
                continue
            dy = abs((t.y + t.height / 2) - (target.y + target.height / 2))
            if dy < self.height * 0.25:
                return s
        # 上一拍点过**同一个**按钮、画面文字却一模一样 → 这个按钮对本页无效。
        # 比 flow 的 `_no_change` 靠谱：后者按整页指纹算，OCR 抖一下就被复位。
        key = (target.name.strip(), self._signature(texts))
        if getattr(self, "_activity_go_key", None) == key:
            return "上一下点了没反应"
        self._activity_go_key = key
        return None

    def block_activity(self, go_text):
        """「前往当服务员」→ 把选项名「当服务员」记成今天不可选。"""
        option = re.sub(r"^前往", "", go_text.strip())
        self.blocked_activities().add(option)
        return option

    def blocked_activities(self):
        """今天已经证实点不动的打工选项。

        挂在 `_weekend` 上：跟着「第N周 + 星期X」一起重置（`day_state()`），
        不落盘、不跨天。
        """
        return self._weekend.setdefault("blocked", set())

    # 地点详情弹窗右上角的 ×：2026-10-31 实测（餐厅 / 1549x871）在 (0.87w, 0.19h)。
    # 图上就是一个叉，OCR 读不到，所以先试文字、再按坐标兜底。
    ACTIVITY_CLOSE = (0.870, 0.195)

    def close_activity_detail(self, after_sleep=0.6):
        """关掉地点详情弹窗、退回卡片页 —— 别直接退出地点，那样就没法改选另一张。

        退出类按钮走 `labels.BUTTONS["exit_hatch"]`（全等匹配，2026-11-01 统一）：
        原来这里写的是 `click_text(("关闭","取消","返回"))`，那是**包含**匹配，
        正文里出现「返回」两个字也会被点；改成全等之后认不出来还有下面的坐标兜底。

        兜底坐标由**调用方**给（通用层不认识这个弹窗长什么样）。
        """
        if self.click_button("exit_hatch", after_sleep=after_sleep, must=False):
            return True
        self.exit_with_coords(self.ACTIVITY_CLOSE, after_sleep=after_sleep,
                              what="地点详情")
        return True

    def mark_visited(self, place):
        """地点内页真的打开后才登记「今天去过」。

        2026-10-26 用户反馈：在地图上点了「公园」，页面其实没打开，
        但旧代码点击前就 `visited.add(place)`，结果整天的公园都被跳过。
        现在只在各地点 handler 识别到自己的内页标题时登记。

        这里直接写 `_weekend`，**不**走 `day_state()` —— 后者按顶栏日期判断，
        地点内页如果读不到顶栏会重建整个状态，把 visited 清空。
        （`day_state()` 现在已经修成「读不到日期就沿用旧状态」，但这里
        直接写更稳：登记不该依赖「读得到日期」。）
        """
        self._weekend.setdefault("visited", set()).add(place)

    def leave_street(self, after_sleep=0.5):
        """退出扭蛋页：先找文字退路，再点右上角 ↩。

        2026-10-26 实测（1349x759）：**买完卡的详情页**底部有「放弃」，
        点它立即回到周末首页，「放弃」中心约 (0.346w, 0.896h)。

        退出类按钮走 `labels.BUTTONS["exit_hatch"]`（全等匹配）：两个页面各自
        只出现其中一个词，合成一个集合不改行为；同屏出现时「关闭」优先。

        兜底坐标由**步行街模块**给（`StreetPlace.EXIT_COORDS`）——
        通用层不认识这一页长什么样（2026-11-01）。
        """
        if self.click_button("exit_hatch", after_sleep=after_sleep, must=False):
            return
        self.exit_with_coords(self.EXIT_COORDS, after_sleep=after_sleep,
                              what="步行街")

    def handle_restaurant(self, texts):
        """餐厅内页：打工二选一（当服务员 / 桌游GM）。

        用户 2026-10-29 口径（**通用**，不分流派）：
        1. 队友多的优先 —— 卡片**左上角**挂着一排队友小头像，数头像个数；
        2. 队友一样多 → 选**右边**那张（消耗精力的）；
        3. 精力不够右边那张 → 改选**左边**（不消耗精力）。
        """
        if self.match_text(texts, labels.RESTAURANT_TITLE) is None:
            return False
        self.mark_visited("餐厅")
        order = self.restaurant_order(texts)
        if not order:
            self.log_warning("餐厅：两张卡今天都点不动（都提示精力不足）→ 退出餐厅",
                             notify=True)
            self.exit_page()
            return True
        return self.choose_option(texts, order)

    def restaurant_order(self, texts):
        """打工两张卡的点击顺序：**能选的优先** → 队友多 → 右边（耗精力）。

        判据（用户 2026-10-29）：
        - 先看这张卡现在**能不能选**（`option_selectable`）：界面提示不可用、
          或精力不够它标的消耗 → 不可选；
        - 可选的两张里：队友多（卡片左上角的小头像数）优先，一样多选右边；
        - 都不可选就按「右边优先」原样返回，让游戏自己拦（下一拍会重判）。
        右边那张默认按 `WORK_STAMINA_COST`（25）算消耗、左边按 0（不消耗），
        卡片上写明消耗时以卡片为准。

        2026-10-31 补两条：
        - 精力取**整帧 OCR**（`top_bar(texts)`）。裁剪顶栏重 OCR 会丢斜杠，
          stamina 恒为 None，这条规则等于没实现（餐厅实跑：23/130 仍去点 25 的卡）；
        - `blocked_activities()`：详情弹窗说过「精力不足」或「前往」点不动的卡，
          今天不再选（`logs` 里会打出来）。
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


    STOCK_TITLE = "请选择你要投资的股票"
    STOCK_TYPES = ("稳健型", "平衡型", "风险型")

    def handle_stock_pick(self, texts):
        """天赋「股神!」的模拟炒股：在**买得起**的卡里选第一张，再点确定。

        用户 2026-10-29 真机截图：
        - 三张卡「稳健型/平衡型/风险型」，价格 24/120/200，买不起的价格显示红色；
        - 黄色角标 + 头顶台词挂在游戏推荐的那张（两次分别在第一张、第二张），
          所以「角标」不是可靠判据，可靠的是**价格 ≤ 当前零用钱**；
        - 用户口径：选第一个（最左）能买的那张。
        """
        if self.match_text(texts, self.STOCK_TITLE) is None:
            return False
        self.dump_stock_frame(texts)
        # 这一页的金币在右上角（真机截图 cx≈0.80, cy≈0.11），不在训练日顶栏的
        # 0.50~0.62 区域，read_money 读不到 → 用页内的读法兜底。
        money = self.read_money(texts)
        if money is None:
            money = self.stock_money(texts)
        cards = []
        for name in self.STOCK_TYPES:
            box = self.exact_text(texts, (name,))
            if box is None:
                continue
            price = self.stock_card_price(texts, box)
            cards.append((box, price))
        if not cards:
            self.log_warning("炒股：认不出三张卡片，点第一个类型附近兜底")
            self.click(int(self.width * 0.25), int(self.height * 0.55), after_sleep=0.6)
            return True
        cards.sort(key=lambda pair: pair[0].x)
        detail = "、".join(
            f"{b.name}={p}{'·队友' * m}"
            for (b, p), m in ((pair, self.stock_card_marks(texts, pair[0]))
                              for pair in cards))
        affordable = [(b, p, self.stock_card_marks(texts, b)) for b, p in cards
                      if money is None or p is None or p <= money]
        if not affordable:
            self.log_warning(f"炒股：都没有买得起的（零用钱 {money}｜{detail}）→ 放弃")
            self.click_button("give_up", after_sleep=0.6, must=False)
            return True
        # 用户口径：买得起的里**有队友推荐（头顶气泡）优先**，同档价格高的优先。
        pick, price, marks = max(
            affordable, key=lambda x: (x[2], x[1] if x[1] is not None else -1))
        badge = f"·队友{marks}" if marks else ""
        cx = pick.x + pick.width / 2
        cy = pick.y + self.height * 0.18
        self.click(int(cx), int(cy), after_sleep=0.6)
        self.log_info(f"炒股：零用钱 {money}｜{detail} → 选「{pick.name}」({price}){badge}")
        if self.click_button("confirm", after_sleep=0.8, must=False):
            self.log_info("炒股：确定")
        return True

    def dump_stock_frame(self, texts):
        """炒股页限流存帧（每局最多 6 张）：用来量「头顶队友头像有几个」。"""
        if getattr(self, "_stock_dumps", 0) >= 6:
            return
        frame = self.frame
        if frame is None or not frame.any():
            return
        try:
            folder = os.path.join(PROJECT_ROOT, "logs", "debug")
            os.makedirs(folder, exist_ok=True)
            idx = getattr(self, "_stock_dumps", 0)
            path = os.path.join(
                folder, f"{time.strftime('%m%d-%H%M%S')}_stock{idx:02d}.jpg")
            cv2.imwrite(path, frame, JPEG_PARAMS)
            with open(path[:-4] + ".txt", "w", encoding="utf-8") as f:
                f.write("\n".join(
                    f"{t.name}\t({t.x},{t.y},{t.width},{t.height})" for t in texts))
            self._stock_dumps = idx + 1
        except Exception as e:                   # noqa: BLE001
            self.log_debug(f"炒股帧存证失败: {type(e).__name__}: {e}")

    def stock_card_marks(self, texts, label_box):
        """卡片头顶的台词气泡（「相信奇迹!」「一定能赚!」「我梭哈了」）= 队友推荐。

        气泡是**短文本 + 感叹号**，按 x 关联到最近的卡片（±0.12 屏宽）。
        OCR 偶尔把「!」吃掉，那就当这张没有标记（退化成按价格取高）。
        """
        if label_box is None:
            return 0
        cx_card = (label_box.x + label_box.width / 2) / self.width
        n = 0
        for t in texts:
            s = t.name.strip()
            if not s.endswith(("！", "!")) or len(s) > 12:
                continue
            cx = (t.x + t.width / 2) / self.width
            if abs(cx - cx_card) <= 0.12:
                n += 1
        return n

    def stock_money(self, texts):
        """炒股页右上角的金币数字（裸数字，没有斜杠）。"""
        for t in texts:
            v = t.name.strip()
            if not v.isdigit() or len(v) > 5:
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if 0.70 <= cx <= 0.90 and 0.05 <= cy <= 0.18:
                return int(v)
        return None

    def stock_card_price(self, texts, label_box):
        """卡片价格：类型名下方、同一列的那个数字（买不起时是红色的，OCR 一样能读）。"""
        best = None
        for t in texts:
            v = t.name.strip()
            if not v.isdigit() or len(v) > 4:
                continue
            cx = t.x + t.width / 2
            if abs(cx - (label_box.x + label_box.width / 2)) > self.width * 0.16:
                continue
            if not (label_box.y < t.y < label_box.y + self.height * 0.35):
                continue
            if best is None or t.y > best[0]:
                best = (t.y, int(v))
        return best[1] if best else None

    def handle_stock_result(self, texts):
        """炒股的两个结算页（2026-10-29 用户截图）。

        1. **股票走势**（有涨跌幅）：这一页只能点「卖出」，卖出后回到选股页再买；
        2. **炒股队收益排名**（全部炒股日结束后的总结）：点「下一步」继续。
        """
        if self.match_text(texts, ("股票走势", "涨跌幅")) is not None:
            if self.click_text(("卖出",), after_sleep=0.7, must=False):
                self.log_info("炒股：走势页卖出 → 回去买下一只")
                return True
            # 2026-10-30 实机：面板上的「卖出」是白字黄底，OCR 经常读不到 →
            # 落到 handle_dialog_continue 去点右上角「跳过」（快进图标，不推进），
            # 连续 3 轮点不动后任务直接停。这里按位置兜底点底部黄色大按钮。
            self.click(int(self.width * 0.5), int(self.height * 0.925), after_sleep=0.7)
            self.log_info("炒股：没认出「卖出」→ 点底部黄色按钮兜底")
            return True
        if self.match_text(texts, "收益排名") is not None:
            if self.click_button("next", after_sleep=0.7, must=False):
                self.log_info("炒股：收益排名 → 下一步")
                return True
        return False

    def handle_park(self, texts):
        """公园内页：散步（零用钱+）/ 划船（属性+）/ 野餐（精力++）。

        实机（2026-10-26 debug 包）：三个**圆形图标**按钮，名字和效果都在图标下方
        （「划船 属性+」），页顶有「在公园干点什么好呢?」。
        旧判据 `labels.PARK_TITLE = ("在公园", ...)` 依赖「在公园」三个字，
        但实际文案是「在公园干点什么好呢?」整句 —— 整句 OCR 时「在公园」不是独立框，
        match_text 的子串匹配靠"整段拼接"兜底才能命中，命中后返回的是**最长的框**
        （整句话），click_box 会点到句子中间，什么都点不到 → 卡死。

        现在改成：标题只当**判据**（有「公园」相关字样 + 有三个圆形选项就处理），
        实际点击永远走选项文字框（散步/划船/野餐）。
        """
        # 周末首页/地图上有「出行」按钮，公园内页没有（与 handle_shop 同一招）。
        # 2026-10-27 事故：地图页的对白「小澪奈总是吵着要去野餐…」包含「野餐」，
        # labels.PARK_TITLE 里的「野餐」直接把地图页判成公园内页 →
        # mark_visited("公园") 后 handle_weekend 认为「都去过了」，
        # 反复点返回循环 9 拍，任务被判卡死停止。
        if self.match_text(texts, "出行") is not None:
            return False
        if self.match_text(texts, labels.PARK_TITLE) is None:
            return False

        if self.style != "训练流":
            pref = self.config.get("公园选项")
            order = (pref,) + tuple(x for x in labels.PARK_OPTIONS if x != pref)
            ok = self.choose_option(texts, order)
            if ok:
                self.mark_visited("公园")
            return ok

        st = self.day_state()
        day = st["date"][1] if st["date"] else None
        if st.get("park_choice") is None:
            if day == "日":
                stamina = self.top_bar(texts).get("stamina")
                threshold = int(self.config.get("周末野餐阈值"))
                if stamina is not None and stamina < threshold:
                    st["park_choice"] = "野餐"
                    self.log_info(f"周日体力 {stamina} < {threshold} → 野餐补精力")
                else:
                    st["park_choice"] = "划船"
                    self.log_info(f"周日体力 {stamina} ≥ {threshold} → 划船（属性+）")
            else:
                st["park_choice"] = "划船"
                self.log_info("周六公园：划船（属性+，不再满体力吃野餐）")

        order = (st["park_choice"],) + tuple(
            x for x in labels.PARK_OPTIONS if x != st["park_choice"])
        ok = self.choose_option(texts, order)
        if ok:
            self.mark_visited("公园")
        return ok

    def handle_shop(self, texts):
        """商店：按「购买策略」买；买得起就多买几件，买不动了才关店。

        实机界面（2026-10-26）：
        - 左侧商品卡：名称 + 剩余数量 + 价格（100 / 60 / 45），**卡片底色 = 品质色**；
        - 右侧是选中商品的详情和**黄色购买条**（价格就在条上）；
        - 右上角 X 关闭；顶栏金币是体力条右边那个独立数字。

        用户口径（2026-10-26）：
        - 平时只买金卡和紫卡（蓝/白不买），零用钱 **≥300** 才进店；
        - **赛前一天**（左上角倒计时「1天」）进店并把零用钱花光，金紫优先、
          买完金紫还有钱就继续买别的（钱不带进下一场，留着没用）。
        所以这里一拍只买**一件**：买完不关店，下一拍重新 OCR 再决定；
        金紫买不起了 / 钱花光了才关店，并把这一天标记为「今天逛过了」。
        """
        # 周末首页也有「商店」两个字，但同时有「出行」，那是按钮不是商店页
        if self.match_text(texts, "出行") is not None:
            return False
        if self.match_text(texts, "商店") is None:
            return False
        if self.match_text(texts, ("上新时间", "剩余", "能力详情")) is None:
            return False

        # 确认真的进到商店页了才记「今天逛过」。比赛日点入口可能点空
        # （2026-10-30 15:04:04 点在顶栏「新品上市」上），那样当天该允许再试一次。
        self._shop_day = getattr(self, "_shop_try_key", None)

        policy = self.config.get("购买策略")
        money = self.read_money(texts)
        items = self.read_shop_items(texts)
        # 免费商品（白神时久：每天多一件 0 元商品）：不花零用钱，
        # 余额不够、甚至钱读不到时都应该拿。
        free = [i for i in items if i["price"] == 0]
        self.log_info("商店：" + "、".join(
            f"{i['name']}({i['price']}/{i['quality']})" for i in items)
            + f"｜零用钱 {money}｜策略 {policy}"
            + ("｜赛前清空" if self._shop_spend_all else ""))

        # 2026-10-29 用户口径：**钱够就买金/紫**。旧版这里还有一道
        # 「零用钱 < 商店进店余额(300) 就不买」的门槛，跟这条直接冲突
        # （实跑：120 零用钱、店里有 100 的紫卡，被门槛拦掉），已删除。
        # 买不起任何东西的分支在下面（没有可买的金/紫 → 关店）。
        if policy == "不买":
            self.log_info("商店：策略是只逛不买，退出")
            self.finish_shop(texts)
            return True

        affordable = free + [i for i in items
                             if i["price"] > 0 and money is not None and i["price"] <= money]
        if not affordable:
            self.log_info("商店：没有买得起的商品了，关店"
                          + ("（零用钱读不到，不盲买）" if money is None else ""))
            self.finish_shop(texts)
            return True

        if free:
            # 免费优先：0 元不花钱，品质只影响先拿哪张
            order = {"金": 2, "紫": 1}
            pick = max(free, key=lambda i: (order.get(i["quality"], 0), -i["y"]))
        elif policy == "只买金和紫":
            pool = [i for i in affordable if i["quality"] in ("金", "紫")]
            if self._shop_spend_all and not pool:
                # 赛前一天：金紫买完了，剩下的钱继续买别的，反正带不走
                pool = affordable
                self.log_info("赛前清空：金紫已买完，继续买剩余商品")
            if not pool:
                self.log_info("商店：没有买得起的金/紫商品，关店")
                self.finish_shop(texts)
                return True
            pick = max(pool, key=lambda i: (1 if i["quality"] == "金" else 0, i["price"]))
        elif policy == "只买器材手册+":
            pool = [i for i in affordable if "器材手册" in i["name"]]
            if not pool:
                self.log_info("商店：没有器材手册，关店")
                self.finish_shop(texts)
                return True
            pick = max(pool, key=lambda i: i["price"])
        else:
            pick = max(affordable, key=lambda i: (
                1 if "器材手册" in i["name"] else 0, i["price"]))

        if self.buy_stalled("商店", money, (pick["name"], pick["price"]),
                            counter=self.buy_counter("商店")):
            self.log_warning(
                f"商店：「{pick['name']}」连续 {self.BUY_RETRY_LIMIT} 次点了没成交"
                "（零用钱没变、卡片还在）→ 今天不再买，关店")
            self.reset_buy_counter(self.buy_counter("商店"))
            self.finish_shop(texts)
            return True

        self.click(pick["x"], pick["y"], after_sleep=0.6)
        # 右侧黄色购买条（实机相对位置 0.79, 0.84）
        self.click(int(self.width * 0.79), int(self.height * 0.84), after_sleep=0.8)
        self.log_info(f"商店：买「{pick['name']}」{pick['price']}（{pick['quality']}｜零用钱 {money}）")

        confirm = self.find_text(labels.BUTTONS["confirm"], region_name="confirm_bar")
        if confirm is not None:
            self.click_box(confirm, after_sleep=0.6)
            self.log_info("商店：确认购买")
        return True

    def finish_shop(self, texts=None):
        """关店。进店去重由周末首页的 `shop_checked` 负责（一天一次）。"""
        self.close_shop(texts)
        self._shop_spend_all = False

    def close_shop(self, texts=None):
        """点右上角 X 关商店。"""
        texts = texts or self._ocr_region()
        target = self.match_text(self.texts_in_region(texts, "top_right"), "X")
        if target is not None:
            # 2026-10-29 用户反馈「X 点得有点歪」：实测帧（docs/shots/2026-10-26-shop.png）
            # 里 OCR 的 X 框是 (1748,133,80,81)、中心 (1788,173)，但 X 字形的视觉中心
            # 在 y≈180（框中心偏下约 8% 框高）。所以往下压 10% 框高再点。
            cx = target.x + target.width / 2
            cy = target.y + target.height / 2 + target.height * 0.10
            self.click(int(cx), int(cy), after_sleep=0.8)
        else:
            self.click(int(self.width * 0.94), int(self.height * 0.11), after_sleep=0.8)
        self.log_info("商店：关闭")

    @staticmethod
    def _gacha_prices_ready(cards):
        """这组卡的价格都读到了吗？

        2026-11-01 15:09：卡面先渲染、价格后渲染。价格是 None 时买不了
        （买不起点「确定」会卡在扭蛋页反复点，见 handle_street 的注释），
        所以价格没出来就不能算「卡片已稳定」。
        """
        return bool(cards) and all(c.get("price") is not None for c in cards)

    @staticmethod
    def _gacha_signature(cards):
        """一组扭蛋卡的指纹：名字 + 价格 + 品质 + 队友标记。空组返回 None。

        2026-10-31 23:41 实跑（用户：「没有识别到中间的卡片有队友，又选择了左边
        的卡片」）：只比「名字 + 价格」不够 —— 卡片淡入期的半透明画面上，OCR 读
        出的名字和价格**完全正确**，但品质/队友是从透出来的背景取的：暖色店面被
        判成金卡、蓝色货架被判成队友标记。于是「两帧一致」立刻通过，左边的
        「运控增加」以金+队友 321 分和中间的「防守升级包++」打平，max() 按顺序
        取了左边。把品质和队友也放进指纹，画面没定下来时就会继续等。
        """
        if not cards:
            return None
        return tuple(sorted((c["name"], c["price"], c["quality"], bool(c["mate"]))
                            for c in cards))

    def settle_gacha_cards(self, texts):
        """读扭蛋三张卡，**等开场动画稳定**后再返回。

        2026-10-27 实测（第 4 局 10:07:52）：卡片飞入/翻转期间 `read_gacha_cards`
        也能返回 3 张卡（中间态），脚本 0.3 秒就点了「确定」，买下中间那张紫卡；
        1.7 秒后重进才看清同价位的金卡（防守增加++/80 币），等于白扔一次机会。
        所以连续两次识别一致才认账；空结果继续等（旧版只处理了"空"这一种）。
        同一组卡确认过就不再重复等，避免每拍多花 1.6 秒；一直不稳时
        最后还会多等一拍 1.2 秒（见函数末尾），最多约 3.6 秒定不下来才放弃。

        2026-11-01 15:09 新增：**价格还没渲染出来时不算「稳定」**。
        卡面文字先画出来、价格数字后画出来，那一拍卡是「读到了但价格是 None」；
        旧判据只看指纹（名字/价格/品质/队友），价格从 None 变成 80 这一下
        会被当成「变了」→ 继续等；但反过来「两拍都是 None」就等于认账了，
        于是拿不到价格、买不了。现在价格未出就**继续等**，直到价格出来。
        """
        cards = self.read_gacha_cards(texts)
        sig = self._gacha_signature(cards)
        prev_sig = getattr(self, "_gacha_sig", None)
        if sig and sig == prev_sig and time.monotonic() - getattr(self, "_gacha_at", 0.0) < 3.0:
            if self._gacha_prices_ready(cards):
                return cards
        last_cards, empty_streak = cards, 0
        for _ in range(3):
            self.sleep(0.8)
            try:
                self.next_frame()
            except Exception:                     # noqa: BLE001
                pass
            nxt = self.read_gacha_cards(self._ocr_region() or [])
            nsig = self._gacha_signature(nxt)
            if nsig and nsig == sig and self._gacha_prices_ready(nxt):
                if sig != prev_sig:
                    self.log_info("扭蛋：卡片已稳定（" + "、".join(
                        c["name"] for c in nxt) + "）")
                self._gacha_sig, self._gacha_at = nsig, time.monotonic()
                return nxt
            if nsig:
                last_cards, empty_streak = nxt, 0
            else:
                empty_streak += 1
                if empty_streak >= self.GACHA_EMPTY_LIMIT:
                    # 2026-11-01 15:09 实跑：卡面文字先画出来、价格数字后画出来，
                    # 旧判据「连续两拍读不到卡就退出」正好卡在这个**中间态**上：
                    # 19.5s 那拍卡面（训练增强/运控增加+++）已经在了，只是没有价格，
                    # 于是被判成「页面是空的」→ 退出 → 白丢一次扭蛋。
                    # 现在放宽到 4 拍（≈3.2 秒）；末尾还有「再等一拍 1.2 秒」的兜底。
                    self._gacha_sig, self._gacha_at = None, time.monotonic()
                    return []
            cards, sig = nxt, nsig
        # 3 拍都还在变（打折数字滚动 + 卡片淡入）：再等一拍**慢的**，用画面定下来
        # 以后的那一帧决定。快速循环里最后那一帧往往正好是淡入中间态
        # （2026-10-31 23:41 就是这么买错的，见 `_gacha_signature`）。
        self.sleep(1.2)
        try:
            self.next_frame()
        except Exception:                         # noqa: BLE001
            pass
        final = self.read_gacha_cards(self._ocr_region() or [])
        if final:
            if self._gacha_signature(final) != self._gacha_signature(last_cards):
                self.log_info("扭蛋：又等一拍（1.2 秒）画面才定下来（"
                              + "、".join(c["name"] for c in final) + "）")
            last_cards = final
        elif last_cards:
            self.log_warning("扭蛋：卡片一直在变（动画/刷新），按最后一次识别结果处理")
        self._gacha_sig = self._gacha_signature(last_cards)
        self._gacha_at = time.monotonic()
        return last_cards

    def handle_street(self, texts):
        """步行街扭蛋：**判断在 `training/places/street.py`**（地点模块）。

        这里只负责「认页面 → 问模块 → 照它说的点」。
        步行街模块只管自己的事（买哪张卡、试过没有），不需要知道别的地点。
        """
        if not self.street_page(texts):
            return False
        if not self.street_enabled():
            self.log_info("步行街扭蛋：未开启，退出")
            self.leave_street()
            return True

        cards = self.settle_gacha_cards(texts)
        # 2026-11-01 15:09 事故：`mark_visited("步行街")` 原来写在**读卡之前**，
        # 于是「进步行街 → 没读到卡 → 退出」也算「今天来过步行街」，
        # 回到地图只剩餐厅可去 → 白跑一趟外出（只赚了 110 零用钱）。
        #
        # 但**不能**简单地「没读到卡就不标记 visited」：那样回到地图时
        # todo 里还有步行街 → 又点进去 → 又失败 → 无限循环。
        # 所以分两件事（见 places/street.py 的说明）：
        #   - `visited`：今天**进过**步行街（进过就算来过，防止反复进）；
        #   - `gacha_tried`：今天**试过**抽扭蛋（不管买没买成）。
        # 地图上只按 `gacha_tried` 排除步行街（试过了就不再试），
        # 「买没买成」交给 gacha_spent 决定要不要去餐厅打工。
        self.mark_visited("步行街")
        self.mark_tried()
        if not cards:
            self.log_warning("扭蛋：三张卡都没认出来，退出不买")
            self.leave_street()
            return True

        bar = self.top_bar(texts)
        stamina = bar.get("stamina")
        stamina_max = bar.get("stamina_max") or 130
        money = self.read_money(texts)
        if money is None:
            # 读不到零用钱就不能判断买不买得起：点「确定」买不起时游戏不会扣钱，
            # 页面也不关，旧代码会一直「选卡 → 确定 → 没反应」（2026-10-26 实测）。
            self.log_warning("扭蛋：读不到零用钱，保守退出不买")
            self.leave_street()
            return True

        best, detail, _all = self.gacha_candidates(
            cards, stamina, stamina_max, money)
        if best is None:
            self.log_info(f"扭蛋：{detail} → 没有可买的"
                          f"（零用钱 {money}，体力 {stamina}），退出")
            self.leave_street()
            return True

        self.click(best["x"], best["y"], after_sleep=0.5)
        self.log_info(f"扭蛋：{detail} → 选「{best['name']}」"
                      f"（{best['quality']}，{best['price']}币，"
                      f"零用钱 {money}，体力 {stamina}）")

        if self.click_button("confirm", after_sleep=0.8, must=False):
            self.log_info("扭蛋：确定")
            # 今天花过钱了：扭蛋流周末不必再去餐厅打工（flow.weekend_priority 用）
            self.mark_spent()
        else:
            self.click(int(self.width * 0.5), int(self.height * 0.93), after_sleep=0.8)
            self.log_warning("扭蛋：没认出「确定」，按底部中间兜底")
        return True

    def handle_arcade(self, texts):
        """游戏厅：优先「跳过」已三星的小游戏；否则花钱进**带「自动」标记**的小游戏。

        界面（实拍 2026-10-27 18:34，客户区 1307x735）：
            请选择一个小游戏
            [自动 弹跳 普通] 奖励：弹跳20~70    打折 64
            [自动 运控 普通] 奖励：运控20~70    打折 64
            [自动 投射 困难] 奖励：投射30~100   打折 80
            [能力详情]   [确定]

        **「自动」两个字 = 已经打过的小游戏**（用户 2026-10-27 口径）：
        带它才能自动挑战/跳过；没有它的要真人打（打篮球、拼图），
        OCR 跟不上，进去就是白费一个周末日。所以只买带「自动」的卡片。

        选卡规则（2026-10-29 用户口径）：**有队友的优先**（卡片上挂着队友头像），
        同类里再按奖励上限高的优先；价格从页面读（「打折」下面那个数字）；
        读不到价格就按 100 处理，买得起才点「确定」。
        付完钱小游戏会**自己打完**（探针实测：零用钱 219→155，之后走普通剧情/结算），
        后续交给通用 handler 推进。
        """
        if self.match_text(texts, labels.ARCADE_TITLE) is None:
            return False
        self.mark_visited("游戏厅")
        skip = self.match_text(self.texts_in_region(texts, "weekend_options"), labels.ARCADE_SKIP)
        if skip is None:
            skip = self.match_text(texts, labels.ARCADE_SKIP)
        if skip is not None:
            self.click_box(skip, after_sleep=0.4)
            confirm = self.match_text(self._ocr_region("confirm_bar"), labels.CONFIRM_TEXTS)
            if confirm is not None:
                self.click_box(confirm, after_sleep=0.45)
            self.log_info("游戏厅：跳过已三星小游戏")
            return True

        if not self.config.get("游戏厅付费"):
            self.log_info("游戏厅：未开启付费，退出")
            self.exit_page()
            return True

        money = self.read_money(texts)
        icons = self.find_teammate_icons(0.0, 1.0)
        cards = []
        for t in texts:
            m = re.search(r"(投射|弹跳|防守|体能|运控|意识)\s*(\d+)\s*[~～]\s*(\d+)",
                          t.name)
            if m is None:
                continue
            cx = t.x + t.width / 2
            band = self.width * 0.12
            auto = any(o.name.strip() == "自动"
                       and abs((o.x + o.width / 2) - cx) < band
                       and o.y < t.y for o in texts)
            # 价格：**打折时会出现两个数字并且位置会偏**（用户 2026-10-27），
            # 所以不用固定坐标，扫「奖励下方 0.30h、同一列」的所有数字取最小值
            # （= 折后价）。OCR 还会把原价和现价粘成一个长串（「10080」），
            # 这种按 3 位切回来。
            prices = []
            for d in texts:
                v = d.name.strip()
                if not v.isdigit():
                    continue
                if abs((d.x + d.width / 2) - cx) > band:
                    continue
                if not (t.y < d.y < t.y + self.height * 0.30):
                    continue
                if len(v) <= 3:
                    prices.append(int(v))
                elif len(v) in (5, 6):
                    for k in range(0, len(v) - 2, 3):
                        prices.append(int(v[k:k + 3]))
            price = min(prices) if prices else None
            # 品质：取卡片「名字那一行 → 奖励那一行」之间的底色（用户 2026-10-27：
            # 只买金/紫，蓝卡和白卡不买）。名字框在同一列、奖励上方。
            name_box = next((o for o in texts
                             if o.name.strip() == m.group(1)
                             and abs((o.x + o.width / 2) - cx) < band
                             and o.y < t.y), None)
            y1f = ((name_box.y / self.height) if name_box is not None
                   else t.y / self.height - 0.10)
            q, qh, qs = self.card_quality_at(cx / self.width, y1f,
                                             t.y / self.height)
            # 队友：卡片上方/左上挂的橙色小头像（和餐厅打工同一套判据）。
            # 2026-10-29 实跑：中间那张带队友的卡没被选中 —— 旧逻辑只看奖励上限。
            mate = self.mates_on_card(t, icons) > 0
            cards.append({"attr": m.group(1), "hi": int(m.group(3)),
                          "box": t, "auto": auto, "price": price,
                          "quality": q, "hue": qh, "sat": qs, "mate": mate})
            self.log_debug(f"游戏厅卡片：{m.group(1)} 奖励{m.group(2)}~{m.group(3)}"
                           f" 自动={auto} 队友={mate} 品质={q}(H={qh:.0f} S={qs:.0f})"
                           f" 价格={price}")

        ok_cards = [c for c in cards if c["auto"] and c["quality"] in ("金", "紫")]
        if not ok_cards:
            self.log_warning("游戏厅：没有「自动」+ 金/紫的小游戏（"
                             + "、".join(f"{c['attr']}/{c['quality']}"
                                        f"{'/自动' if c['auto'] else ''}"
                                        for c in cards) + "），退出")
            self.exit_page()
            return True
        auto_cards = ok_cards

        # 有队友的优先，其余按奖励上限从高到低（2026-10-29 口径）
        auto_cards.sort(key=lambda c: (0 if c["mate"] else 1, -c["hi"]))
        pick = None
        for c in auto_cards:
            price = c["price"]
            if price == 0:
                # 免费小游戏（白神时久那类 0 元商品）：不花钱，钱读不到也能买
                pick = c
                break
            if price is None:
                continue                    # 价格读不到：不猜
            if money is None:
                continue                    # 钱读不到就不买，盲买就是 2026-10-29 的死循环
            if money >= price:
                pick = c
                break
        confirm = self.exact_text(texts, labels.BUTTONS["confirm"])
        if pick is not None and confirm is not None:
            if self.buy_stalled("游戏厅", money, (pick["attr"], pick["price"]),
                                counter=self.buy_counter("游戏厅")):
                self.log_warning(
                    f"游戏厅：「{pick['attr']}」连续 {self.BUY_RETRY_LIMIT} 次点了没成交"
                    "（零用钱没变、卡片还在）→ 今天不再买，退出")
                self.reset_buy_counter(self.buy_counter("游戏厅"))
                self.exit_page()
                return True
            self.click_box(pick["box"], after_sleep=0.4)
            self.click_box(confirm, after_sleep=1.0)
            # 小游戏会自己打完（用户 2026-10-29 口径），中间只有一段没有按钮的
            # 动画：交给 run() 的等待分支，不要点、也不要让卡死看门狗误判。
            self._arcade_wait_since = time.time()
            self.log_info(f"游戏厅：买了「{pick['attr']}」小游戏"
                          f"（奖励上限 {pick['hi']}，价格 {pick['price']}，"
                          f"零用钱 {money}，队友={'有' if pick['mate'] else '无'}），"
                          "等它自己打完")
            return True

        if money is None:
            self.log_warning("游戏厅：零用钱读不到，先不买（避免盲买）→ 退出")
        elif all(c["price"] is None for c in auto_cards):
            self.log_warning("游戏厅：卡片价格都没读出来，先不买（不猜）→ 退出")
        else:
            self.log_warning(f"游戏厅：带「自动」的都买不起（零用钱 {money}），退出")
        self.exit_page()
        return True

    def handle_go_out(self, texts):
        """周末首页：点「出行」进地图。

        实机（2026-10-25 周六）：出行页只有「出行」两个字，没有任何地点名，
        所以 handle_weekend 认不出来，必须先点这一下。

        2026-10-29 口径：周末首页**不再进店**（进店挪到「比赛日进比赛前」，
        见 `training.handle_pre_match_shop`），这里只负责点出行。

        **判断在 `training/daycycle.py`**（与训练日那个入口同级）：
        `outing_offer(..., block_if_used=False)` —— 周末出行是**主流程**，
        不是野上泷周五那种「额外外出」，所以**不受** `outing_done` 限制。
        """
        # 地图上没有「出行」按钮（2026-10-27 修正）：旧判据是「有地点名就是地图」，
        # 但周末首页的台词也含地点名（「告诉她公园的草坪是假的」），
        # 结果首页被判成地图、永远不点「出行」。现在统一用「有出行 = 首页」。
        if self.match_text(texts, "出行") is None:
            return False

        # 训练日里的额外出行（野上泷：周五也能出行）：一天只用一次。
        # 用过之后如果又停在一个「有出行」的页面（进地图又退回来的首页），
        # 再点就是「出行 → 地图 → 退回首页」打转，所以这里直接退出去。
        # 注意：**只对非周末生效** —— 周末出行是主流程，每次进地图都要点。
        st = self.day_state()
        day = st["date"][1] if st["date"] else None
        if st.get("outing_done") and day not in ("六", "日"):
            self.exit_page()
            self.log_info("训练日的额外出行已经用过 → 返回主界面")
            return True

        # 2026-10-30 用户截图（野上泷周五额外周末）：对话框里有一句
        # 「今天可以出行哦！要不…」，click_text("出行") 会命中这句**台词**
        # （日志 `left_click 今天可以出行哦！要不 (950, 182)`），不是右侧按钮。
        # 2026-10-31：判据收进 parsers.find_go_out_button（只认整词「出行」，
        # 多个候选时优先右下按钮区），训练日那边也用它，别各写一份。
        action, reason = self.outing_offer(texts, block_if_used=False)
        if action == "点按钮":
            target = self.find_go_out_button(texts)
            self.click_box(target, after_sleep=0.6)
            self.log_info(f"周末：点「出行」进地图"
                          f"@({target.x + target.width // 2},"
                          f"{target.y + target.height // 2})")
        elif self.click_text("出行", region_name="go_out", after_sleep=0.6,
                             must=False):
            # 兜底：按钮上除了「出行」还带了箭头之类的字，整词匹配不到
            self.log_info("周末：点「出行」进地图（按钮区兜底）")
        else:
            # 只有台词、没有按钮：这一拍什么都不点，等对话先推进。
            # 注意：这里**不点台词**（训练日那个入口才点）——
            # 周末首页的台词不是按钮，点它会点到句子中间（2026-10-30 实跑）。
            return False
        return True

    def venue_on_discount(self, texts, label_box):
        """地点名字**左边**有没有红色大号「打折」字样。

        用户 2026-10-30：打折标记是地点名左侧的红色大号文字「打折」。
        用 OCR 文本判（游戏里这两个字够大，OCR 能读到——2026-10-30 01:32 的
        handle_weekend 事件里就出现了「打折」）；位置要求在同一行、且在名字左边，
        避免把别处的「打折」算进来。
        """
        if label_box is None:
            return False
        cx = label_box.x + label_box.width / 2
        cy = label_box.y + label_box.height / 2
        for t in texts or []:
            name = t.name.strip()
            if "打折" not in name:
                continue
            tcx = t.x + t.width / 2
            tcy = t.y + t.height / 2
            if tcx < cx and abs(tcy - cy) <= self.height * 0.12:
                return True
        return False

    def _venue_box(self, texts, place):
        """地点名对应的文本框（整词优先，其次 ≤4 字的短框）。"""
        box = self.exact_text(texts, (place,))
        if box is not None:
            return box
        return next((t for t in texts
                     if place in t.name and len(t.name.strip()) <= 4), None)

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

    def handle_weekend(self, texts):
        """周末地图：按「星期 + 流派」去地点。

        用户 2026-10-29 口径（见 flow.weekend_priority / constants.WEEKEND_PLAN）：
        - 周六：训练流原设定（步行街 → 游戏厅 → 公园）；其他流派去餐厅打工；
        - 周日：训练流→公园、商店流→餐厅、扭蛋流→步行街→餐厅、小游戏流→游戏厅→餐厅。
        `允许进步行街 / 允许进游戏厅` 没开时对应地点会被过滤掉，退化成公园。
        """
        # 周末首页有「出行」大按钮，地图上没有（2026-10-27 核对 run2 地图页三帧 OCR：
        # 地图只有「公园/公园+」「游戏厅/属性+」「步行街/零用钱+」，无「出行」）。
        # 台词常含地点名（「告诉她公园的草坪是假的」「游戏厅地狱挑战开始了！」），
        # 只靠地点名会把首页当地图：点中台词 → 误 mark_visited → todo 空 → 点右上角。
        if self.match_text(texts, "出行") is not None:
            return False
        if not self.match_text(texts, labels.WEEKEND_PLACES):
            return False
        st = self.day_state()
        visited = st.setdefault("visited", set())
        day = st["date"][1] if st["date"] else None

        # 周六的训练流 + 周日公园：地点完全由 WEEKEND_PLAN 表决定
        places = self.weekend_priority(day, st)

        # 今天已经去过的地方不再进：游戏厅没有可跳过的小游戏时会退回地图，
        # 不记录「已去过」就会来回进出游戏厅（2026-10-26 实训）。
        todo = [p for p in places if p not in visited]
        # 2026-11-01 15:09：步行街**试过**（不管买没买成）就不再进第二次。
        # 只按 `visited` 排除不够：买不到时如果不记 visited，会出现
        # 「进步行街 → 失败 → 回地图 → 又点步行街」的无限循环。
        # 买没买成由 `gacha_spent` 决定（买成了就不去餐厅打工，没买成就去）。
        tried = st.setdefault("gacha_tried", set())
        if "步行街" in tried and "步行街" in todo:
            todo.remove("步行街")
        if not todo:
            # 地点都去过：停在周末地图上什么都不会发生（2026-10-26 卡死：
            # 地图被 story_arrow 当成剧情页连点 ▼，反复 50 秒）。
            # 点右上角返回周末首页，让当天结算。
            self.log_info(f"周末：{sorted(visited)} 都去过了 → 返回")
            self.exit_page()
            return True

        self.log_info(f"周末{'日' if day == '日' else '六'}：待去 {todo}（已去 {sorted(visited)}）")
        todo = self.prefer_mate_venue(texts, todo, day)
        for place in todo:
            # 只认「公园」「餐厅」这种**整框就是地点名**的按钮。
            # 2026-10-27 事故：地图页有对白「告诉她公园的草坪是假的」「要去野餐」，
            # match_text 的包含匹配会命中长句，click_box 点到句子中间（图上什么都没发生），
            # 下一拍又判「都去过了」→ 返回死循环。
            target = self.exact_text(texts, (place,))
            if target is None:
                target = next((t for t in texts
                               if place in t.name and len(t.name.strip()) <= 4), None)
            if target:
                # 不在这里记 visited：要等地点的内页 handler 真认到标题才登记，
                # 否则「点了没打开」也会被当成去过（2026-10-26 公园没打开就被跳过）。
                self.click_box(target, after_sleep=0.5)
                self.log_info(f"周末点「{place}」（{self.style}）")
                return True
        return False
