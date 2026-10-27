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
from src.tasks.training.constants import JPEG_PARAMS  # noqa: F401


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
        """餐厅内页：桌游GM（零用钱+）/ 当服务员（零用钱++）。"""
        if self.match_text(texts, Defs.RESTAURANT_TITLE) is None:
            return False
        self.mark_visited("餐厅")
        pref = self.config.get("餐厅选项", "当服务员")
        order = (pref,) + tuple(x for x in Defs.RESTAURANT_OPTIONS if x != pref)
        return self.choose_option(texts, order)

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

        policy = self.config.get("购买策略", "只买金和紫")
        money = self.read_money(texts)
        items = self.read_shop_items(texts)
        threshold = int(self.config.get("商店进店余额", 300))
        self.log_info("商店：" + "、".join(
            f"{i['name']}({i['price']}/{i['quality']})" for i in items)
            + f"｜零用钱 {money}｜策略 {policy}"
            + ("｜赛前清空" if self._shop_spend_all else ""))

        # 平时余额低于门槛就不该待在店里（周末商店流等入口可能绕过进店检查）
        if (not self._shop_spend_all and money is not None and money < threshold
                and policy != "不买"):
            self.log_info(f"商店：零用钱 {money} < {threshold}，不买，关店")
            self.finish_shop(texts)
            return True

        if policy == "不买":
            self.log_info("商店：策略是只逛不买，退出")
            self.finish_shop(texts)
            return True

        affordable = [i for i in items if money is not None and i["price"] <= money]
        if not affordable:
            self.log_info("商店：没有买得起的商品了，关店")
            self.finish_shop(texts)
            return True

        if policy == "只买金和紫":
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
        """关店 + 记下「这一天已经逛过」，避免关掉后又被点进去。"""
        self.close_shop(texts)
        if self._shop_key is not None:
            self._shop_day = self._shop_key
        self._shop_spend_all = False

    def close_shop(self, texts=None):
        """点右上角 X 关商店。"""
        texts = texts or self._ocr_region()
        target = self.match_text(self.texts_in_region(texts, "top_right"), "X")
        if target is not None:
            self.click_box(target, after_sleep=0.8)
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
            base = {"金": 100, "紫": 60, "蓝": 20}.get(c["quality"], 0)
            s = base + (40 if c["mate"] else 0) + (1 if "+" in c["name"] else 0)
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
        else:
            self.click(int(self.width * 0.5), int(self.height * 0.93), after_sleep=0.8)
            self.log_warning("扭蛋：没认出「确定」，按底部中间兜底")
        return True

    def handle_arcade(self, texts):
        """游戏厅：只处理「已三星 → 可跳过」的小游戏。

        「强化」里解锁后，游戏厅可以跳过已三星的游戏，但仍会触发地狱游戏。
        没三星的小游戏需要真人打（打篮球/拼图），OCR 跟不上，遇到就立刻退出，
        避免白费一个周末日。
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
        self.log_warning("游戏厅：没有可跳过的小游戏（未三星），点右上角返回")
        # 游戏厅选择页没有「跳过」时，back() 点左上角无效（2026-10-26 卡死），
        # 返回箭头在右上角，和商店/公园一致。
        self.exit_page()
        return True

    def handle_go_out(self, texts):
        """周末首页：周六先点「商店」看一眼，再点「出行」进地图。

        实机（2026-10-25 周六）：出行页只有「出行」两个字，没有任何地点名，
        所以 handle_weekend 认不出来，必须先点这一下。
        """
        # 地图上没有「出行」按钮（2026-10-27 修正）：旧判据是「有地点名就是地图」，
        # 但周末首页的台词也含地点名（「告诉她公园的草坪是假的」），
        # 结果首页被判成地图、永远不点「出行」。现在统一用「有出行 = 首页」。
        if self.match_text(texts, "出行") is None:
            return False

        # 商店只有「商店流」才进（用户 2026-10-26：训练流的钱优先换步行街/游戏厅的黄色道具）
        if self.style == "商店流":
            st = self.weekend_state()
            shop = self.match_text(texts, "商店")
            # 周六、周日首页都有商店入口（用户 2026-10-27 确认），两天各进一次。
            # weekend_state 按「第N周+星期X」重置，shop_checked 天然就是"每天一次"；
            # 旧版用 `day != "日"` 把周日挡掉了，等于每周少逛一次店。
            if shop is not None and not st["shop_checked"]:
                st["shop_checked"] = True
                self.click_box(shop, after_sleep=0.9)
                self.log_info("周末：商店流，先去商店")
                return True

        self.click_text("出行", after_sleep=0.6)
        self.log_info("周末：点「出行」进地图")
        return True

    def handle_weekend(self, texts):
        """周末地图：按流派顺序去地点。

        训练流（用户 2026-10-26）：周六先去步行街，其次游戏厅，
        找「有队友加成的黄色道具」；周日再回公园补精力。
        `允许进步行街 / 允许进游戏厅` 没开时这两个地点会被过滤掉，退化成公园。
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

        places = self.weekend_priority()
        if (self.style == "训练流"
                and self.config.get("周末第一天优先资源点", True)
                and day == "日"):
            places = ["公园"]

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
