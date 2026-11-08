"""MCP 探针：图鉴列表慢速小拖，观察每次实际步进（惯性/步长）。

用法：
    python tools/mcp/client.py tools/mcp/probe_codex_scroll.py
"""

import os

from ok import BaseTask

DETAIL = (0.70, 0.22, 1.00, 0.92)
CELL = (170, 300)
MID_X = 484


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def _name(self):
        x, y, tx, ty = DETAIL
        boxes = self.ocr(x=x, y=y, to_x=tx, to_y=ty) or []
        boxes = [b for b in boxes if getattr(b, "name", "").strip()]
        boxes.sort(key=lambda b: b.y)
        return boxes[0].name.strip() if boxes else None

    def run(self):
        out = []

        def probe(tag):
            self.click(CELL[0], CELL[1], after_sleep=0.4)
            out.append(f"{tag}: {self._name()}")

        # 回顶
        for _ in range(5):
            self.swipe(MID_X, 485, MID_X, 650, duration=0.8)
            self.sleep(0.35)
        probe("top")
        # 慢速小拖（165px，0.8s 防惯性）6 次，看步进
        for i in range(6):
            self.swipe(MID_X, 650, MID_X, 485, duration=0.8)
            self.sleep(0.5)
            probe(f"slow{i + 1}")
        # 对照：快拖（0.3s）
        self.swipe(MID_X, 650, MID_X, 485, duration=0.3)
        self.sleep(0.6)
        probe("fast1")
        for line in out:
            self.log_info("滚动探针 " + line)
        return {"probe": out}
