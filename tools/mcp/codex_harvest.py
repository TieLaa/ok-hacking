"""MCP 入口：图鉴「商品」页批量采集（逐格点开 → OCR 详情 → TSV）。

为什么必须走 MCP（2026-11-08 实测）：游戏是管理员权限运行，普通 shell 的
鼠标事件会被 UIPI 丢弃（截图能抓、点击进不去）。这个任务跑在管理员权限的
runtime 里，`self.click` 才真的点得动。

用法：
    1. （管理员）启动 runtime：tools\\restart_mcp_runtime.ps1
    2. 游戏里打开 图鉴 →「商品」页、窗口在前台
    3. python tools/mcp/client.py tools/mcp/codex_harvest.py

产物：logs/captures/codex/codex_items.tsv
     每行 `页码\t格子\t名字\t详情(用 | 连接)`——由会话读它建商店登记表。
"""

import os

from ok import BaseTask

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "logs", "captures", "codex")

# 图鉴「商品」页网格（1549×871 客户区实测坐标）
COLS = (170, 327, 484, 641, 798, 955)
ROWS = (300, 465, 625, 745)     # 第 4 行半露，点得到就采
# 右侧详情面板（相对坐标，0~1）
DETAIL = (0.70, 0.22, 1.00, 0.92)
SCROLL_COUNT = -3       # 每次翻页滚几档；**负 = 向下**（ok 的 scroll 正数是向上）
# 2026-11-08 实测：图鉴列表**不认滚轮**（scroll 任何档位都不动），
# 只认拖拽（swipe）。一次只拖**一行**（≈165px，行高），慢拖防惯性甩飞；
# 逐行推进 + 按名字去重，比一次翻三行更不容易错位漏格。
SWIPE_FROM = (484, 650)
SWIPE_TO = (484, 485)
MAX_PAGES = 24
MAX_PAGES = 12


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def _detail_lines(self):
        """右侧详情面板的文字，按 y 排序（第一行是商品名）。"""
        x, y, tx, ty = DETAIL
        boxes = self.ocr(x=x, y=y, to_x=tx, to_y=ty) or []
        boxes = [b for b in boxes if getattr(b, "name", "").strip()]
        boxes.sort(key=lambda b: b.y)
        return [b.name.strip() for b in boxes]

    def run(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        # 先回顶：往下拖几次（列表向上滚），避免上次采集停在半路
        for _ in range(4):
            self.swipe(SWIPE_TO[0], SWIPE_TO[1],
                       SWIPE_FROM[0], SWIPE_FROM[1], duration=0.6)
            self.sleep(0.4)
        seen, rows = {}, []
        empty_pages = 0
        for page in range(MAX_PAGES):
            new_this_page = 0
            for ri, cy in enumerate(ROWS):
                for ci, cx in enumerate(COLS):
                    self.click(cx, cy, after_sleep=0.35)
                    lines = self._detail_lines()
                    if len(lines) < 2:
                        continue                      # 空格子/未解锁
                    name = lines[0]
                    if name in seen:
                        continue
                    seen[name] = lines
                    rows.append((page, f"{ri}-{ci}", name, " | ".join(lines)))
                    new_this_page += 1
            # 翻页：**拖拽**（图鉴列表不认滚轮，2026-11-08 探针实测）
            self.swipe(SWIPE_FROM[0], SWIPE_FROM[1],
                       SWIPE_TO[0], SWIPE_TO[1], duration=0.6)
            self.sleep(0.6)
            self.log_info(f"图鉴采集：第 {page + 1} 页 +{new_this_page} 条"
                          f"（累计 {len(rows)}）")
            if new_this_page == 0:
                empty_pages += 1
                if empty_pages >= 2:
                    break
            else:
                empty_pages = 0

        path = os.path.join(OUT_DIR, "codex_items.tsv")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for page, cell, name, detail in rows:
                f.write(f"{page}\t{cell}\t{name}\t{detail}\n")
        self.log_info(f"图鉴采集完成：{len(rows)} 条 → {path}")
        return {"items": len(rows), "out": path}
