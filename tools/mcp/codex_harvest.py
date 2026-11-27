"""MCP 入口：图鉴「商品」页批量采集（逐格点开 → OCR 详情 → TSV）。

为什么必须走 MCP（2026-11-08 实测）：游戏是管理员权限运行，普通 shell 的
鼠标事件会被 UIPI 丢弃（截图能抓、点击进不去）。这个任务跑在管理员权限的
runtime 里，`self.click` 才真的点得动。

用法：
    1. （管理员）启动 runtime：tools\\restart_mcp_runtime.ps1
    2. 游戏里打开 图鉴 →「商品」页、窗口在前台，并停在第一页
       （脚本不再自动回顶）
    3. python tools/mcp/client.py tools/mcp/codex_harvest.py

产物：logs/captures/codex/codex_items.tsv
      每行 `页码\t格子\t名字\t详情`——详情里的换行用 `\n` 转义
      （单元格不能含真换行；老文件仍是 ` | ` 拼接，读取时两种都认）。

2026-11-11 重写（旧版是「盲拖 + 连续两次空页就停」，实跑 53/192 就收工，
到顶和到底分不出来，还会空拖）：
2026-11-24 修正（用户实机确认）：
- 一页只有 **3 行 × 6 列 = 18 张**可点卡片；第 4 行只是底部预览，点不中；
- 列坐标固定 x=157/322/487/653/818/983；行中心每次点击前按卡片底色
  重新扫描（卡片约 139~140px、间隙 25~26px），不再写死屏幕 y：
  点击/翻页都会让列表相对屏幕偏移，写死会逐页累积误差；
- **每拖一次都抓帧**，只用来判断「有没有动」：位移 ≈ 0 → 到顶/到底，立刻停；
- 翻页固定 495px（3 行）：下一页从第 4 行开始，两页不重叠；
  拖动用「拖-停-放」：连续拖 495px，停 0.2s 再松手，松手后等列表停住；
  **列坐标固定，行中心按当前帧量到的卡片行取**；
- 每次点击后必须确认**详情首行变了**才算成功：点不中就重试一次，
  仍不变就留到下一页重试，不再把「读到旧详情」当成成功；
- 详情面板出现「继续训练有机会解锁更多商品」/灰色占位箱 → 扫完了，停；
- 「点亮 / 占位 / 空格」判据：点亮卡有品质底色，饱和度均值 27~114；
  灰占位箱饱和度 1.2；纯空格 1.5。用饱和度做闸门，占位箱和空格都不点。
"""

import os

import cv2
import numpy as np
from ok import BaseTask

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "logs", "captures", "codex")


def join_detail(lines):
    """详情行写进 TSV 单元格：单元格不能含真换行，用 `\\n` 转义。"""
    return "\\n".join(lines)


def split_detail(detail):
    """兼容两种历史格式：新数据 `\\n` 转义，老数据 ` | ` 拼接。"""
    if "\\n" in detail:
        return detail.split("\\n")
    return detail.split(" | ")

# ---- 实测几何（1548×871，2026-11-24 用户确认）----
# 图鉴「商品」页一页只有 3 行 × 6 列 = 18 张可点卡片；
# 第 4 行只是底部露出的预览，点不中，不能算进识别区域。
# 每张卡只有**正中心**可靠：黄色选中框的中心。
COLS = (157, 322, 487, 653, 818, 983)      # 6 列中心
ROW_CLICK_Y = (314, 479, 644)              # 3 行中心（选中框中心，不是卡片底色中心）
ROW_PITCH = 165                            # 行距 = 列距
GRID = (88, 245, 1053, 714)                # 3 行可交互区 x1,y1,x2,y2
PANEL_BOTTOM = GRID[3]                     # 714，第 4 行预览不参与
GAP_POINT = (240, 390)                     # 行列间隙里的一点，取面板底色
DETAIL = (0.70, 0.22, 1.00, 0.92)          # 右侧详情面板（相对坐标）
# 一次翻 3 行：495px（3 × 165）；不用 650，650 会过 4 行。
DRAG_ROWS = 3
DRAG_PX = ROW_PITCH * DRAG_ROWS
DRAG_HOLD = 0.2                             # 到位置停 0.2s 再松手
DRAG_X = COLS[2]                           # 拖动落在第 3 列卡片中心线上
DRAG_UP_FROM_Y = ROW_CLICK_Y[2]            # 644，向上翻页的起点
MAX_PAGES = 64                             # 只看防死循环；到底由位移/空页判
EMPTY_STOP = MAX_PAGES                       # 不再按 3 页早停；只由末尾灰框/拖不动停
MOVE_DIFF = 2.0                            # 前后帧平均差超过它 = 列表真的动了
END_MARKS = ("继续训练有机会", "解锁更多")   # 图鉴末尾灰框的提示文案


class Task(BaseTask):
    # 子类可覆盖：页签 x 坐标 / 输出文件名（扭蛋页复用同一套采集逻辑）。
    TAB_X = 430
    OUT_NAME = "codex_items.tsv"

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def _codex_open(self):
        """当前是不是「图鉴详情」页；不是就拒绝采集，避免把剧情文字当卡片。"""
        boxes = self.ocr(x=0.0, y=0.0, to_x=1.0, to_y=1.0) or []
        text = "".join(getattr(b, "name", "").strip() for b in boxes)
        if "事件播放确认" in text or "确定要播放此事件吗" in text:
            return False
        return "图鉴详情" in text

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

    def _wait_still(self, frames=60):
        """等列表真正停住：连续两帧的网格差 < 0.5 才返回。

        松手后列表还会滑；不等它停就拖下一次，第二次的前一段会被余滑吃掉。
        这里按画面状态等，不设固定延迟。
        """
        prev = self._grid_gray()
        if prev is None:
            return
        for _ in range(frames):
            self.next_frame()
            cur = self._grid_gray()
            if cur is None:
                return
            diff = float(np.abs(cur.astype(np.int16)
                                - prev.astype(np.int16)).mean())
            if diff < 0.5:
                return
            prev = cur

    def _row_centers(self):
        """扫描卡片底色，返回当前 3 行卡片的实际中心 y；失败回退固定值。

        每页/每次点击都可能让列表相对屏幕移动几像素；固定 y=314/479/644
        只在列表不动时成立。这里在左列卡片底色上找连续的 3 段（卡片约
        139~140px、间隙 25~26px），取中间一段的中心，再 ±165。
        """
        frame = self.frame
        if frame is None:
            return ROW_CLICK_Y
        sat = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 1]
        for x in (100, 265, 430, 595, 760, 925):
            strip = sat[220:730, max(0, x - 4):x + 4].mean(axis=1)
            on = strip > 12
            runs = []
            start = None
            for i, v in enumerate(on):
                if v and start is None:
                    start = i
                elif not v and start is not None:
                    if i - start >= 20:
                        runs.append((start + 220, i - 1 + 220))
                    start = None
            if start is not None:
                runs.append((start + 220, 729))
            full = [(a, b) for a, b in runs if b - a >= 100]
            mids = [round((a + b) / 2) for a, b in full]
            mid = next((m for m in mids if 380 <= m <= 580), None)
            if mid is not None:
                return (mid - ROW_PITCH, mid, mid + ROW_PITCH)
        return ROW_CLICK_Y

    def _bg_color(self, rows=None):
        """面板底色：取行列间隙里的一小块，取中位色。"""
        frame = self.frame
        x, y = GAP_POINT
        if rows:
            y = int((rows[0] + rows[1]) / 2)
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

    def _end_marker(self):
        """详情面板是不是「继续训练有机会解锁更多商品」的末尾提示。"""
        text = "".join(self._detail_lines())
        return any(mark in text for mark in END_MARKS)

    def _is_placeholder(self, cx, cy, bg):
        """这一格是不是灰色未解锁占位箱（不是点亮卡，也不是空格）。"""
        frame = self.frame
        y1, y2 = max(0, cy - 30), min(PANEL_BOTTOM, cy + 30)
        x1, x2 = max(0, cx - 30), min(self.width, cx + 30)
        patch = frame[y1:y2, x1:x2]
        if patch.size == 0 or patch.ndim != 3:
            return False
        sat = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[:, :, 1]
        if float(sat.mean()) >= 15:
            return False
        dist = np.abs(patch.astype(np.int16)
                     - bg.reshape(1, 1, -1)).sum(axis=2)
        return float((dist > 45).mean()) >= 0.20

    def _check_page_end(self, rows, bg):
        """扫到末尾灰框提示就停：先看当前详情，再点第一个灰框确认。"""
        if self._end_marker():
            self.log_info("详情面板已是末尾提示 → 扫完了")
            return True
        cells = [(cy, cx) for cy in rows for cx in COLS]
        first = None
        for i, (cy, cx) in enumerate(cells):
            if self._is_placeholder(cx, cy, bg):
                first = (cy, cx)
                # 灰框后面还有点亮卡 → 只是中间的未解锁格，不是末尾。
                if any(self._has_card(x, y, bg)
                       for y, x in cells[i + 1:]):
                    return False
                break
        if first is None:
            return False
        cy, cx = first
        self.click(cx, cy, after_sleep=0.5)
        if self._end_marker():
            self.log_info("扫到「继续训练有机会解锁更多商品」→ 扫完了，停止")
            return True
        return False

    # ---------- 拖动 ----------

    def _drag(self, distance=None):
        """向上拖一页，返回前后帧的平均差（> MOVE_DIFF = 列表确实动了）。

        连续拖 495px 到目标，停 DRAG_HOLD 秒再松开；松手后等列表停住。
        """
        x = DRAG_X
        dist = DRAG_PX if distance is None else int(round(distance))
        start = DRAG_UP_FROM_Y
        end = start - dist
        before = self._grid_gray()
        # 拖-停-放：连续拖到目标，停 0.2s 再松手；松手后等列表停住。
        self.swipe(x, start, x, end, duration=1.0,
                   settle_time=DRAG_HOLD, after_sleep=0)
        self._wait_still()
        self.next_frame()
        after = self._grid_gray()
        if before is None or after is None:
            return 0.0
        diff = float(np.abs(after.astype(np.int16)
                            - before.astype(np.int16)).mean())
        self.log_info("拖动：内容变化 %.1f（%s）"
                      % (diff, "有移动" if diff >= MOVE_DIFF else "没动"))
        return diff

    # ---------- 主流程 ----------

    def _select_card(self, cx, cy):
        """点卡片正中心，返回详情行；详情首行没变 = 没点中，返回 None。

        旧版只看 len(lines) >= 2 就算成功，点不中时读到的是上一张卡的详情，
        会把整行标成已扫。这里必须看到详情首行变化才认。
        """
        prev = self._detail_lines()
        prev_name = prev[0] if len(prev) >= 2 else None
        # 当前详情有效时只点一次：详情没变通常表示这一格本来就是选中状态，
        # 留给本页最后的 pending 再补点；不要在这里立刻重复点。
        attempts = 2 if prev_name is None else 1
        for attempt in range(attempts):
            self.click(cx, cy, after_sleep=0.45 if attempt == 0 else 0.75)
            cur = self._detail_lines()
            if len(cur) >= 2 and cur[0] != prev_name:
                return cur
        return None

    @staticmethod
    def _append_row(path, row):
        """每采一条立刻落盘：中途停/异常不丢本次结果。"""
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write("%s\t%s\t%s\t%s\n" % row)

    def _record(self, lines, page, ri, ci, seen, rows, path):
        """记录一条；同名已采过返回 0。"""
        name = lines[0]
        if any(mark in name for mark in END_MARKS):
            return 0
        if name in seen:
            return 0
        seen[name] = lines
        row = (page, "r%d-c%d" % (ri, ci), name, join_detail(lines))
        rows.append(row)
        self._append_row(path, row)
        self.log_info("第 %d 页 r%d-c%d：%s" % (page + 1, ri, ci, name))
        return 1

    def run(self):
        if not self._codex_open():
            self.log_warning(
                "当前不是「图鉴详情」页 → 不采集；请先打开 图鉴 → 商品/扭蛋")
            return {"items": 0, "out": None}
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, self.OUT_NAME)
        # 续采：上一次已经抓到的不丢（老值先读进来，按名字去重后一起写回）。
        seen, rows = {}, []
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) != 4:
                        continue
                    page_s, cell, name, detail = parts
                    # 旧文件里可能残留「继续训练有机会解锁更多」这行末尾提示，
                    # 它不是商品，读入时丢掉。
                    if any(mark in name for mark in END_MARKS):
                        continue
                    seen[name] = split_detail(detail)
                    rows.append((page_s, cell, name, detail))
            self.log_info("续采：已有 %d 条" % len(rows))

        # 开始前请把列表放在第一页（不再自动回顶）。
        # 一页固定 3×6 = 18 个中心；翻页固定 3 行（495px），
        # 下一页从第 4 行开始，点击坐标不参与位移修正。
        empty_pages = 0
        for page in range(MAX_PAGES):
            new_this = 0
            pending = []
            self.log_info("第 %d 页行中心 %s"
                          % (page + 1, self._row_centers()))
            order = [(ri, ci) for ri in range(len(ROW_CLICK_Y))
                     for ci in range(len(COLS))]
            if page == 0:
                # 第一页开局时 r0c0 往往已经是选中状态，先点别的格，
                # 把它放到最后点，避免同一张卡连点两次/翻页前补点。
                order = order[1:] + order[:1]
            for ri, ci in order:
                cx = COLS[ci]
                # 每次点击前重新量行中心：选中/详情刷新会让列表抖几像素。
                row_y = self._row_centers()
                bg = self._bg_color(row_y)
                cy = row_y[ri]
                if (self._is_placeholder(cx, cy, bg)
                        or not self._has_card(cx, cy, bg)):
                    continue
                lines = self._select_card(cx, cy)
                if lines is None:
                    # 可能这一格本来就是选中状态（点它详情不变），
                    # 先记下来，等本页其他格点完再补点一次。
                    pending.append((ri, ci, cx))
                    continue
                new_this += self._record(
                    lines, page, ri, ci, seen, rows, path)
            for ri, ci, cx in pending:
                row_y = self._row_centers()
                lines = self._select_card(cx, row_y[ri])
                if lines is None:
                    self.log_warning("第 %d 页 r%d-c%d 重试仍没变，跳过"
                                     % (page + 1, ri, ci))
                    continue
                new_this += self._record(
                    lines, page, ri, ci, seen, rows, path)
            self.log_info("第 %d 页 +%d 条（累计 %d）"
                          % (page + 1, new_this, len(rows)))
            row_y = self._row_centers()
            if self._check_page_end(row_y, self._bg_color(row_y)):
                break
            empty_pages = empty_pages + 1 if new_this == 0 else 0
            if empty_pages >= EMPTY_STOP:
                self.log_info("连续 %d 页没有新品 → 到底" % EMPTY_STOP)
                break

            diff = self._drag()
            if diff < MOVE_DIFF:
                self.log_info("拖不动了 → 到底（本页已采完）")
                break

        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for page, cell, name, detail in rows:
                f.write("%s\t%s\t%s\t%s\n" % (page, cell, name, detail))
        self.log_info("采集完成：%d 条 → %s" % (len(rows), path))
        return {"items": len(rows), "out": path}
