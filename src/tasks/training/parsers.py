"""只读解析（OCR 文本 + 帧取色），不产生点击。

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

from ok.feature.Box import Box

from src.tasks.ui import Defs  # noqa: F401
from src.tasks.training.constants import (JPEG_PARAMS, PROJECT_ROOT,  # noqa: F401
                                          STYLE_BY_NAME)

# 科目按钮外圈「框」的判定门限（比例 = 环带上命中像素 / 环带总像素）。
# 2026-10-27 第六局实跑 + 用户当场复核：
#   - 普通科目页里被点开的那一行，环带天然就有 0.075~0.114 的橙黄（选中高亮 +
#     科目图标本身），旧门限 0.10 全被当成金框（21 次选科目报 13 次，用户指出
#     「刚才没有金框」）；
#   - 真彩虹框同一套环形采样是 0.333，西瓜图标那一行是 0.100。
# 所以两者都取 0.20 作下限，金框再加「明显高于其它行中位数」这一条。
GOLD_RING_MIN = 0.20
GOLD_RING_FACTOR = 2.0
CRIT_RING_MIN = 0.20
# 代码版本戳（run() 会打到日志里）：排查「改完不生效」时一眼看出跑的是哪份代码
CODE_VERSION = "2026-10-29b"


def ring_is_border(ratio, baseline=None, floor=GOLD_RING_MIN,
                   factor=GOLD_RING_FACTOR):
    """环带命中比例 → 是不是「真外圈」。

    绝对下限挡掉选中高亮/科目图标自带的那点颜色；给了 baseline（其它行中位数）
    时还要求明显高于基线，度假页那种整屏偏黄就不会误判。
    """
    if ratio <= floor:
        return False
    if baseline is None:
        return True
    return ratio > baseline * factor


class TrainingParsersMixin:

    # 商店商品格：实测 3 列 × 2 行（2026-10-27 商店帧 1920x1080）。
    # 列中心 0.223 / 0.357 / 0.490（间距 0.134），行中心 0.368 / 0.634（间距 0.266）。
    # 件数沿革：原本 4 件（3+1）→ 全局强化「商品+1」→ 现在 5 件（3+2，第二行居中）
    #   → 新角色入队可能再 +1 = 6 件（3+3）。
    # 6 件时第 6 格与第 3 列同列（名称 y≈0.544、价格 y≈0.746），区域天然覆盖，不用改判据。
    # 会不会出现 7 件还不知道（也可能不止一位角色有这能力）。真到那一步，屏幕上放不下
    # 第 3 行（价格会到 y≈1.01），大概率变成可滚动列表 —— 到时上面的告警会响，
    # 再决定是滚一屏还是改网格，别凭猜先写。
    # 注意：名称与价格只靠「水平距离 < 0.10w」配对，列间距实测 0.134，
    # 余量只有 0.034。如果哪天变成 4 列（间距≈0.10）就会配错，必须同时改阈值。
    SHOP_COLS = 3
    SHOP_ROWS = 2
    SHOP_MAX_ITEMS = SHOP_COLS * SHOP_ROWS

    def read_money(self, texts):
        """顶栏金币：体力条右边的独立数字（不含斜杠）。"""
        best = None
        for t in texts:
            s = t.name.strip()
            if not s.isdigit() or len(s) > 5:
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if 0.50 <= cx <= 0.62 and cy < 0.09:
                best = int(s)
        return best

    def read_shop_items(self, texts):
        """解析商店左侧商品：名称 + 价格 + 卡片中心点击坐标。

        区域是**按实测网格**留的，不是按商品件数写死的：
        第二行价格实测 y≈0.746，旧上限 0.75 只剩 4px 余量，
        布局抖一下（换分辨率、强化加格子）第二行就整行消失 → 静默不买。
        """
        prices = []
        for t in texts:
            s = t.name.strip()
            # 免费商品（白神时久：每天多一件 0 元商品）：卡上是「0」或「免费」。
            # 旧判据要求 1~9999，正好把 0 元商品整件丢掉（2026-10-29 用户指出）。
            if s in ("免费", "免费！", "免费!"):
                price = 0
            elif s.isdigit() and 0 <= int(s) <= 9999:
                price = int(s)
            else:
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            # 上限 0.82：覆盖第二行价格（0.746）并留 0.07 余量；
            # 底部「上新时间」在 0.93 附近，不会被吃进来。
            if 0.15 <= cx <= 0.58 and 0.38 <= cy <= 0.82:
                prices.append((price, t))

        names = []
        for t in texts:
            s = t.name.strip()
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (0.15 <= cx <= 0.58 and 0.18 <= cy <= 0.62):
                continue
            if "剩余" in s or "时间" in s or "商店" in s:
                continue
            if not (2 <= len(s) <= 12):
                continue
            if not any("\u4e00" <= ch <= "\u9fff" for ch in s):
                continue
            names.append(t)

        items = []
        for price, pt in prices:
            px = pt.x + pt.width / 2
            near = [n for n in names
                    if n.y + n.height / 2 < pt.y + pt.height / 2
                    and abs((n.x + n.width / 2) - px) < self.width * 0.10]
            if not near:
                continue
            name = max(near, key=lambda n: n.y)
            quality, qh, qs = self.card_quality(name, pt)
            items.append({
                "name": name.name.strip(),
                "price": price,
                "quality": quality,
                "hue": qh,
                "sat": qs,
                # 点名称和价格之间的卡片图，避免点到价格/数量
                "x": int(name.x + name.width / 2),
                "y": int((name.y + name.height / 2 + pt.y) / 2),
            })
        if len(items) > self.SHOP_MAX_ITEMS:
            # 超容量说明布局变了（强化又加格子？）——不做截断，截断可能把最贵的丢掉，
            # 但必须喊一声，否则就是静默少买/买错。
            self.log_warning(
                f"商店解析出 {len(items)} 件商品，超过 {self.SHOP_COLS}x{self.SHOP_ROWS} "
                f"网格容量 {self.SHOP_MAX_ITEMS} 件，请补一张商店截图核区域")
        return items

    def card_quality(self, name_box, price_box):
        """读商品卡片的品质色（金 / 紫 / 蓝 / 其他）。

        实机（docs/shots/2026-10-26-shop.png）：卡片底色就是品质色 ——
        紫卡 HSL 偏紫（OpenCV H≈124~132、S≈40、V≈251），
        金卡是暖黄（H≈20~40、S 更高），蓝卡 H≈100~115。
        采样卡片的**左右两条内边**（避开中间图标和文字），取中位色相。
        颜色只用来筛「只买金和紫」，认不出来就按「其他」处理（不买），
        宁可少买也不乱花钱。
        """
        frame = self.frame
        if frame is None:
            return "其他", -1, -1
        h, w = frame.shape[:2]
        x1 = int(name_box.x - w * 0.015)
        x2 = int(name_box.x + name_box.width + w * 0.015)
        y1 = int(name_box.y - h * 0.02)
        y2 = int(price_box.y + price_box.height)
        x1, x2 = max(0, x1), min(w, x2)
        y1, y2 = max(0, y1), min(h, y2)
        if x2 - x1 < 20 or y2 - y1 < 40:
            return "其他", -1, -1

        band_y1 = y1 + int((y2 - y1) * 0.45)
        band_y2 = y2 - int((y2 - y1) * 0.15)
        strips = [frame[band_y1:band_y2, x1 + 4:x1 + 16],
                  frame[band_y1:band_y2, x2 - 16:x2 - 4]]
        pixels = [s.reshape(-1, 3) for s in strips if s.size]
        if not pixels:
            return "其他", -1, -1
        pixels = np.concatenate(pixels)
        hsv = cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
        colored = (hsv[:, 1] >= 20) & (hsv[:, 2] >= 170)
        if colored.sum() < 40:
            return "其他", -1, -1
        hue = float(np.median(hsv[colored, 0]))
        sat = float(np.median(hsv[colored, 1]))
        if 10 <= hue <= 45 and sat >= 55:
            return "金", hue, sat
        if 115 <= hue <= 155:
            return "紫", hue, sat
        if 90 <= hue < 115:
            return "蓝", hue, sat
        return "其他", hue, sat

    @staticmethod
    def is_stamina_clear(text):
        """这段文字是不是「清空精力」效果（而不是恢复精力）。

        只认 Defs.GACHA_CLEAR_PHRASES 里写死的整句，不做「包含清空/包含精力」
        这种模糊匹配；并且先排除恢复类字样 —— 「恢复全部精力」不能被选成清空扭蛋。
        """
        s = (text or "").replace(" ", "").replace("　", "")
        if any(bad in s for bad in Defs.GACHA_CLEAR_EXCLUDE):
            return False
        return any(p in s for p in Defs.GACHA_CLEAR_PHRASES)

    @staticmethod
    def stamina_recover(text):
        """从扭蛋效果文本里算这张卡**总共能补多少精力**。

        2026-10-27 用户截图（第6周周六 45/130，三选一）：
            精力恢复++  立刻恢复90精力，之后每天恢复30点，持续4天，队友属性大幅提升
            金卡 / 100 币 / 带队友
        拆成 90 + 30×4 = 210 点。当时它和「运控增加++」同为 141 分，
        max() 取第一个把恢复卡丢掉了 —— 所以恢复量必须进评分。

        只认「恢复N精力」「恢复N点」两种写法，并且先排除清空/消耗型
        （那是相反效果，见 `is_stamina_clear`）。
        """
        s = (text or "").replace(" ", "").replace("　", "")
        # 「每天恢复N点，持续D天」这种写法不带「精力」二字，也要认。
        if "精力" not in s and "每天恢复" not in s:
            return 0
        # 注意不能复用 GACHA_CLEAR_EXCLUDE：那份列表含「恢复」，是给「清空卡」
        # 判断用的反向词表；恢复卡本身就带「恢复」。
        if "清空" in s or "消耗" in s:
            return 0
        total = 0
        # 持续型：每天恢复 N 点，持续 D 天
        m = re.search(r"每天恢复(\d+)点.*?持续(\d+)天", s)
        if m:
            total += int(m.group(1)) * int(m.group(2))
            s = s.replace(m.group(0), "")
        # 一次性：立刻恢复 N 精力 / 恢复 N 点
        if mm := re.search(r"恢复(\d+)(?:精力|点)", s):
            total += int(mm.group(1))
        return total

    def read_gacha_cards(self, texts):
        """解析扭蛋三选一：卡名 / 品质色 / 是否有队友加成 / 分值 / 点击坐标。

        坐标靠「价格药丸」定位（每张卡底部一个 60/80 的数字），
        卡名取价格正上方最近的短文本，品质色取卡头那一条横带的色相。
        """
        frame = self.frame
        cards = []
        for t in texts:
            s = t.name.strip()
            if not s.isdigit() or not (1 <= int(s) <= 999):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (0.15 <= cx <= 0.85 and 0.60 <= cy <= 0.82):
                continue

            names = [n for n in texts
                     if 0.28 <= (n.y + n.height / 2) / self.height <= 0.44
                     and abs((n.x + n.width / 2) / self.width - cx) < 0.13
                     and 2 <= len(n.name.strip()) <= 12
                     and any("\u4e00" <= ch <= "\u9fff" for ch in n.name.strip())]
            if not names:
                continue
            title = min(names, key=lambda n: abs(
                (n.y + n.height / 2) / self.height - 0.41))

            quality, hue, sat = self.card_band_quality(cx)
            rank = {"金": 3, "紫": 2, "蓝": 1}.get(quality, 0)
            mate = self.has_mate_badge(cx)
            # 「增强款」斜标签贴在卡片右上角（2026-10-30 截图：金/蓝卡都有）。
            # 卡内说明里也有「增强 额外获得体能+14」，所以必须限定在卡片顶部
            # y 0.24~0.35 那一带，不能整页找「增强」。
            enhanced = any(
                "增强" in n.name
                and 0.24 <= (n.y + n.height / 2) / self.height <= 0.35
                and abs((n.x + n.width / 2) / self.width - cx) < 0.14
                for n in texts)
            # 效果文本：卡名下沿到价格药丸之间的同列文字。
            # 2026-10-26 漏读「消耗所有精力」导致 72 体力买了清空扭蛋。
            effect = " ".join(
                n.name.strip() for n in texts
                if title.y + title.height <= n.y < t.y
                and abs((n.x + n.width / 2) / self.width - cx) < 0.13)
            score = rank * 10 + (3 if mate else 0) + (1 if "+" in title.name else 0)
            cards.append({
                "name": title.name.strip(),
                "price": int(s),
                "quality": quality,
                "mate": mate,
                "enhanced": enhanced,
                "hue": hue,
                "sat": sat,
                "effect": effect,
                "clear": self.is_stamina_clear(effect),
                "recover": self.stamina_recover(effect),
                "score": score,
                "x": int(title.x + title.width / 2),
                "y": int(title.y + title.height / 2),
            })
        return cards

    def card_band_quality(self, cx):
        """取卡片标题条（y≈0.36~0.43）的底色，返回 金/紫/蓝/其他 与 H/S。"""
        return self.card_quality_at(cx, 0.36, 0.43)

    def card_quality_at(self, cx, y1f, y2f):
        """按比例框取卡片底色判断品质，返回 金/紫/蓝/其他 与中位 H/S。

        `cx` / `y1f` / `y2f` 都是比例（0~1）：扭蛋页用标题条 0.36~0.43，
        游戏厅卡片用自己的卡面区间（2026-10-27），两种情况共用同一套色相阈值。
        """
        frame = self.frame
        if frame is None:
            return "其他", -1, -1
        h, w = frame.shape[:2]
        x1 = int(w * (cx - 0.06))
        x2 = int(w * (cx + 0.06))
        y1, y2 = int(h * y1f), int(h * y2f)
        roi = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if roi.size == 0:
            return "其他", -1, -1
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        m = (sat > 60) & (val > 120)
        if m.sum() < 50:
            return "其他", -1, -1
        hmed = float(np.median(hue[m]))
        smed = float(np.median(sat[m]))
        if 10 <= hmed <= 45 and smed >= 90:
            return "金", hmed, smed
        if 115 <= hmed <= 160:
            return "紫", hmed, smed
        if 90 <= hmed < 115:
            return "蓝", hmed, smed
        return "其他", hmed, smed

    def has_mate_badge(self, cx):
        """卡头左上角有没有队友标记（= 这次带队友加成）。

        2026-10-30 截图（扭蛋增强界面）该标记是**深蓝底 + 浅色头像 + 「!」**，
        不是旧版的橙色「!」气泡，而且位置更靠下（y≈0.29~0.34h，旧 ROI 只到 0.26h）。
        所以 ROI 放宽到 y 0.22~0.36，并同时接受「橙色气泡」和「深蓝紫块」两种样子。
        金卡背景是亮黄（g 很高）、蓝卡背景是浅蓝（r 很高），都不会命中 deep。
        """
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        x1 = int(w * (cx - 0.12))
        x2 = int(w * (cx + 0.02))
        y1, y2 = int(h * 0.22), int(h * 0.36)
        roi = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if roi.size == 0:
            return False
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        orange = (r > 200) & (g > 90) & (g < 200) & (b < 90)
        # 2026-10-30：队友标记也可能是「深蓝底 + 头像」的圆（BGR 约 82/60/55），
        # 蓝色分量高于红色。用 b > r+20 就能抓住，同时不会命中金色/橙色横幅。
        deep = (b > r + 20) & (r < 130) & (g < 130) & (b < 170)
        mask = (orange | deep).astype(np.uint8)
        if not mask.any():
            return False
        # 必须按**形状 + 相对面积**过滤：左侧第一张卡会被左上角「再买一次！」
        # 橙色横幅覆盖（189x77，占 ROI 33%），只按像素个数会把它当队友标记
        # （2026-10-30 16:46 日志里左卡被误记成「蓝+队友」）。
        # 实测：横幅 frac=0.33 被排除；扭蛋标记 0.164、餐厅标记 0.062 都命中。
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
        roi_area = float(mask.size)
        for i in range(1, n):
            ww, hh, area = int(stats[i, 2]), int(stats[i, 3]), int(stats[i, 4])
            frac = area / roi_area
            if 0.03 <= frac <= 0.28 and 0.5 <= ww / max(hh, 1) <= 2.8:
                return True
        return False

    def weekend_state(self):
        """按「第N周 + 星期X」维护周末状态：去过哪些地点、商店看没看、公园选什么。

        `visited` 是关键：游戏厅进去发现没有可跳过的小游戏会退出，
        如果不记录「今天已经去过」，下一拍又会点进游戏厅，来回循环（2026-10-26）。
        """
        state = self.top_bar()
        key = (state.get("week"), state.get("day"))
        prev = self._weekend.get("date")
        if prev != key:
            if prev is not None:
                # 观测用：改期角色「跳进其他流程」后顶栏日期会不会变还不知道。
                # 先把这个切换记下来，等角色到手跑一局，就能从日志判断
                # day_state 的键该不该带上「流程实例」（变了就重置今日行程，不变就沿用）。
                self.log_info(f"日期键 {prev} → {key}（今日行程重置）")
            self._weekend = {
                "date": key,
                "shop_checked": False,
                "bought": False,
                "park_choice": None,
                "visited": set(),
                # 今天在步行街抽到过扭蛋（扭蛋流决定要不要再去餐厅打工）
                "gacha_spent": False,
                # 训练日里出现「出行」时先外出（野上泷周五也能出行），一天只做一次
                "outing_done": False,
            }
        return self._weekend

    def read_fail_rate(self, texts):
        """读取当前选中科目的失败率（0~100），读不到返回 None。"""
        joined = "".join(t.name for t in self.texts_in_region(texts, "fail_rate")).replace(" ", "")
        if m := Defs.FAIL_RATE_RE.search(joined):
            return int(m.group(1))
        return None

    def subjects_from_texts(self, texts):
        """从 OCR 结果取「科目名 -> 框」，并把被 OCR 拆成两个字的科目合起来。

        2026-10-27 第 4 周度假页实跑：「防守」被读成「防」「守」两个单字框，
        科目直接漏掉（用户：「两次防守有队友都没有被识别到就直接跳过了」）。
        这里把同一行相邻的两个单字拼回科目名，再用并集框当点击目标。
        """
        pool = self.texts_in_region(texts, "subject_list")
        found = {}
        for t in pool:
            name = t.name.strip()
            if name in Defs.TRAINING_SUBJECTS:
                found.setdefault(name, t)
        singles = [t for t in pool if len(t.name.strip()) == 1]
        for i, a in enumerate(singles):
            sa = a.name.strip()
            acy = a.y + a.height / 2
            for b in singles[i + 1:]:
                name = sa + b.name.strip()
                if name not in Defs.TRAINING_SUBJECTS or name in found:
                    continue
                if abs((b.y + b.height / 2) - acy) > a.height * 0.8:
                    continue
                gap = b.x - (a.x + a.width)
                if gap > a.width * 1.5:
                    continue
                x = min(a.x, b.x)
                y = min(a.y, b.y)
                w = max(a.x + a.width, b.x + b.width) - x
                h = max(a.y + a.height, b.y + b.height) - y
                found[name] = Box(name=name, x=x, y=y, width=w, height=h)
                self.log_debug(f"科目名被拆开，已合并：{sa}+{b.name.strip()} → {name}")
                break
        # 2026-10-29 第 4 周海边合宿 星期三实跑：科目行是「防守 + MAX」（满级标签），
        # OCR 把「守」吞进了「MAX」，整帧只剩一个「防」字 → 两个字拼不回来，
        # 「有队友的防守」连探测都没探测就被跳过（用户：「有队友的防守训练被跳过了」）。
        # 六个科目的首字/尾字在全表里都唯一，所以只剩一个字时也能确定是哪一行；
        # 只在「整名没读出来、且这个字只属于唯一一个科目」时才补，避免乱认。
        for t in singles:
            ch = t.name.strip()
            matches = [n for n in Defs.TRAINING_SUBJECTS
                       if n not in found and (n.startswith(ch) or n.endswith(ch))]
            if len(matches) != 1:
                continue
            name = matches[0]
            found[name] = Box(name=name, x=t.x, y=t.y, width=t.width, height=t.height)
            self.log_debug(f"科目名只剩一个字，按唯一首/尾字补全：{ch} → {name}")
        return found

    def current_subject(self, texts):
        """当前已选中的科目：右边有「决定」的那一行。"""
        decide = self.match_text(texts, "决定")
        if decide is None:
            return None
        dy = decide.y + decide.height / 2
        cands = []
        for t in self.texts_in_region(texts, "subject_list"):
            if t.name.strip() not in Defs.TRAINING_SUBJECTS:
                continue
            cy = t.y + t.height / 2
            if abs(cy - dy) < 45 * self.height / 1080.0:
                cands.append(t)
        return max(cands, key=lambda t: t.height).name.strip() if cands else None

    def find_teammate_icons(self, x_from=0.78, x_to=1.0):
        """在指定横向范围内找「有队友」的橙色小人头像，返回中心点列表。

        实测（2026-10-25）：小人是 42x42 的纯橙圆，主色 (255,145,0)；
        右上角「决定」按钮是亮黄 (255,237,75)，**G 通道高 90**，所以卡 g<190 就能排除。
        再用连通域尺寸过滤（42px 上下），后期按钮图标换色也不影响。

        默认只扫右侧科目区（0.78~1.0）；餐厅两个打工选项在屏幕中间，
        所以要能传整屏范围（2026-10-29）。
        """
        frame = self.frame
        if frame is None:
            return []
        h, w = frame.shape[:2]
        x0, x1 = int(w * x_from), int(w * x_to)
        roi = frame[:, x0:x1]
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        mask = ((r > 225) & (g > 100) & (g < 190) & (b < 70)).astype(np.uint8)
        if not mask.any():
            return []
        n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
        size = 42 * w / 1920.0                   # 按分辨率缩放
        icons = []
        for i in range(1, n):
            _x, _y, ww, hh, area = stats[i]
            if not (size * 0.6 <= ww <= size * 1.6 and size * 0.6 <= hh <= size * 1.6):
                continue
            if area < size * size * 0.3:
                continue
            icons.append((cent[i][0] + x0, cent[i][1]))
        return icons

    def mates_on_card(self, box, icons):
        """这张卡片**左上角**挂了几个队友小头像（用户 2026-10-29 口径）。

        餐厅打工的两个选项是「卡片」，队友头像排在卡片左上角，名字/效果写在
        卡片下方，所以只数文字框**上方偏左**那一片：
        纵向从文字框上沿往上 2.5 个文字高，横向从文字中心往左 1.4 个文字宽。
        数不到就返回 0 —— 调用方拿 0 当平局处理（退化成「选右边」）。
        """
        if not icons or box is None:
            return 0
        cx = box.x + box.width / 2
        x1, x2 = cx - box.width * 1.4, cx + box.width * 0.1
        y1, y2 = box.y - box.height * 2.5, box.y + box.height * 0.3
        n = sum(1 for ix, iy in icons if x1 <= ix <= x2 and y1 <= iy <= y2)
        if n:
            return n
        # 2026-10-30 用户实机（餐厅，报告 #12 / #84）：队友标记换成了
        # **深蓝底 + 头像的圆**，不是橙色小人 —— 橙色判据必然返回 0，
        # 于是两张卡都记成「0 人」，最后按「右边优先」选错。
        # 这里按位置兜底：文字框上方 2.4~4.6 个字高、横向 ±0.55 个字宽那一条带，
        # 深蓝像素占比 > 0.08 记 1 个队友。
        # 实测（1030-163846 / 1030-164421 两帧）：有标记 0.2226，没有 0.0123。
        frame = self.frame
        if frame is None:
            return 0
        fh, fw = frame.shape[:2]
        bx1 = int(max(0, cx - box.width * 0.55))
        bx2 = int(min(fw, cx + box.width * 0.55))
        by1 = int(max(0, box.y - box.height * 4.6))
        by2 = int(max(0, box.y - box.height * 2.4))
        roi = frame[by1:by2, bx1:bx2]
        if roi.size == 0:
            return 0
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        deep = (b > r) & (r < 120) & (g < 120) & (b < 160)
        return 1 if float(deep.mean()) > 0.08 else 0

    def is_training_plan(self, texts):
        """是不是训练计划（日历）页。

        判据要能挡掉训练日台词「今天的训练计划，我看看」——那句话同样含
        「的训练计划」。真正的那一页有**整周课表**：至少两个「星期X」列 + 三节课，
        台词页只有当天那一个「星期四」，课表数也不够。
        """
        if self.match_text(texts, "的训练计划") is None:
            return False
        if self.read_plan_captain(texts):
            return True
        day_cols = sum(1 for t in texts
                       if t.name.strip() in ("星期二", "星期三", "星期四",
                                             "星期五", "星期六", "星期日"))
        lessons = sum(1 for t in texts if "训练" in t.name)
        return day_cols >= 2 and lessons >= 3

    def read_plan_captain(self, texts):
        """训练计划（日历）页左上角「XX的训练计划」里的 XX。

        实测（logs/unknown/1029-093636_register_stuck_s636.txt，1349x759）OCR 会把
        名字和标题拆成两个框：
            望月     (184,74,158,91)
            的训练计划 (339,96,294,62)
        所以两种写法都要认：整段「真崎的训练计划」，或者「的训练计划」左边那个框。

        2026-10-29 实跑踩坑：名字被漏识别时，标题左边只剩**「星期」列头**
        （同一行区域，离标题 60px），于是「队长」被认成「星期」。
        现在候选名必须像人名（`_looks_like_captain`），并且**优先取能在角色表里
        查到的那个**，一个都不像就返回 None（让上一拍重试，而不是认一个假名字）。
        训练日台词「今天的训练计划，我看看」不在左上角区域，不会被当标题。
        """
        area = self.texts_in_region(texts, "plan_title")
        cands = []
        for t in area:
            s = t.name.replace(" ", "").strip()
            m = Defs.PLAN_TITLE_RE.match(s)
            if m:
                cands.append(m.group(1))
        title = next((t for t in area
                      if t.name.replace(" ", "").strip() == "的训练计划"), None)
        if title is not None:
            cy = title.y + title.height / 2
            tcx = title.x + title.width / 2
            for t in area:
                if t is title:
                    continue
                # 名字就在标题左边、同一行，而且是**大字号**。
                # 2026-10-29 真帧（logs/live_run/plan_*.txt）：
                #     闪堂      (187,74,163,89)
                #     的训练计划 (339,91,295,71)   ← 名字右边缘 350 压过标题左边 339
                #     星期      (182,170,68,35)    ← 列头在正下方 96px
                # 所以：纵向 0.8 个标题高内、横向允许 5% 屏宽重叠、名字高度 ≥ 0.6 标题高。
                if abs((t.y + t.height / 2) - cy) >= title.height * 0.8:
                    continue
                if (t.x + t.width / 2) >= tcx:
                    continue
                gap = title.x - (t.x + t.width)
                if gap < -self.width * 0.05 or gap > self.width * 0.10:
                    continue
                if t.height < title.height * 0.6:
                    continue
                cands.append(t.name.replace(" ", "").strip())
        cands = [c for c in cands if self._looks_like_captain(c)]
        for c in cands:                       # 能查到流派的优先（最可靠）
            if self.style_for_name(c):
                return c
        return cands[0] if cands else None

    @staticmethod
    def _looks_like_captain(name):
        """这段字像不像队长名（1~6 个汉字，可以带「·」）。

        必须挡掉日历上的非人名：列头「星期」、周次「第1周」、状态「大胜」、
        按钮「训练」「比赛」… 否则名字一旦漏识别，就会拿这些当队长。
        """
        s = (name or "").replace(" ", "").strip()
        # 队长名至少两个字：日历上的单字全是噪声（「第」5周、「星」期…），
        # 2026-10-29 实跑就此把「第」当成了队长。
        if not (2 <= len(s) <= 6):
            return False
        if s.startswith("星期") or s in ("今天", "今日", "训练", "计划", "比赛",
                                        "周", "月", "日", "胜", "负"):
            return False
        if any(ch.isdigit() for ch in s):
            return False
        return all("\u4e00" <= ch <= "\u9fff" or ch in "·・" for ch in s)

    @staticmethod
    def style_for_name(name):
        """角色名（可能只有姓氏）→ 流派；查不到返回 None。

        查表用 STYLE_BY_NAME（抄自 docs/roster/ssr_roster.json）。
        日历标题只显示姓氏，所以按前缀匹配；多个前缀都命中（「羽」→ 羽田 / 羽漱川）
        时取**更长的那个**，长度一样才算歧义（返回 None，宁可不动）。
        """
        if not name:
            return None
        s = name.replace(" ", "").replace("・", "·").strip()
        best, best_len, tie = None, 0, False
        for full, style in STYLE_BY_NAME.items():
            if s == full:
                return style
            if not (full.startswith(s) or s.startswith(full)):
                continue
            if len(full) > best_len:
                best, best_len, tie = style, len(full), False
            elif len(full) == best_len and style != best:
                tie = True
        return None if tie else best

    def has_teammate_icon(self, box, icons):
        """这个科目的右边有没有小人头像。

        两代 UI 都支持（2026-10-26 实测）：
        - 旧版：头像在按钮**右上角**，中心比文字中心高约 55px；
        - 新版：头像在按钮**右侧居中**，和文字中心几乎同高。
        不能用 ±80 的大范围：相邻两行只差 100px，会同时命中两个科目
        （旧代码就是这么把 5 个科目全当成「有队友」的）。
        """
        if not icons or box is None:
            return False
        cx = box.x + box.width / 2
        cy = box.y + box.height / 2
        old_ui_y = cy - 55 * self.height / 1080.0
        tol_old = 20 * self.height / 1080.0
        tol_new = 35 * self.height / 1080.0
        return any(
            ix > cx and (abs(iy - old_ui_y) < tol_old or abs(iy - cy) < tol_new)
            for ix, iy in icons)

    def probe_subject(self, box):
        """点开一个科目，返回预览信息：队友数 / 装备数 / 满经验数 / 彩虹边框。"""
        name = (box.name or "").strip()
        self.click_box(box, after_sleep=0.55)
        texts = self._ocr_region()
        self.log_debug(f"场上角色名：{self.scene_names(texts)}")
        self.dump_subject_frame(name, texts)
        return self.analyze_preview(texts, subject=name)

    def dump_subject_frame(self, subject, texts):
        """把科目预览帧存一份（限流），用于事后校准金框/彩框。

        为什么要它：2026-10-29 用户两次说「黄框/彩框没识别到」，但事后没有帧，
        只能靠猜。这里每局最多存 12 张，文件名带科目名，复盘时直接量环带占比。
        """
        if getattr(self, "_subject_dumps", 0) >= 12:
            return
        frame = self.frame
        if frame is None or not frame.any():
            return
        try:
            folder = os.path.join(PROJECT_ROOT, "logs", "debug")
            os.makedirs(folder, exist_ok=True)
            idx = getattr(self, "_subject_dumps", 0)
            # 文件名只用 ASCII：提权进程写中文名会变成乱码，OpenCV 也读不了
            path = os.path.join(
                folder, f"{time.strftime('%m%d-%H%M%S')}_subject{idx:02d}.jpg")
            cv2.imwrite(path, frame, JPEG_PARAMS)
            with open(path[:-4] + ".txt", "w", encoding="utf-8") as f:
                f.write(f"# subject={subject}\n")
                f.write("\n".join(
                    f"{t.name}\t({t.x},{t.y},{t.width},{t.height})" for t in texts))
            self._subject_dumps = idx + 1
        except Exception as e:                   # noqa: BLE001
            self.log_debug(f"科目帧存证失败: {type(e).__name__}: {e}")

    def analyze_preview(self, texts, subject=None):
        """读一帧科目预览，返回打分要用的全部字段。

        - 队友 / 经理：场景里飘在角色头顶的名字（经理的名字牌就是「球队经理」，
          会被当成队友名，必须单独扣出来）；
        - 器材数 / 满经验：底部器材卡的经验条与 MAX 字样（已实测）；
        - 金框 / 彩框：**训练科目按钮外面那一圈**（用户 2026-10-27 口径）。
          框本来就是装备/经理经验满了才点亮的，如果再去数器材卡上的金框，
          等于和「装备」重复计分，所以器材卡只按张数算一次。

        `subject` = 这次要评分的科目名。框是**常驻**的（用户 2026-10-27：
        「金框彩框很显眼，不需要选中也会亮起」），所以按科目名找它自己那一行，
        不要用「决定按钮所在行」去猜（旧做法在 56 次探测里错了 10 次）。
        """
        scene = self.read_scene(texts)
        equip, full = self.read_equipment(texts)
        crit = self.has_crit_border(texts, subject=subject)
        # 彩框和金框**互斥**：彩虹边框里本来就有一段橙黄，若不互斥，
        # 同一个按钮会同时算「金框 1.0 + 彩框 1.2」，等于一物两记。
        # 而且两者同源（装备/经理经验满了才亮），本来就不该叠加
        # （用户 2026-10-27 截图：科目按钮彩框 ↔ 中间装备卡彩框）。
        gold = 0 if crit else (1 if self.has_gold_border(texts, subject=subject) else 0)
        return {
            "names": scene["names"],
            "mates": scene["mates"],
            "manager": scene["manager"],
            "equip": equip,
            "full": full,
            "gold": gold,
            "crit": crit,
            "color": 1 if crit else 0,
            "manager_crit": 1 if (scene["manager"] and (full > 0 or crit)) else 0,
        }

    def read_scene(self, texts):
        """场上角色：队友数（扣掉队长和经理）+ 经理在不在。"""
        names = self.scene_names(texts)
        manager = 1 if any(any(m in n for m in Defs.MANAGER_TEXTS) for n in names) else 0
        mates = [n for n in names if not any(m in n for m in Defs.MANAGER_TEXTS)]
        return {
            "names": names,
            "manager": manager,
            # 队长总在场，所以队友数 = 场上名字 - 队长（- 经理已经被扣掉）
            "mates": max(0, len(mates) - 1),
        }

    def read_attribute_values(self, texts):
        """读底部六维面板的当前数值：`{属性名: 当前值}`，读不到的属性不出现。

        版式（2026-10-26 实测，三行两列）：每格「名字 + 等级 + 当前值/上限」，
        OCR 会拆成「999」+「/1450」，也会把等级粘在数值前（「C+354/±90」）。
        所以按行找名字右侧、从左往右第一个 ≥100 的数字 —— 后面的 /上限、/+加成
        都比当前值大或小于 100。
        """
        panel = self.texts_in_region(texts, "attr_panel")
        values = {}
        for name in Defs.TRAINING_SUBJECTS:
            # 必须整词匹配：match_text 的「拼接兜底」会返回面板里最长的框
            # （例如「C+354/±90」），拿来当名字会把整行读错。
            head = self.exact_text(panel, (name,))
            if head is None:
                continue
            cy = head.y + head.height / 2
            row = [t for t in panel
                   if abs((t.y + t.height / 2) - cy) <= self.height * 0.035
                   and t.x > head.x + head.width * 0.5]
            for t in sorted(row, key=lambda b: b.x):
                m = re.search(r"(\d{2,4})", t.name)
                if m and int(m.group(1)) >= 100:
                    values[name] = int(m.group(1))
                    break
        return values

    def read_equipment(self, texts):
        """数预览界面底部的器材卡，并判断哪些已经满经验。

        两种形态：
        - 未满：卡片底部是黑色胶囊经验条，青色部分是进度；
        - 已满：经验条变成「MAX」字样（实测 2026-10-26），且卡片带彩虹边框。
        所以 装备数 = 经验条个数 + MAX 个数；满经验数 = MAX 个数 + 全满的经验条。
        金框**不在这里算**：框是科目按钮外圈的事（见 has_gold_border），
        按器材卡的金框再算一次会和「装备」重复计分（用户 2026-10-27）。
        """
        frame = self.frame
        equip = full = 0
        if frame is not None:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = Defs.region("equip_bars")
            roi = frame[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
            if roi.size:
                b = roi[:, :, 0].astype(int)
                g = roi[:, :, 1].astype(int)
                r = roi[:, :, 2].astype(int)
                dark = ((b < 90) & (g < 90) & (r < 90)).astype(np.uint8)
                n, lab, stats, _cent = cv2.connectedComponentsWithStats(dark, 8)
                min_w, max_w = int(w * 0.04), int(w * 0.10)
                for i in range(1, n):
                    x, y, ww, hh, _area = stats[i]
                    if not (min_w <= ww <= max_w and 8 <= hh <= 30):
                        continue
                    equip += 1
                    cb = b[y:y + hh, x:x + ww]
                    cg = g[y:y + hh, x:x + ww]
                    cr = r[y:y + hh, x:x + ww]
                    cyan = (cb > 150) & (cg > 110) & (cr < 160)
                    if float(cyan.mean()) > 0.75:
                        full += 1
        # MAX 字样（满经验卡没有经验条）
        bar_texts = self.texts_in_region(texts, "equip_bars")
        joined = "".join(t.name for t in bar_texts).upper().replace(" ", "")
        max_count = joined.count(Defs.EQUIP_MAX_TEXT)
        if max_count:
            equip += max_count
            full += max_count
        return equip, full

    def has_gold_border(self, texts=None, subject=None, quiet=False):
        """科目按钮外圈有没有金色边框。

        用户 2026-10-27 口径：金框/彩框都指科目按钮外面那一圈，不是器材卡；
        而且**框是常驻的，不需要选中也会亮**，所以按科目名找它自己那一行。

        **必须用「相对亮度」判，不能只看绝对值**：海边度假那套界面里所有科目
        按钮本身就是橙黄色（等级强制 MAX），固定阈值会把 6 行全判成有金框。
        做法：把 6 个科目按钮的环带都量一遍，看目标行是否明显亮于其余行（中位数）。

        门限 0.20（2026-10-27 第六局实跑 + 用户当场复核）：普通科目页里被点开的
        那一行本来就比其它行更橙（选中高亮 + 科目图标本身），实测 0.075~0.114，
        旧门限 0.10 把整批高亮都当成了金框（21 次选科目报了 13 次，用户指出
        「刚才没有金框」）。真彩虹框同一套环形采样是 0.333，0.20 能把
        「选中高亮」和「真外圈」分开；倍数条件同步抬到 2.0。
        """
        frame = self.frame
        if frame is None:
            return False
        texts = texts if texts is not None else self._ocr_region()
        subjects = [t for t in texts
                    if t.name.strip() in Defs.TRAINING_SUBJECTS
                    and (t.x + t.width / 2) / max(self.width, 1) > 0.7]
        if len(subjects) < 2:
            return False
        h, w = frame.shape[:2]

        target = None
        if subject:
            target = next((t for t in subjects if t.name.strip() == subject), None)
            # 指定了科目但这一帧没找到它那一行 → 直接判「无框」。
            # 2026-10-27 17:04 事故：探测「防守」时 OCR 没认出防守那行，
            # 兜底退回「决定所在行」= 投射，把投射的金框 0.482 记到防守头上，
            # 结果两个科目同分 4.30，把真正的金框科目挤掉了。
            if target is None:
                return False
        if target is None:
            # 没有指定科目（离线工具）才退回「决定按钮所在行」
            decide = self.match_text(texts, "决定")
            sel_cy = ((decide.y + decide.height / 2) if decide is not None
                      else self.height / 2)
            target = min(subjects,
                         key=lambda t: abs((t.y + t.height / 2) - sel_cy))

        x1, x2 = int(w * 0.752), int(w * 0.968)
        t_ring = max(6, int(h * 0.012))

        def ring_gold_ratio(cy):
            y1 = max(0, int(cy - h * 0.055))
            y2 = min(h, int(cy + h * 0.055))
            roi = frame[y1:y2, x1:x2]
            if roi.size == 0:
                return 0.0
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            hue, sat, val = cv2.split(hsv)
            gold = (sat > 110) & (val > 150) & (hue >= 8) & (hue <= 42)
            ring = np.zeros(roi.shape[:2], dtype=bool)
            ring[:t_ring, :] = True
            ring[-t_ring:, :] = True
            ring[:, :t_ring] = True
            ring[:, -t_ring:] = True
            return float((gold & ring).sum()) / max(int(ring.sum()), 1)

        ratio = ring_gold_ratio(target.y + target.height / 2)
        others = sorted(ring_gold_ratio(s.y + s.height / 2)
                        for s in subjects if s is not target)
        baseline = others[len(others) // 2] if others else 0.0
        # 2026-10-29：金框在度假页也漏识别，占比与基线一直打出来（校准用）
        self.log_debug(f"科目金框：{target.name.strip()} 占比 {ratio:.3f}，"
                       f"其它行中位数 {baseline:.3f}")
        return ring_is_border(ratio, baseline)

    def has_crit_border(self, texts=None, subject=None, quiet=False):
        """科目按钮外圈有没有彩虹边框（= 有满经验器材，训练会暴击）。

        框常驻、不需要选中（用户 2026-10-27），所以按科目名定位那一行；
        没有指定科目时退回「决定按钮所在行」。
        只采样按钮**边缘一圈**：按钮内部的科目图标（西瓜/鞋）有绿色/红色，
        采样整块会把普通科目误判成彩虹（实训 2026-10-26）。

        门限 0.20：实测真彩虹框 = 0.333，西瓜图标那一行 = 0.100，
        旧门限 0.06 会把西瓜当彩虹。
        """
        frame = self.frame
        if frame is None:
            return False
        texts = texts if texts is not None else self._ocr_region()
        h, w = frame.shape[:2]

        cy = None
        if subject:
            row = next((t for t in texts
                        if t.name.strip() == subject
                        and (t.x + t.width / 2) / max(self.width, 1) > 0.7), None)
            if row is None:
                # 同上：指名了科目却找不到，不能拿别的行顶替
                return False
            cy = row.y + row.height / 2
        if cy is None:
            decide = self.match_text(texts, "决定")
            if decide is None:
                return False
            cy = decide.y + decide.height / 2

        x1, x2 = int(w * 0.74), int(w * 0.98)
        y1 = max(0, int(cy - h * 0.06))
        y2 = min(h, int(cy + h * 0.06))
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return False
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        magenta = (sat > 90) & (val > 120) & (hue >= 125) & (hue <= 175)
        green = (sat > 90) & (val > 120) & (hue >= 35) & (hue <= 90)
        ring = np.zeros(roi.shape[:2], dtype=bool)
        t = max(4, int(h * 0.008))
        # 2026-10-30 修：原先取整圈 + x 只到 0.93w。
        # 实测（logs/debug/1030-154728_subject07.jpg，第 1 行「投射」明显有彩框）
        # 整圈 0.66~0.93w = 0.197，差 0.003 没过 0.20 门限 → 判「无彩框」，
        # 于是把第一位带彩框的科目录成了 0 分，选了别的。
        # 原因：左右两条环带一条落在按钮外背景、一条落在按钮内部渐变，纯噪声。
        # 改成只采**上下两条边**、x 覆盖到按钮真实右边缘（0.74~0.98w）后：
        #   彩框帧 0.24~0.49，非彩框（选中高亮/鞋图标绿）0~0.158，门限 0.20 分得开。
        ring[:t, :] = True
        ring[-t:, :] = True
        hit = (magenta | green) & ring
        ratio = float(hit.sum()) / max(int(ring.sum()), 1)
        # 2026-10-29：彩框在度假页漏识别，把占比一直打出来（quiet 只管提示级别），
        # 方便拿真实数字校准门限（CRIT_RING_MIN）。
        self.log_debug(f"科目彩框：{subject or '当前行'} 占比 {ratio:.3f}"
                       f"（门限 {CRIT_RING_MIN}）")
        return ring_is_border(ratio, floor=CRIT_RING_MIN)

    def scene_names(self, texts):
        """过滤出训练场景里飘在角色头顶的名字。"""
        x1, y1, x2, y2 = Defs.region("scene_names")
        skip = {"失败率", "决定", "训练", "休息", "比赛", "商店", "出行", "跳过"}
        found = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            s = t.name.strip()
            if s in skip:
                continue
            if 2 <= len(s) <= 4 and all("\u4e00" <= ch <= "\u9fff" for ch in s):
                found.append(s)
        return found

    def read_captain_name(self):
        """底部中间那行队长名字（区域见 Defs.CAPTAIN_NAME_AREA）。"""
        x1, y1, x2, y2 = Defs.CAPTAIN_NAME_AREA
        best = None
        for t in self._ocr_region():
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            if len(t.name.strip()) < 2:
                continue
            if best is None or t.width > best.width:
                best = t
        return best.name.strip() if best is not None else None

    def _card_texts(self, texts, idx):
        x1 = 0.05 + idx * 0.31
        x2 = x1 + 0.28
        return [t for t in texts if x1 <= (t.x + t.width / 2) / self.width <= x2]

    def _card_quality(self, frame, idx):
        """判断技能卡品质，返回 3=彩色 / 2=金色 / 1=白色。

        评分公式里技能价值差一倍（75 / 50 / 25），所以必须先分清金色和白色。
        判据用卡片上部色带的**暖色像素的平均 G 通道**：
        橙色 (255,145,0) 的 G≈145 → 彩色；金色 (255,215,0) 的 G≈215 → 金色；
        白/蓝卡没有暖色带 → 白色。
        实测日志里会打印 G 均值，方便以后校准。
        """
        if frame is None:
            return 1
        h, w = frame.shape[:2]
        x1 = int(w * (0.07 + idx * 0.32))
        x2 = int(w * (0.07 + idx * 0.32 + 0.24))
        y1, y2 = int(h * 0.20), int(h * 0.34)
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return 1
        b, g, r = roi[:, :, 0].astype(int), roi[:, :, 1].astype(int), roi[:, :, 2].astype(int)
        warm = (r > 185) & (b < 120) & (g > 90)
        ratio = float(warm.mean())
        if ratio < 0.12:
            return 1
        mean_g = float(g[warm].mean())
        self.log_debug(f"技能卡{idx + 1} 暖色 {ratio:.2f} G均值 {mean_g:.0f}")
        return 3 if mean_g < 185 else 2

    def choice_candidates(self, texts):
        """屏幕中部的竖向选项候选（事件 / 对话三选一），按 y 从下到上排序。

        只做形态判断：同一列、竖排、短文本；**不要求白底**。
        用途：跳过按钮是灰的时，先认出选项，别让 dialog_continue 去点灰按钮。
        训练动画页的「训练加成/表情加成」是横向三个，分组后每组只有 1 个，
        不会误判成竖向选项。
        """
        ui = {"下一场", "下一步", "跳过", "确定", "取消", "关闭",
              "继续", "领取", "开始训练"}
        cands = []
        for t in texts:
            s = t.name.strip()
            if s in ui or s in Defs.WEEKEND_PLACES or self.is_decor(s):
                continue
            if re.search(r"[0-9%]", s):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (0.22 <= cx <= 0.78 and 0.20 <= cy <= 0.72):
                continue
            if not (2 <= len(s) <= 20):
                continue
            # 选项名是短名词：带句读的台词、学校名都不是选项。
            # 2026-10-26 周末首页把「眼镜的度数又上涨了，教练 / 您可以陪我去配一副
            # 新的吗？/ 北空之丘女子高校」当成三选一，每拍点第一个导致卡死。
            # 选项名是短名词：带句读的台词、学校名都不是选项。
            if re.search(r"[，。？！、；：]", s):
                continue
            if re.search(r"(高校|公学|中学|大学|学校)$", s):
                continue
            # 选项是普通字号；「备战 / 东部大赛」这种大标题/大按钮不算。
            if t.height > self.height * 0.08:
                continue
            cands.append(t)

        groups = []
        for t in sorted(cands, key=lambda x: x.y):
            cx = (t.x + t.width / 2) / self.width
            placed = False
            for g in groups:
                gx = (g[-1].x + g[-1].width / 2) / self.width
                if abs(gx - cx) < 0.12 and 0 < (t.y - g[-1].y) / self.height < 0.25:
                    g.append(t)
                    placed = True
                    break
            if not placed:
                groups.append([t])
        if not groups:
            return []
        group = max(groups, key=len)
        return group if len(group) >= 2 else []


    def options_not_paragraph(self, texts):
        """2026-10-29 新事件：选项是**各自居中的整句**，不是左对齐的段落。

        真帧 docs/shots/2026-10-29-event-sim-software.png（1549x871）：
            只是模拟软件而已，让她们试试 (371,299,714,97)
            不许她们乱来                 (647,440,253,44)
        两条左边缘差 276px、宽度差 461px；而对话/关卡列表/本周总结都是
        **左对齐等宽**的段落（左边缘几乎相同）。所以这里只认「左边缘不齐」的
        2 行以上，避免把对话当选项（2026-10-26 卡死事故）。
        """
        ui = {"下一步", "下一场", "跳过", "确定", "取消", "关闭",
              "继续", "领取", "开始训练"}
        rows = []
        for t in texts:
            s = t.name.strip()
            if s in ui or s in Defs.WEEKEND_PLACES or self.is_decor(s):
                continue
            if re.search(r"[0-9%]", s) or not (2 <= len(s) <= 30):
                continue
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if not (0.30 <= cx <= 0.70 and 0.20 <= cy <= 0.72):
                continue
            if t.height > self.height * 0.13:
                continue
            rows.append(t)
        if len(rows) < 2:
            return []
        rows.sort(key=lambda t: t.y)
        lefts = [t.x for t in rows]
        widths = [t.width for t in rows]
        if max(lefts) - min(lefts) <= self.width * 0.05:
            return []                      # 左对齐 = 段落，不是选项
        if max(widths) - min(widths) <= self.width * 0.10:
            return []                      # 等宽换行 = 段落
        centers = [(t.x + t.width / 2) / self.width for t in rows]
        if max(centers) - min(centers) > 0.10:
            return []                      # 不在同一列
        return rows

    def _yellow_buttons(self):
        """找屏幕下半部分的黄色按钮框（事件选项 / 确认按钮）。

        事件按钮是「白底 + 黄色圆角边框」，所以用黄色 mask 找外框，
        再做闭运算把文字/图标造成的断口补上。返回 (x, y, w, h) 列表。
        """
        frame = self.frame
        if frame is None:
            return []
        h, w = frame.shape[:2]
        y0 = int(h * 0.60)
        roi = frame[y0:, :]
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        mask = ((r > 200) & (g > 150) & (b < 120)).astype(np.uint8)
        kx = max(3, w // 240)
        ky = max(3, h // 240)
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)))
        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
        boxes = []
        for i in range(1, n):
            x, y, ww, hh, _area = stats[i]
            if ww < w * 0.12 or hh < h * 0.045:
                continue
            if ww > w * 0.95 or hh > h * 0.35:
                continue
            boxes.append((int(x), int(y + y0), int(ww), int(hh)))
        return boxes

    def _has_white_band(self, t):
        """文字所在的那条横带是不是白底（事件选项按钮）。

        竖向 3 选 1 的真按钮是「白底彩边」；训练动画的「训练加成/表情加成」
        是深色背景上的文字，白底占比很低。用这一条把两者分开。
        """
        frame = self.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        cy = int(t.y + t.height / 2)
        y1, y2 = max(0, cy - 10), min(h, cy + 10)
        x1, x2 = int(w * 0.22), int(w * 0.78)
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return False
        b = roi[:, :, 0].astype(int)
        g = roi[:, :, 1].astype(int)
        r = roi[:, :, 2].astype(int)
        bright = (b > 195) & (g > 190) & (r > 180)
        return float(bright.mean()) > 0.50
