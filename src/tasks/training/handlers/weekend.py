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

    def handle_restaurant(self, texts):
        """餐厅内页：桌游GM（零用钱+）/ 当服务员（零用钱++）。"""
        if self.match_text(texts, Defs.RESTAURANT_TITLE) is None:
            return False
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
        if self.match_text(texts, Defs.PARK_TITLE) is None:
            return False

        if self.style != "训练流":
            pref = self.config.get("公园选项", "野餐")
            order = (pref,) + tuple(x for x in Defs.PARK_OPTIONS if x != pref)
            return self.choose_option(texts, order)

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
        return self.choose_option(texts, order)

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
        if not self.config.get("允许进步行街", True):
            self.log_info("步行街扭蛋：未开启，退出")
            self.exit_page()
            return True

        cards = self.read_gacha_cards(texts)
        if not cards:
            self.log_warning("扭蛋：三张卡都没认出来，退出不买")
            self.exit_page()
            return True

        stamina = self.top_bar().get("stamina")
        clear_limit = int(self.config.get("扭蛋清空体力阈值", 30))
        allow_clear = stamina is not None and stamina < clear_limit

        def score(c):
            base = {"金": 100, "紫": 60, "蓝": 20}.get(c["quality"], 0)
            return base + (40 if c["mate"] else 0) + (1 if "+" in c["name"] else 0)

        ok = []
        for c in cards:
            if c["clear"]:
                # 清空卡只在体力低于阈值时买（这时是拿体力换属性）
                if not allow_clear:
                    continue
            elif c["quality"] not in ("金", "紫"):
                continue
            c["score"] = score(c)
            ok.append(c)

        detail = "、".join(
            f"{c['name']}({c['quality']}{'+队友' if c['mate'] else ''}"
            f"{'·清空' if c['clear'] else ''}/{score(c)})" for c in cards)
        if not ok:
            self.log_info(f"扭蛋：{detail} → 没有可买的（体力 {stamina}），退出")
            self.exit_page()
            return True

        best = max(ok, key=lambda c: c["score"])
        self.click(best["x"], best["y"], after_sleep=0.5)
        self.log_info(f"扭蛋：{detail} → 选「{best['name']}」（体力 {stamina}）")

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
        if self.match_text(texts, Defs.WEEKEND_PLACES) is not None:
            return False          # 已经在地图上了，交给 handle_weekend
        if self.match_text(texts, "出行") is None:
            return False

        # 商店只有「商店流」才进（用户 2026-10-26：训练流的钱优先换步行街/游戏厅的黄色道具）
        if self.style == "商店流":
            st = self.weekend_state()
            day = st["date"][1] if st["date"] else None
            week = st["date"][0] if st["date"] else None
            if week is None:
                # 顶栏 OCR 偶尔读不到周数；沿用上次的周，避免同一周重复进店
                week = self._shop_week
            shop = self.match_text(texts, "商店")
            if (shop is not None and day != "日" and not st["shop_checked"]
                    and self._shop_week != week):
                st["shop_checked"] = True
                self._shop_week = week
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
        if not self.match_text(texts, Defs.WEEKEND_PLACES):
            return False
        places = self.weekend_priority()
        if self.style == "训练流" and self.config.get("周末第一天优先资源点", True):
            st = self.weekend_state()
            day = st["date"][1] if st["date"] else None
            if day == "日":
                places = ["公园"]
            self.log_info(f"周末{'日' if day == '日' else '六'}：顺序 {places}")
        for place in places:
            target = self.match_text(texts, place)
            if target:
                self.click_box(target, after_sleep=0.5)
                self.log_info(f"周末去「{place}」（{self.style}）")
                return True
        return False
