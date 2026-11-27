"""MCP：图鉴「扭蛋」页批量采集（复用商品采集器，OCR 定位页签 + 独立输出）。"""
import importlib

import codex_harvest

importlib.reload(codex_harvest)
from codex_harvest import Task as ItemTask  # noqa: E402


class Task(ItemTask):
    TAB_X = 650
    OUT_NAME = "codex_items_gacha.tsv"

    def _tab_active(self, x):
        """页签胶囊底色：激活是深蓝，未激活是浅色。"""
        patch = self.frame[150:185, max(0, x - 45):max(0, x - 15)]
        return float(patch.mean()) < 120

    def _click_tab(self, name):
        """按 OCR 文字框中心点击页签，不猜固定坐标。"""
        boxes = self.ocr(x=0.0, y=0.0, to_x=1.0, to_y=1.0) or []
        box = next((b for b in boxes if b.name.strip() == name), None)
        if box is None:
            self.log_warning("找不到页签「%s」" % name)
            return False
        self.click_box(box, after_sleep=1.5)
        self.log_info("点击页签「%s」" % name)
        return True

    def run(self):
        if not self._codex_open():
            self.log_warning("当前不是图鉴页 → 先打开 图鉴 → 扭蛋")
            return {"items": 0, "out": None}
        # 先切商品再切扭蛋，强制扭蛋列表从第一页重新渲染；都用 OCR 定位。
        for _ in range(3):
            self._click_tab("商品")
            self._click_tab("扭蛋")
            if self._tab_active(self.TAB_X):
                break
            self.sleep(2.0)
        if not self._tab_active(self.TAB_X):
            self.log_warning("扭蛋页签没有激活 → 不采集")
            return {"items": 0, "out": None}
        return super().run()
