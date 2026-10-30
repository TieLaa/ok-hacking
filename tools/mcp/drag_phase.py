"""MCP 入口：拖动过程抓帧 —— 区分「完全没反应」和「跟随后回弹」。

序列：
  before            → 拖到终点（还不松手）→ mid
  松手               → after_release
  1 秒后             → after_1s
比较这些帧，就能看出列表有没有跟随手指、松手后回弹多少。
"""

import os
import time

import cv2
import numpy as np
import win32api
import win32con
import win32gui
from ok import BaseTask

from src.tasks.TrainingTask import TrainingTask

from _harness import borrow

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOT_DIR = os.path.join(ROOT, "logs", "debug")
MOVE = (win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE
        | win32con.MOUSEEVENTF_VIRTUALDESK)


def imread_u(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


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

        def grab(tag):
            frame = self.frame
            if frame is None:
                return None
            p = os.path.join(SHOT_DIR, f"dragphase_{tag}.jpg")
            cv2.imwrite(p, frame)
            return p

        x = int(w * 0.82)
        sx1, sy1 = win32gui.ClientToScreen(hwnd, (x, int(h * 0.62)))
        sx2, sy2 = win32gui.ClientToScreen(hwnd, (x, int(h * 0.30)))

        before = grab("before")
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
        mid = grab("mid")                     # 还按着，列表应该被拖上去
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        after_release = grab("after_release")
        time.sleep(1.0)
        after_1s = grab("after_1s")

        def diff(p1, p2):
            if not p1 or not p2:
                return None
            a, b = imread_u(p1), imread_u(p2)
            if a is None or b is None or a.shape != b.shape:
                return None
            return round(float(np.abs(a.astype(int) - b.astype(int)).mean()), 3)

        report = {
            "before→mid(按住拖动后)": diff(before, mid),
            "before→after_release": diff(before, after_release),
            "before→after_1s": diff(before, after_1s),
            "mid→after_1s(松手后回弹量)": diff(mid, after_1s),
        }
        self.log_info("拖动过程诊断：" + str(report))
        return report
