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

from src.tasks.ui import Defs  # noqa: F401
from src.tasks.training.constants import (JPEG_PARAMS, PROJECT_ROOT,
                                          WORK_STAMINA_COST)  # noqa: F401


class WeekendHandlers:

    # 地点详情弹窗的「前往XXX」：公园点「划船」只是打开详情，还要再点这个。
    ACTIVITY_GO = re.compile(r"^前往(划船|野餐|散步|当服务员|桌游GM|游戏厅|商店|步行街|餐厅)")

    def handle_activity_go(self, texts):
        """地点详情弹窗：右下「前往划船 / 前往野餐」才是真正进入。

        坑（2026-10-26）：公园点「划船」只弹出详情（推荐属性 / 随机属性+10 /
        前往划船），必须再点「前往划船」。旧流程没有这个二级确认，
        于是停在详情页、每拍点屏幕中央都没反应，最后被卡死检测停掉。
        """
        target = self.match_text(texts, self.ACTIVITY_GO)
        if target is None:
            return False
        self.click_box(target, after_sleep=0.9)
        self.log_info(f"地点确认：{target.name.strip()}")
        return True

    def choose_option(self, texts, names, after_sleep=0.4):
        """在周末子页面的选项区里选一个，并处理可能的「确定」。

        确定按钮位置不固定（扭蛋页在底部中间，餐厅/公园可能在别处），
        先看底部条，再看整屏，避免只在固定区域找不到就反复点同一张卡。

        坑（2026-10-26 实机）：公园的「划船」图标上盖了一个
        「划船比赛！」活动标签，子串匹配先命中的是这个标签，点它毫无反应。
        所以这里**先整词匹配**，找不到再用子串，并且排除角标文字。
        """
        opts = self.texts_in_region(texts, "weekend_options")
        opts = [t for t in opts if not self.is_decor(t.name)]

        target = None
        for name in names:
            target = next((t for t in opts if t.name.strip() == name), None)
            if target is not None:
                break
        if target is None:
            plain = [t for t in opts if not self.is_label(t.name)]
            target = self.match_text(plain, names)
            if target is not None and self.is_label(target.name):
                target = None
        if target is None:
            return False
        self.click_box(target, after_sleep=after_sleep)
        self.log_info(f"选择「{target.name}」")

        confirm = self.find_text(("确定", "确认"))
        if confirm is not None:
            self.click_box(confirm, after_sleep=0.45)
            self.log_info(f"确定 @({confirm.x},{confirm.y})")
        return True

    # 购买没成交的保护：同一件东西连点这么多次还没变化，就认为点不动。
    # 用户 2026-10-29 口径：**不要按次数限制购买** —— 羽漱川湊能买两次、
    # 卧云真昼能买到升级版、白神时久多一件免费商品，商店/游戏厅/步行街
    # 一天买几件都可能是正常的。要防的是「点了没成交还一直点」。
    BUY_RETRY_LIMIT = 3

    def buy_stalled(self, scene, money, item):
        """判断「这一下购买有没有成交」。

        成交一定会让页面状态变（零用钱变少、卡片消失、剩余数变化），
        所以只盯 **同一场景 + 零用钱没变 + 还是同一件（名字+价格）** 连续出现几次。
        零用钱变了 = 上一次真花钱了，计数自然重置 → 想买几次买几次。
        """
        key = (scene, money, item[0], item[1])
        same = getattr(self, "_buy_key", None) == key
        tries = (getattr(self, "_buy_tries", 0) + 1) if same else 1
        self._buy_key, self._buy_tries = key, tries
        return tries >= self.BUY_RETRY_LIMIT

    def mark_visited(self, place):
        """地点内页真的打开后才登记「今天去过」。

        2026-10-26 用户反馈：在地图上点了「公园」，页面其实没打开，
        但旧代码点击前就 `visited.add(place)`，结果整天的公园都被跳过。
        现在只在各地点 handler 识别到自己的内页标题时登记。
        这里直接写 `_weekend`，不走 `weekend_state()` —— 后者按顶栏日期判断，
        地点内页如果读不到顶栏会重置整个状态，把 visited 清空。
        """
        self._weekend.setdefault("visited", set()).add(place)

    def leave_street(self, after_sleep=0.5):
        """退出扭蛋页：先找文字退路，再点右上角 ↩，最后才用底部兜底。

        2026-10-26 实测（1349x759）：**买完卡的详情页**底部有「放弃」，
        点它立即回到周末首页，「放弃」中心约 (0.346w, 0.896h)。

        2026-10-27 实测（logs/debug/1027-000428_nochange3）：第一次进店的
        「请选择1个扭蛋」页底部只有「能力详情 / 确定」，**没有「放弃」**，
        旧兜底坐标 (0.346w, 0.896h) 正好落在两个按钮中间的空白，
        连续 9 拍点不动 → 任务被判「连续 3 轮点不动」停止。
        这一页右上角 ↩ (0.957w, 0.047h) 实测有效，退回周末地图页。
        """
        if self.click_text(("放弃", "取消", "关闭"), after_sleep=after_sleep, must=False):
            return
        self.click(int(self.width * 0.957), int(self.height * 0.047), after_sleep=after_sleep)

    def handle_restaurant(self, texts):
        """餐厅内页：打工二选一（当服务员 / 桌游GM）。

        用户 2026-10-29 口径（**通用**，不分流派）：
        1. 队友多的优先 —— 卡片**左上角**挂着一排队友小头像，数头像个数；
        2. 队友一样多 → 选**右边**那张（消耗精力的）；
        3. 精力不够右边那张 → 改选**左边**（不消耗精力）。
        """
        if self.match_text(texts, Defs.RESTAURANT_TITLE) is None:
            return False
        self.mark_visited("餐厅")
        return self.choose_option(texts, self.restaurant_order(texts))

    def restaurant_order(self, texts):
        """打工两张卡的点击顺序：**能选的优先** → 队友多 → 右边（耗精力）。

        判据（用户 2026-10-29）：
        - 先看这张卡现在**能不能选**（`option_selectable`）：界面提示不可用、
          或精力不够它标的消耗 → 不可选；
        - 可选的两张里：队友多（卡片左上角的小头像数）优先，一样多选右边；
        - 都不可选就按「右边优先」原样返回，让游戏自己拦（下一拍会重判）。
        右边那张默认按 `WORK_STAMINA_COST`（25）算消耗、左边按 0（不消耗），
        卡片上写明消耗时以卡片为准。
        ⚠ 餐厅内页没有实测帧，第一次实跑要看日志里两张卡的队友数/可选性。
        """
        opts = self.texts_in_region(texts, "weekend_options")
        found = []
        for name in Defs.RESTAURANT_OPTIONS:
            box = self.exact_text(opts, (name,)) or self.match_text(opts, (name,))
            if box is not None:
                found.append(box)
        if not found:
            return Defs.RESTAURANT_OPTIONS

        stamina = self.top_bar().get("stamina")
        icons = self.find_teammate_icons(0.0, 1.0)
        scored = [(self.mates_on_card(b, icons), b) for b in found]
        scored.sort(key=lambda pair: (-pair[0], -pair[1].x))   # 队友多 → 右边
        ranked = [b for _n, b in scored]
        right = max(found, key=lambda b: b.x)
        usable = [b for b in ranked
                  if self.option_selectable(
                      b, texts, stamina,
                      WORK_STAMINA_COST if b is right else 0)]
        detail = "、".join(
            f"{b.name}={n}人{'' if b in usable else '（不可选）'}" for n, b in scored)
        self.log_info(f"餐厅：{detail}｜精力 {stamina} → "
                      + ("按可选/队友多/右边选" if usable else "都不可选，按右边优先试"))
        return tuple(b.name for b in (usable or ranked))

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
            if any(k in s for k in Defs.GACHA_CLEAR_EXCLUDE) or "+" in s:
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
            self.click_text(("放弃",), after_sleep=0.6, must=False)
            return True
        # 用户口径：买得起的里**有队友推荐（头顶气泡）优先**，同档价格高的优先。
        pick, price, marks = max(
            affordable, key=lambda x: (x[2], x[1] if x[1] is not None else -1))
        badge = f"·队友{marks}" if marks else ""
        cx = pick.x + pick.width / 2
        cy = pick.y + self.height * 0.18
        self.click(int(cx), int(cy), after_sleep=0.6)
        self.log_info(f"炒股：零用钱 {money}｜{detail} → 选「{pick.name}」({price}){badge}")
        if self.click_text(("确定",), after_sleep=0.8, must=False):
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
            if self.click_text(("下一步",), after_sleep=0.7, must=False):
                self.log_info("炒股：收益排名 → 下一步")
                return True
        return False

    def handle_park(self, texts):
        """公园内页：散步（零用钱+）/ 划船（属性+）/ 野餐（精力++）。

        实机（2026-10-26 debug 包）：三个**圆形图标**按钮，名字和效果都在图标下方
        （「划船 属性+」），页顶有「在公园干点什么好呢?」。
        旧判据 `Defs.PARK_TITLE = ("在公园", ...)` 依赖「在公园」三个字，
        但实际文案是「在公园干点什么好呢?」整句 —— 整句 OCR 时「在公园」不是独立框，
        match_text 的子串匹配靠"整段拼接"兜底才能命中，命中后返回的是**最长的框**
        （整句话），click_box 会点到句子中间，什么都点不到 → 卡死。

        现在改成：标题只当**判据**（有「公园」相关字样 + 有三个圆形选项就处理），
        实际点击永远走选项文字框（散步/划船/野餐）。
        """
        # 周末首页/地图上有「出行」按钮，公园内页没有（与 handle_shop 同一招）。
        # 2026-10-27 事故：地图页的对白「小澪奈总是吵着要去野餐…」包含「野餐」，
        # Defs.PARK_TITLE 里的「野餐」直接把地图页判成公园内页 →
        # mark_visited("公园") 后 handle_weekend 认为「都去过了」，
        # 反复点返回循环 9 拍，任务被判卡死停止。
        if self.match_text(texts, "出行") is not None:
            return False
        if self.match_text(texts, Defs.PARK_TITLE) is None:
            return False

        if self.style != "训练流":
            pref = self.config.get("公园选项", "野餐")
            order = (pref,) + tuple(x for x in Defs.PARK_OPTIONS if x != pref)
            ok = self.choose_option(texts, order)
            if ok:
                self.mark_visited("公园")
            return ok

        st = self.weekend_state()
        day = st["date"][1] if st["date"] else None
        if st.get("park_choice") is None:
            if day == "日":
                stamina = self.top_bar().get("stamina")
                threshold = int(self.config.get("周末野餐阈值", 60))
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
            x for x in Defs.PARK_OPTIONS if x != st["park_choice"])
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

        policy = self.config.get("购买策略", "只买金和紫")
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

        if self.buy_stalled("商店", money, (pick["name"], pick["price"])):
            self.log_warning(
                f"商店：「{pick['name']}」连续 {self.BUY_RETRY_LIMIT} 次点了没成交"
                "（零用钱没变、卡片还在）→ 今天不再买，关店")
            self._buy_key = None
            self.finish_shop(texts)
            return True

        self.click(pick["x"], pick["y"], after_sleep=0.6)
        # 右侧黄色购买条（实机相对位置 0.79, 0.84）
        self.click(int(self.width * 0.79), int(self.height * 0.84), after_sleep=0.8)
        self.log_info(f"商店：买「{pick['name']}」{pick['price']}（{pick['quality']}｜零用钱 {money}）")

        confirm = self.find_text(("确定", "确认"), region_name="confirm_bar")
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
    def _gacha_signature(cards):
        """一组扭蛋卡的指纹：名字 + 价格。空组返回 None。"""
        if not cards:
            return None
        return tuple(sorted((c["name"], c["price"]) for c in cards))

    def settle_gacha_cards(self, texts):
        """读扭蛋三张卡，**等开场动画稳定**后再返回。

        2026-10-27 实测（第 4 局 10:07:52）：卡片飞入/翻转期间 `read_gacha_cards`
        也能返回 3 张卡（中间态），脚本 0.3 秒就点了「确定」，买下中间那张紫卡；
        1.7 秒后重进才看清同价位的金卡（防守增加++/80 币），等于白扔一次机会。
        所以连续两次识别一致才认账；空结果继续等（旧版只处理了"空"这一种）。
        同一组卡确认过就不再重复等，避免每拍多花 1.6 秒。
        """
        cards = self.read_gacha_cards(texts)
        sig = self._gacha_signature(cards)
        prev_sig = getattr(self, "_gacha_sig", None)
        if sig and sig == prev_sig and time.monotonic() - getattr(self, "_gacha_at", 0.0) < 3.0:
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
            if nsig and nsig == sig:
                if sig != prev_sig:
                    self.log_info("扭蛋：卡片已稳定（" + "、".join(
                        c["name"] for c in nxt) + "）")
                self._gacha_sig, self._gacha_at = nsig, time.monotonic()
                return nxt
            if nsig:
                last_cards, empty_streak = nxt, 0
            else:
                empty_streak += 1
                if empty_streak >= 2:
                    # 卡片彻底消失（页面被关掉/还在飞入）：不要把上一帧的中间态当真
                    self._gacha_sig, self._gacha_at = None, time.monotonic()
                    return []
            cards, sig = nxt, nsig
        if last_cards:
            self.log_warning("扭蛋：卡片一直在变（动画/刷新），按最后一次识别结果处理")
        self._gacha_sig = self._gacha_signature(last_cards)
        self._gacha_at = time.monotonic()
        return last_cards

    def handle_street(self, texts):
        """步行街扭蛋：只买金/紫，队友优先，体力低才允许清空精力的卡。

        用户规则（2026-10-26）：
        1. 只买黄色/紫色品质；蓝色、其他一律不买（宁可退出）；
        2. 同品质有队友加成的优先；
        3. 「消耗/清空精力」的卡：只有体力低于阈值（默认 30）才买；
        4. 没有合格卡就点右上角返回，不点确定。

        2026-10-26 事故：旧版只按 rank*10 打分，蓝卡也有 10 分，
        结果 72 体力买了「精力转换-意识（消耗所有精力）」，体力归 0，
        周日只能划船回 30。现在清空卡在体力够时必须排除。
        """
        if self.match_text(texts, Defs.STREET_TITLE) is None:
            return False
        self.mark_visited("步行街")
        if not self.config.get("允许进步行街", True):
            self.log_info("步行街扭蛋：未开启，退出")
            self.leave_street()
            return True

        cards = self.settle_gacha_cards(texts)
        if not cards:
            self.log_warning("扭蛋：三张卡都没认出来，退出不买")
            self.leave_street()
            return True

        bar = self.top_bar()
        stamina = bar.get("stamina")
        stamina_max = bar.get("stamina_max") or 130
        money = self.read_money(texts)
        if money is None:
            # 读不到零用钱就不能判断买不买得起：点「确定」买不起时游戏不会扣钱，
            # 页面也不关，旧代码会一直「选卡 → 确定 → 没反应」（2026-10-26 实测）。
            self.log_warning("扭蛋：读不到零用钱，保守退出不买")
            self.leave_street()
            return True
        clear_limit = int(self.config.get("扭蛋清空体力阈值", 30))
        allow_clear = stamina is not None and stamina < clear_limit

        def score(c):
            # 2026-10-30 用户口径（扭蛋增强界面）：**品质 > 增强款 > 队友**。
            # 金 300 / 紫 200 / 蓝 100 的间隔远大于后两项，保证「优先金/紫」；
            # 同品质里先看卡片右上角「增强款」斜标签，再看左上角队友标记。
            base = {"金": 300, "紫": 200, "蓝": 100}.get(c["quality"], 0)
            s = base + (30 if c.get("enhanced") else 0) + (20 if c["mate"] else 0)
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
            elif c["quality"] not in ("金", "紫"):
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
            f"{'·清空' if c['clear'] else ''}/{c['price']}币/{score(c)})" for c in cards)
        if not ok:
            self.log_info(f"扭蛋：{detail} → 没有可买的"
                          f"（零用钱 {money}，体力 {stamina}），退出")
            self.leave_street()
            return True

        best = max(ok, key=lambda c: c["score"])
        self.click(best["x"], best["y"], after_sleep=0.5)
        self.log_info(f"扭蛋：{detail} → 选「{best['name']}」"
                      f"（{best['price']}币，零用钱 {money}，体力 {stamina}）")

        if self.click_text(("确定",), after_sleep=0.8, must=False):
            self.log_info("扭蛋：确定")
            # 今天花过钱了：扭蛋流周末不必再去餐厅打工（flow.weekend_priority 用）
            self.weekend_state()["gacha_spent"] = True
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
        if self.match_text(texts, Defs.ARCADE_TITLE) is None:
            return False
        self.mark_visited("游戏厅")
        skip = self.match_text(self.texts_in_region(texts, "weekend_options"), Defs.ARCADE_SKIP)
        if skip is None:
            skip = self.match_text(texts, Defs.ARCADE_SKIP)
        if skip is not None:
            self.click_box(skip, after_sleep=0.4)
            confirm = self.match_text(self._ocr_region("confirm_bar"), Defs.CONFIRM_TEXTS)
            if confirm is not None:
                self.click_box(confirm, after_sleep=0.45)
            self.log_info("游戏厅：跳过已三星小游戏")
            return True

        if not self.config.get("游戏厅付费", True):
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
        confirm = self.exact_text(texts, ("确定",))
        if pick is not None and confirm is not None:
            if self.buy_stalled("游戏厅", money, (pick["attr"], pick["price"])):
                self.log_warning(
                    f"游戏厅：「{pick['attr']}」连续 {self.BUY_RETRY_LIMIT} 次点了没成交"
                    "（零用钱没变、卡片还在）→ 今天不再买，退出")
                self._buy_key = None
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
        """
        # 地图上没有「出行」按钮（2026-10-27 修正）：旧判据是「有地点名就是地图」，
        # 但周末首页的台词也含地点名（「告诉她公园的草坪是假的」），
        # 结果首页被判成地图、永远不点「出行」。现在统一用「有出行 = 首页」。
        if self.match_text(texts, "出行") is None:
            return False

        # 训练日里的额外出行（野上泷：周五也能出行）：一天只用一次。
        # 用过之后如果又停在一个「有出行」的页面（进地图又退回来的首页），
        # 再点就是「出行 → 地图 → 退回首页」打转，所以这里直接退出去。
        st = self.weekend_state()
        day = st["date"][1] if st["date"] else None
        if st.get("outing_done") and day not in ("六", "日"):
            self.exit_page()
            self.log_info("训练日的额外出行已经用过 → 返回主界面")
            return True

        # 2026-10-30 用户截图（野上泷周五额外周末）：对话框里有一句
        # 「今天可以出行哦！要不…」，click_text("出行") 会命中这句**台词**
        # （日志 `left_click 今天可以出行哦！要不 (950, 182)`），不是右侧按钮。
        # 所以先找右侧（x>0.75w）、整词的「出行」，找不到才退回文字匹配。
        target = next((t for t in texts if t.name.strip() == "出行"
                       and (t.x + t.width / 2) / self.width > 0.75), None)
        if target is not None:
            self.click_box(target, after_sleep=0.6)
            self.log_info("周末：点「出行」进地图"
                          f"@({target.x + target.width // 2},"
                          f"{target.y + target.height // 2})")
        else:
            self.click_text("出行", after_sleep=0.6)
            self.log_info("周末：点「出行」进地图（按文字框兜底）")
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
            if self.match_text(texts, ("扭蛋增强", "扭蛋加强")):
                self.log_info("周末地点：步行街有「扭蛋增强！」"
                              "→ 不管队友，先进步行街")
                return ["步行街"] + [p for p in places if p != "步行街"]
            # 用户 2026-10-30 完整口径（扭蛋流的周五/周六/周日）：
            #   1. 两边都有队友 → 先进步行街；
            #   2. 只有一边有队友 → 去有队友的那边；
            #   3. 都没队友 → 周五/周六去餐厅赚钱，周日去步行街消费。
            if len(mates) == 2:
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
        if not self.match_text(texts, Defs.WEEKEND_PLACES):
            return False
        st = self.weekend_state()
        visited = st.setdefault("visited", set())
        day = st["date"][1] if st["date"] else None

        # 周六的训练流 + 周日公园：地点完全由 WEEKEND_PLAN 表决定
        places = self.weekend_priority(day, st)

        # 今天已经去过的地方不再进：游戏厅没有可跳过的小游戏时会退回地图，
        # 不记录「已去过」就会来回进出游戏厅（2026-10-26 实训）。
        todo = [p for p in places if p not in visited]
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
