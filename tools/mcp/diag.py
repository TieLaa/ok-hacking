"""MCP 入口：坐标与拖动诊断（管理员运行时才能正确读窗口）。"""

import os
import time

import cv2
import numpy as np
import win32gui
from ok import BaseTask

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "logs", "debug", "diag_shot.jpg")


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def run(self):
        hwnd = self.executor.device_manager.hwnd_window.hwnd
        cap = self.executor.device_manager.capture_method
        rect = win32gui.GetWindowRect(hwnd)
        report = {
            "hwnd": int(hwnd),
            "rect": list(rect),
            "cap_size": [int(cap.width), int(cap.height)],
        }
        samples = []
        for x, y in ((100, 100), (774, 540), (774, 270)):
            try:
                cap_pt = cap.get_abs_cords(x, y)
            except Exception as e:                       # noqa: BLE001
                cap_pt = f"ERR {type(e).__name__}: {e}"
            client_pt = win32gui.ClientToScreen(hwnd, (x, y))
            samples.append({"client": [x, y], "cap": list(cap_pt)
                            if not isinstance(cap_pt, str) else cap_pt,
                            "win32": list(client_pt)})
        report["points"] = samples

        # 拖一次，比较前后帧（选队长页会滚动，画面应该明显变化）
        before = self.frame.copy() if self.frame is not None else None
        w, h = cap.width, cap.height
        self.swipe(int(w * 0.5), int(h * 0.62), int(w * 0.5), int(h * 0.30),
                   duration=0.3, after_sleep=0.9)
        after = self.frame.copy() if self.frame is not None else None
        diff = None
        if before is not None and after is not None and before.shape == after.shape:
            diff = float(np.abs(before.astype(int) - after.astype(int)).mean())
        report["drag_diff"] = diff
        if after is not None and after.any():
            cv2.imwrite(OUT, after)
            report["shot"] = OUT
        self.log_info("诊断：" + str(report))
        return report
