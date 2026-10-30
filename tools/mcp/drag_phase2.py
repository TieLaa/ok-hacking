"""MCP 入口：用 capture.get_frame() 直接抓帧验证拖动（绕开 self.frame 缓存）。

之前 15 组实验都用 self.frame，独立脚本里它不刷新，所有 diff 都是 0.0，
判据不可信。这里每次都用 WGC 直接取帧。
"""

import os
import time

import cv2
import numpy as np
import win32api
import win32con
import win32gui
from ok import BaseTask

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOT_DIR = os.path.join(ROOT, "logs", "debug")
MOVE = (win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE
        | win32con.MOUSEEVENTF_VIRTUALDESK)


def move_abs(sx, sy):
    vx, vy, vw, vh = (win32api.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    nx = int(round((sx - vx) * 65535 / max(vw - 1, 1)))
    ny = int(round((sy - vy) * 65535 / max(vh - 1, 1)))
    win32api.mouse_event(MOVE, nx, ny, 0, 0)


def focus(hwnd):
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:                                # noqa: BLE001
        pass
    time.sleep(0.3)
    return win32gui.GetForegroundWindow() == hwnd


class Task(BaseTask):

    def reset_to_start(self):
        return True

    def verify_start_state(self):
        return True

    def run(self):
        hwnd = self.executor.device_manager.hwnd_window.hwnd
        cap = self.executor.device_manager.capture_method
        w, h = cap.width, cap.height
        os.makedirs(SHOT_DIR, exist_ok=True)
        focus(hwnd)

        def grab(tag, tries=12):
            for _ in range(tries):
                f = cap.get_frame()
                if f is not None and f.any():
                    p = os.path.join(SHOT_DIR, f"frame_{tag}.jpg")
                    cv2.imwrite(p, f)
                    return p, f
                time.sleep(0.1)
            return None, None

        pa, fa = grab("before")
        x = int(w * 0.82)
        sx1, sy1 = win32gui.ClientToScreen(hwnd, (x, int(h * 0.62)))
        sx2, sy2 = win32gui.ClientToScreen(hwnd, (x, int(h * 0.30)))
        move_abs(sx1, sy1)
        time.sleep(0.08)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.12)
        steps = 20
        for i in range(1, steps + 1):
            t = i / steps
            move_abs(int(sx1 + (sx2 - sx1) * t), int(sy1 + (sy2 - sy1) * t))
            time.sleep(0.025)
        time.sleep(0.15)
        pb, fb = grab("holding")
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.9)
        pc, fc = grab("after")

        def diff(a, b):
            if a is None or b is None:
                return None
            if a.shape != b.shape:
                return "shape"
            return round(float(np.abs(a.astype(int) - b.astype(int)).mean()), 3)

        ta = [t.name for t in (self.ocr(frame=fa) or [])]
        tb = [t.name for t in (self.ocr(frame=fb) or [])]
        tc = [t.name for t in (self.ocr(frame=fc) or [])]
        report = {
            "before→holding": diff(fa, fb),
            "before→after": diff(fa, fc),
            "texts_before": ta[:10],
            "texts_holding": tb[:10],
            "texts_after": tc[:10],
            "new_after": sorted(set(tc) - set(ta))[:8],
        }
        self.log_info("直接抓帧诊断：" + str(report))
        return report
