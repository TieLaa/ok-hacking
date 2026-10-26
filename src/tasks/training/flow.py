"""单拍状态机、存证、卡死检测。

从 TrainingTask 拆出；方法仍通过 self 访问任务状态。
禁止在这里 import TrainingTask（循环依赖），需要类时用 handler.__name__ 判断。
"""

import os
import random
import re
import threading
import time
from collections import deque

import cv2
import numpy as np

from src.tasks.ui import Defs  # noqa: F401
from src.tasks.training.constants import JPEG_PARAMS, PROJECT_ROOT, STYLE_PLACES  # noqa: F401
from src.util.emergency_stop import STOP


class TrainingFlowMixin:

    def run(self):
        self._steps = 0
        STOP.ensure_started()
        self.log_info(f"紧急停止热键：{STOP.label or '（未注册成功，用 ok-script 自带 F9）'}")
        max_steps = int(self.config.get("单局最大操作数", 400))
        interval = float(self.config.get("操作间隔(秒)", 0.8))
        stuck_seconds = float(self.config.get("卡死判定(秒)", 10))
        stuck_limit = max(3, int(stuck_seconds / max(interval, 0.05)))
        last_sig, stuck = None, 0
        self._last_dump = 0.0

        while self._steps < max_steps:
            if STOP.consume():
                self.log_warning(f"按下紧急停止键（{STOP.label}）→ 立即中断，鼠标已释放",
                                 notify=True)
                return False
            if not self._enabled:
                self.log_info("任务已停止")
                return False
            self._steps += 1

            texts = self._ocr_region()
            if not texts:
                self.log_debug("本帧没有识别到文字，等待")
                self.sleep(0.3)
                continue

            if self.is_loading(texts):
                self.sleep(0.3)
                continue

            # 界面指纹：同一页面每拍都一样，用来判「卡住了没」
            sig = self._signature(texts)
            if sig == last_sig:
                stuck += 1
            else:
                stuck, last_sig = 0, sig

            # ---- 点 3 次没变化 → 立刻进入 debug（用户 2026-10-26 口径）----
            # 不等到 10 秒卡死判定，也不让 handler 一直"命中但无效"地空转。
            if self._sig_after_click is not None:
                if sig == self._sig_after_click:
                    self._no_change += 1
                else:
                    self._no_change = 0
                    self._sig_after_click = None
                    self._stuck_rounds = 0

            if self._no_change >= 3:
                self._stuck_rounds += 1
                self.dump_debug(texts, f"nochange{self._no_change}")
                self.log_warning(
                    f"连续 {self._no_change} 次点击界面没变化（上次动作：{self._last_state}）"
                    f"→ 已存证 logs/debug/，点击屏幕中央重试")
                if self._stuck_rounds >= 3:
                    self.log_error("已经 3 轮「点不动」→ 停止任务，避免一直卡在同一界面")
                    return False
                self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)
                self._no_change = 0
                self._sig_after_click = sig
                continue

            # ---- 卡死检测必须**优先于所有 handler** ----
            # 否则会出现「handler 一直命中、但点了完全没反应」的死循环：
            # 例如剧情页的「跳过」是灰的，handle_dialog_continue 每拍都返回 True，
            # 于是连点 70+ 次、把 400 步全耗光（实训 2026-10-26）。
            if stuck >= stuck_limit:
                self.dump_unknown(texts, "stuck")
                self.log_warning(f"连续 {stuck + 1} 拍没变化 → 强制点一下继续")
                self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)
                stuck, last_sig = 0, None
                continue

            # ---- 一局的终点：进入注册球员编队就算跑完（用户 2026-10-26）----
            if self.in_registration_page(texts):
                self.log_info("已到一局终点（注册球员编队）→ 任务完成", notify=True)
                return True

            if self._handle_any(texts):
                self.sleep(interval)
                continue

            # ---- 兜底：不认识的界面也**让流程继续走**，不要卡死 ----
            # 存证限流：同一段时间最多 3 秒存一张，避免动画期间刷出几十张图
            if stuck == 0 and time.monotonic() - self._last_dump > 3:
                self.dump_unknown(texts, "unknown")
                self._last_dump = time.monotonic()
                self.log_warning("没见过这个界面 → 已存证，点一下继续")

            # 兜底点击也要参与「点 3 次没变化」检测，否则不认识的界面
            # 会一直静默点中央，既不 debug 也不停（2026-10-26 公园详情页踩过）。
            self._sig_after_click = sig
            self._last_state = "unknown"
            # 点画面中央：对话页能推进、空白处能关弹窗、大部分过场也吃这一下
            self.click(int(self.width * 0.5), int(self.height * 0.5), after_sleep=interval)

        self.log_warning(f"达到最大操作数 {max_steps}，停止")
        return False

    @staticmethod
    def _signature(texts):
        """界面指纹：只看**文字内容**，不看位置。

        坑（2026-10-26）：之前把坐标也算进指纹，动画/滚动时文字位置抖动 20px 就变，
        结果每一拍都被当成"新界面"，疯狂存证、stuck 永远为 0。
        """
        return hash("|".join(sorted(t.name for t in texts)))

    def dump_debug(self, texts, tag):
        """点不动的时候存一份「调试包」：截图 + OCR + 最近动作 + 线程。

        用户口径（2026-10-26）：连续点 3 次界面没变化就要开始 debug。
        产物在 logs/debug/<时间>_<tag>/，logs 已被 .gitignore 忽略。
        """
        try:
            root = os.path.join(PROJECT_ROOT, "logs", "debug")
            folder = os.path.join(root, f"{time.strftime('%m%d-%H%M%S')}_{tag}")
            os.makedirs(folder, exist_ok=True)

            frame = self.frame
            if frame is not None:
                # 存 JPEG 不存 PNG：同一帧 PNG 约 2.4 MB、JPEG 约 0.36 MB，读图更快。
                # JPEG_PARAMS 是 q95 + 4:4:4（见文件顶部），保证颜色可复核。
                # 注意：运行时取色读的是内存里的 self.frame，与这里存盘格式无关。
                cv2.imwrite(os.path.join(folder, "frame.jpg"), frame, JPEG_PARAMS)
            with open(os.path.join(folder, "ocr.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(
                    f"{t.name}\t({t.x},{t.y},{t.width},{t.height})" for t in texts))
            with open(os.path.join(folder, "actions.txt"), "w", encoding="utf-8") as f:
                f.write(f"step={self._steps} last={self._last_state} "
                        f"no_change={self._no_change} rounds={self._stuck_rounds}\n")
                f.write("\n".join(self._recent_actions))
            with open(os.path.join(folder, "threads.txt"), "w", encoding="utf-8") as f:
                for th in threading.enumerate():
                    f.write(f"{th.name}\t{th.is_alive()}\n")
            self.log_info(f"调试包已保存：logs/debug/{os.path.basename(folder)}")
        except Exception as e:                       # noqa: BLE001
            self.log_debug(f"调试包保存失败: {type(e).__name__}: {e}")

    def dump_unknown(self, texts, tag):
        """把不认识的界面存证下来：截图 + OCR 文本，事后复盘补判据用。

        产物在 logs/unknown/（logs 已被 .gitignore 忽略，不会污染仓库）。
        """
        try:
            folder = os.path.join(PROJECT_ROOT, "logs", "unknown")
            os.makedirs(folder, exist_ok=True)
            name = f"{time.strftime('%m%d-%H%M%S')}_{tag}_s{self._steps}"
            frame = self.frame
            if frame is not None:
                # 同 dump_debug：q95 + 4:4:4，颜色可复核。
                cv2.imwrite(os.path.join(folder, name + ".jpg"), frame, JPEG_PARAMS)
            with open(os.path.join(folder, name + ".txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(
                    f"{t.name}\t({t.x},{t.y},{t.width},{t.height})" for t in texts))
            self.log_info(f"存证 logs/unknown/{name}.jpg")
        except Exception as e:
            self.log_debug(f"存证失败: {type(e).__name__}: {e}")

    def _handle_any(self, texts):
        for handler in self.HANDLERS:
            try:
                if handler(self, texts):
                    self._last_state = handler.__name__
                    self._recent_actions.append(f"step{self._steps} {handler.__name__}")
                    # 记录「点击之后应该看到的那一帧」的指纹，下一拍对比是否变化
                    self._sig_after_click = self._signature(texts)
                    self.log_debug(f"命中 {handler.__name__}（第 {self._steps} 步）")
                    # 用名字判断而不是 `is TrainingTask.handle_x`：
                    # 拆分到 mixin 后类引用会循环 import，名字更稳。
                    name = handler.__name__
                    if name != "handle_prepare":
                        self._skip_tries = 0
                    if name != "handle_in_match":
                        self._match_flags = {"manual_fixed": False, "fast_clicked": False, "last_fast_at": 0.0}
                    if name != "handle_subject_select":
                        self._picked_subject = False
                        self._picked_index = 0
                    return True
            except Exception as e:
                self.log_error(f"{handler.__name__} 异常: {type(e).__name__}: {e}")
                return True  # 吞掉这一拍，避免同一异常反复刷屏
        return False

    @staticmethod
    def exact_text(texts, names):
        """整词匹配（`in` 的包含匹配会命中正文里的字，例如休息确认框正文里的「休息」）。"""
        for name in names:
            for t in texts:
                if t.name.strip() == name:
                    return t
        return None

    @staticmethod
    def is_label(name):
        """浮层/角标文字（「划船比赛！」这种），只是视觉提示，点了没反应。"""
        s = (name or "").strip()
        if not s:
            return False
        return s.endswith("！") or s.endswith("!") or "加成" in s or "比赛！" in s

    def exit_page(self, after_sleep=0.45):
        """点右上角返回箭头退出当前页面。

        坑：左上角 (70,55) 那个按钮在训练日会打开「训练计划」日历，不是返回。
        """
        self.click(int(self.width * 0.958), int(self.height * 0.060), after_sleep=after_sleep)

    @property
    def style(self):
        return self.config.get("流派", "训练流")

    def weekend_priority(self):
        """周末地图上实际要去的地点（顺序 = 优先级）。

        「商店」是周末首页的按钮、不是地图上的地点，这里要过滤掉；
        步行街 / 游戏厅仍是显式开关（没验证过内页，先别默认乱进）。
        """
        places = [p for p in STYLE_PLACES.get(self.style, STYLE_PLACES["训练流"])
                  if p != "商店"]
        if not self.config.get("允许进游戏厅", True):
            places = [p for p in places if p != "游戏厅"]
        if not self.config.get("允许进步行街", True):
            places = [p for p in places if p != "步行街"]
        # 公园永远兜底：周末总得花掉一天
        if "公园" not in places:
            places.append("公园")
        return places

    def low_stamina(self, stamina_state):
        current = stamina_state.get("stamina")
        limit = int(self.config.get("休息体力阈值", 30))
        return current is not None and current < limit
