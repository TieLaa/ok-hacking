"""篮球少女任务基类：文字优先的界面操作。

这个游戏的 UI 特点：几乎所有可交互元素都有中文大字标签（地点、训练科目、
跳过比赛、确认…），而 3D 场景反而没有稳定纹理。所以这里统一走 OCR 找文字
再点文字框，只在「大按钮」这种无文字目标上退化成区域点击。
"""

import random
import re
import time

from ok import BaseTask

from src.tasks.ui import Defs


class BaseGirlsTask(BaseTask):

    # ---------- 文字 ----------

    def _ocr_region(self, region_name=None, box=None):
        if box is None and region_name:
            box = self.box_of_screen(*Defs.region(region_name))
        try:
            return self.ocr(box=box) or []
        except Exception as e:
            self.log_debug(f"ocr failed: {e}")
            return []

    @staticmethod
    def match_text(texts, names):
        """在一批 OCR 结果里找名字匹配的框。names 支持字符串 / 元组 / 正则。"""
        if isinstance(names, str):
            names = (names,)
        for name in names:
            if hasattr(name, "search"):
                for t in texts:
                    if name.search(t.name):
                        return t
                continue
            for t in texts:
                if name in t.name:
                    return t
        # 兜底：整段拼接后匹配，处理「跳 过 比 赛」被拆成多个框
        joined = "".join(t.name for t in texts)
        for name in names:
            if not hasattr(name, "search") and name in joined and texts:
                return max(texts, key=lambda t: len(t.name))
        return None

    def find_text(self, names, region_name=None, box=None):
        return self.match_text(self._ocr_region(region_name, box), names)

    def texts_in_region(self, texts, region_name):
        """把整帧 OCR 结果过滤到某个区域内（按文本框中心点）。"""
        x1, y1, x2, y2 = Defs.region(region_name)
        picked = []
        for t in texts:
            cx = (t.x + t.width / 2) / self.width
            cy = (t.y + t.height / 2) / self.height
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                picked.append(t)
        return picked

    def click_text(self, names, region_name=None, box=None, after_sleep=0.6, must=True):
        target = self.find_text(names, region_name, box)
        if target is None:
            if must:
                self.log_warning(f"未找到文字: {names}")
            return False
        self.click_box(target, after_sleep=after_sleep)
        self.log_info(f"点击「{target.name}」")
        return True

    def click_and_expect(self, x, y, expect, timeout=5.0, poll=0.5):
        """点一下，然后等「下一页该出现的文字」出现。

        点击丢失是这个游戏的常态（启程就丢过），所以每次点击都要有反馈校验。
        不能用像素差判断——3D 大厅一直在动，随便两帧都不一样。
        返回 True 表示界面确实变了。
        """
        self.click(int(x), int(y))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sleep(poll)
            if self.match_text(self._ocr_region(), expect) is not None:
                return True
        return False

    # ---------- 顶栏状态 ----------

    def top_bar(self):
        """解析顶栏：周数 / 星期几 / 体力 / 金币。解析不到的字段为 None。"""
        texts = self._ocr_region("top_bar")
        joined = " ".join(t.name for t in texts)
        state = {"week": None, "day": None, "stamina": None, "stamina_max": None, "coin": None}

        if m := Defs.WEEK_RE.search(joined):
            state["week"] = int(m.group(1))
        if m := Defs.DAY_RE.search(joined):
            state["day"] = "日" if m.group(1) == "天" else m.group(1)
        if m := Defs.STAMINA_RE.search(joined.replace(" ", "")):
            state["stamina"], state["stamina_max"] = int(m.group(1)), int(m.group(2))
        return state

    # ---------- 大按钮 / 区域点击 ----------

    def click_region(self, region_name, after_sleep=0.8):
        box = self.box_of_screen(*Defs.region(region_name))
        x = box.x + box.width * random.uniform(0.3, 0.7)
        y = box.y + box.height * random.uniform(0.3, 0.7)
        self.click(int(x), int(y), after_sleep=after_sleep)
        return True

    def click_main_action(self, after_sleep=1.5):
        """右下角主按钮（训练 / VS 比赛）。没有文字标签，只能按区域点。"""
        self.log_info("点击右下角主按钮")
        return self.click_region("main_action", after_sleep=after_sleep)

    # ---------- 通用 ----------

    def is_loading(self):
        """加载/过场：出现百分比或「加载」字样时不要乱点。"""
        texts = self._ocr_region()
        for t in texts:
            if "加载" in t.name or re.search(r"^\d{1,3}%$", t.name.strip()):
                return True
        return False

    def dismiss_popup(self):
        """关掉公告/领奖/月卡这类弹窗。返回是否处理了。"""
        if self.click_text(Defs.POPUP_TEXTS, "dialog", after_sleep=0.8, must=False):
            return True
        return False

    def in_weekend_map(self):
        return bool(self.find_text(Defs.WEEKEND_PLACES[:2], "weekend_map"))

    def in_prepare_page(self):
        return bool(self.find_text(("跳过比赛", "前往比赛"), "result_page"))

    def in_result_page(self):
        return bool(self.find_text(("比赛胜利", "比赛失败", "获得", "奖励"), "result_page"))
