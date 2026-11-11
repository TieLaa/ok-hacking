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

2026-11-11 重写（旧版是「盲拖 + 连续两次空页就停」，实跑 53/192 就收工，
到顶和到底分不出来，还会空拖）：
- 几何实测（1548×871 客户区）：卡片 140×139、行距/列距 165、
  行心 y=314/479/644/773（第 4 行只露上半截，点可见中心）、
  列心 x=157/322/487/653/818/983；
- **每拖一次都抓帧**，用像素级模板匹配量出实际位移：
  位移 ≈ 0 → 到顶/到底，立刻停，不再空拖；
- 拖动固定给 330px（2 行），实测位移带小数时用「内容行号 + 累计位移」定位，
  只点没点过的行；小数位移也能点回卡片中心；
- 整屏都是灰色占位箱（未解锁）→ 到底，停；占位屏拖不动时切页签复位；
- 「点亮 / 占位 / 空格」判据：点亮卡有品质底色，饱和度均值 27~114；
  灰占位箱饱和度 1.2；纯空格 1.5。用饱和度做闸门，占位箱和空格都不点。
"""

import os

import cv2
import numpy as np
from ok import BaseTask

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "logs", "captures", "codex")

# ---- 实测几何（1548×871，2026-11-11）----
COLS = (157, 322, 487, 653, 818, 983)      # 6 列中心
# 每行**可交互矩形的上下沿**（不是卡片几何中心）：第 4 行被面板裁在 741..805，
# 点它的几何中心 809 会落到面板外，必须点可见矩形的中心 773。
ROW_BANDS = ((245, 383), (410, 548), (575, 714), (741, 805))
ROW_CLICK_Y = tuple((a + b) // 2 for a, b in ROW_BANDS)   # 314 / 479 / 644 / 773
PANEL_BOTTOM = ROW_BANDS[-1][1]            # 805，面板可视下沿
ROW_PITCH = 165                            # 行距 = 列距
CARD_HW, CARD_HH = 70, 69                  # 卡片半宽/半高（140×139）
GRID = (88, 245, 1053, PANEL_BOTTOM)       # 网格区域 x1,y1,x2,y2
GAP_POINT = (240, 390)                     # 行列间隙里的一点，取面板底色
DETAIL = (0.70, 0.22, 1.00, 0.92)          # 右侧详情面板（相对坐标）
# 一次滚 2 行（330px）：可视 3 个整行 + 1 个半行，留 1 行重叠防漏。
DRAG_ROWS = 2
DRAG_PX = ROW_PITCH * DRAG_ROWS
DRAG_X = COLS[2]                           # 拖动落在第 3 列卡片中心线上
DRAG_FROM_Y = ROW_CLICK_Y[2]               # 644
DRAG_TO_Y = DRAG_FROM_Y - DRAG_PX          # 314
MAX_PAGES = 64                             # 只看防死循环；到底由位移/空屏判
EMPTY_STOP = 3                             # 连续几屏没新品才判到底（旧版 2 太急）
NO_MOVE_PX = 25                            # 位移小于这么多 = 拖不动
TAB_Y = 167
TAB_NUGGET = 650                           # 「扭蛋」页签
TAB_ITEM = 430                             # 「商品」页签


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    # ---------- 画面 ----------

    def _grid_gray(self):
        """网格区域缩成 96×112 的灰度图，用来量位移。"""
        frame = self.frame
        if frame is None:
            return None
        x1, y1, x2, y2 = GRID
        roi = frame[y1:y2, x1:x2]
        if roi.ndim == 3:
            roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return cv2.resize(roi, (96, 112), interpolation=cv2.INTER_AREA)

    def _measure_shift(self, prev, cur):
        """cur 相对 prev 的位移（正 = 内容向上移）。返回 (px, 匹配误差)。"""
        if prev is None or cur is None:
            return 0, 1e9
        h = prev.shape[0]
        scale = (GRID[3] - GRID[1]) / float(h)
        # d=0 必须参与比较，否则「没动」会被硬凑成某个位移
        best = (0, np.abs(cur.astype(np.int16)
                          - prev.astype(np.int16)).mean())
        for d in range(1, int(h * 0.9)):
            up = np.abs(cur[0:h - d].astype(np.int16)
                        - prev[d:h].astype(np.int16)).mean()
            if up < best[1]:
                best = (d * scale, up)
            down = np.abs(cur[d:h].astype(np.int16)
                          - prev[0:h - d].astype(np.int16)).mean()
            if down < best[1]:
                best = (-d * scale, down)
        return best

    def _bg_color(self):
        """面板底色：取行列间隙里的一小块，取中位色。"""
        frame = self.frame
        x, y = GAP_POINT
        patch = frame[y - 6:y + 6, x - 6:x + 6]
        return np.median(patch.reshape(-1, patch.shape[-1]), axis=0)

    def _has_card(self, cx, cy, bg):
        """这一格是不是**点亮**的图鉴（灰色占位箱不算）。

        判据（2026-11-11 实测 1548×871）：
        - 点亮卡有品质底色：饱和度均值 27~114；
        - 未解锁的灰色占位箱：饱和度均值 1.2（但「非背景占比」高达 0.72，
          光看对比度会把它当卡片，导致到底了还继续点）；
        - 纯空格：饱和度 1.5、占比 0.04。
        所以用**饱和度**做闸门，占比只是兜底。
        """
        frame = self.frame
        y1, y2 = max(0, cy - 30), min(PANEL_BOTTOM, cy + 30)
        x1, x2 = max(0, cx - 30), min(self.width, cx + 30)
        patch = frame[y1:y2, x1:x2]
        if patch.size == 0 or patch.ndim != 3:
            return False
        sat = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[:, :, 1]
        if float(sat.mean()) < 15:
            return False
        dist = np.abs(patch.astype(np.int16) - bg.reshape(1, 1, -1)).sum(axis=2)
        return float((dist > 45).mean()) >= 0.20

    def _detail_lines(self):
        """右侧详情面板的文字，按 y 排序（第一行是商品名）。"""
        x, y, tx, ty = DETAIL
        boxes = self.ocr(x=x, y=y, to_x=tx, to_y=ty) or []
        boxes = [b for b in boxes if getattr(b, "name", "").strip()]
        boxes.sort(key=lambda b: b.y)
        return [b.name.strip() for b in boxes]

    # ---------- 拖动 ----------

    def _drag(self, up=True, distance=None):
        """拖一行，返回实际位移（px，正 = 内容上移）。"""
        x = DRAG_X
        dist = DRAG_PX if distance is None else int(round(distance))
        if up:
            start, end = DRAG_FROM_Y, DRAG_FROM_Y - dist
        else:
            start, end = DRAG_TO_Y, DRAG_TO_Y + dist
        before = self._grid_gray()
        self.swipe(x, start, x, end, duration=1.0, after_sleep=0.6)
        after = self._grid_gray()
        shift, err = self._measure_shift(before, after)
        self.log_info("拖动：目标 %dpx，实测 %.0fpx（%.2f 行，匹配误差 %.1f）"
                      % (dist, shift, shift / float(ROW_PITCH), err))
        return shift

    def _lit_count(self):
        """当前屏有几格是点亮的（占位箱/空格都算 0）。"""
        bg = self._bg_color()
        return sum(1 for y in ROW_CLICK_Y for x in COLS
                   if self._has_card(x, y, bg))

    def _tab_reset(self):
        """切到「扭蛋」再切回「商品」——实机验证会把列表重新从顶部渲染。

        2026-11-11 实测：滚到占位屏（未解锁的灰箱）之后，任何点位的拖拽
        和滚轮都不生效（四个点位都只有 ±15px），只有切页签能复位。
        """
        self.click(TAB_NUGGET, TAB_Y, after_sleep=0.9)
        self.click(TAB_ITEM, TAB_Y, after_sleep=0.9)
        self.log_info("切页签复位 → 点亮格 %d" % self._lit_count())

    def _scroll_to_top(self):
        """拖到顶：位移为 0 立刻停；如果当前屏压根没有点亮商品（占位屏），
        先切页签复位。"""
        if self._lit_count() == 0:
            self.log_info("当前屏没有点亮商品（占位/空白）→ 切页签复位")
            self._tab_reset()
        for _ in range(MAX_PAGES):
            if abs(self._drag(up=False)) < NO_MOVE_PX:
                self.log_info("已经到顶")
                return
        self.log_warning("拖了很多次都没到顶，按当前位置继续")

    # ---------- 主流程 ----------

    def run(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, "codex_items.tsv")
        # 续采：上一次已经抓到的不丢（老值先读进来，按名字去重后一起写回）。
        seen, rows = {}, []
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) != 4:
                        continue
                    page_s, cell, name, detail = parts
                    seen[name] = detail.split(" | ")
                    rows.append((page_s, cell, name, detail))
            self.log_info("续采：已有 %d 条" % len(rows))

        self._scroll_to_top()
        # 用「内容行号」做唯一坐标：total_shift = 第一屏顶行滚过的总像素。
        # 第 r 行的屏幕中心 = 314 + r*165 - offset，offset = total_shift % 165。
        # 这样小数位移也能点回卡片中心，不会落到行间隙。
        total_shift = 0.0
        clicked = set()                  # 已经点过的内容行号
        drag_px = float(DRAG_PX)
        for screen in range(MAX_PAGES):
            bg = self._bg_color()
            first = int(total_shift // ROW_PITCH)
            offset = total_shift - first * ROW_PITCH
            visible = []                 # [(内容行号, 可点击的屏幕 y)]
            for i in range(6):           # 最多 6 行会和可视区有交集
                y = ROW_CLICK_Y[0] + i * ROW_PITCH - offset
                y1, y2 = max(245, y - CARD_HH), min(PANEL_BOTTOM, y + CARD_HH)
                if y2 - y1 < 40:         # 露出太少，点了也容易点到面板外
                    continue
                visible.append((first + i, int((y1 + y2) // 2)))

            screen_lit = any(self._has_card(cx, cy, bg)
                             for _, cy in visible for cx in COLS)
            if not screen_lit:
                self.log_info("整屏都是空图鉴 → 到底了")
                break

            new_this = 0
            for row_idx, cy in visible:
                if row_idx in clicked:   # 上一屏已经扫过的重叠行，跳过
                    continue
                lit = [(ci, cx) for ci, cx in enumerate(COLS)
                       if self._has_card(cx, cy, bg)]
                if not lit:              # 整行都是占位/空格，没什么可采
                    clicked.add(row_idx)
                    continue
                got = False
                for ci, cx in lit:
                    self.click(cx, cy, after_sleep=0.30)
                    lines = self._detail_lines()
                    if len(lines) < 2:
                        continue
                    got = True
                    name = lines[0]
                    if name in seen:
                        continue
                    seen[name] = lines
                    rows.append((screen, "r%d-c%d" % (row_idx, ci),
                                 name, " | ".join(lines)))
                    new_this += 1
                # 有卡片却一条详情都没读出来（点歪/OCR 抖了）→ 不标记已扫，
                # 让它留在重叠区，下一屏再试一次。
                if got:
                    clicked.add(row_idx)
                else:
                    self.log_warning("第 %d 行有卡片但没读到详情，下一屏重试"
                                     % row_idx)
            self.log_info("第 %d 屏 +%d 条（累计 %d，行 %d~%d，偏移 %.0fpx）"
                          % (screen + 1, new_this, len(rows),
                             visible[0][0], visible[-1][0], offset))

            shift = self._drag(up=True, distance=drag_px)
            if abs(shift) < NO_MOVE_PX:
                self.log_info("拖不动了 → 到底（本屏已采完）")
                break
            total_shift += shift
            # 自适应：这次多滑了就少拖一点，让实测位移收敛到 2 行
            drag_px = min(620.0, max(180.0,
                                     drag_px + (DRAG_PX - shift) * 0.8))

        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for page, cell, name, detail in rows:
                f.write("%s\t%s\t%s\t%s\n" % (page, cell, name, detail))
        self.log_info("采集完成：%d 条 → %s" % (len(rows), path))
        return {"items": len(rows), "out": path}
