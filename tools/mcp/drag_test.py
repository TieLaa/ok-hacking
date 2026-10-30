"""MCP 入口：选队长页拖动实验第 3 轮。

前两轮已排除：坐标偏移、前台窗口。
这一轮换「移动方式」：
- N/O：绝对坐标，但拆成 60/100 小步（每步 4~5px），模拟连续滑动
- P/Q：**相对位移**（mouse_event 不带 ABSOLUTE，每次 dx/dy 几像素），
       最接近物理鼠标，会被 raw input 当成真正的移动
"""

import os
import time

import cv2
import win32api
import win32con
import win32gui
from ok import BaseTask

from src.tasks.TrainingTask import TrainingTask

from _harness import borrow

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOT_DIR = os.path.join(ROOT, "logs", "debug")

MOVE_ABS = (win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE
            | win32con.MOUSEEVENTF_VIRTUALDESK)


def virtual_desktop():
    m = win32api.GetSystemMetrics
    return m(76), m(77), m(78), m(79)


def move_abs(sx, sy):
    vx, vy, vw, vh = virtual_desktop()
    nx = int(round((sx - vx) * 65535 / max(vw - 1, 1)))
    ny = int(round((sy - vy) * 65535 / max(vh - 1, 1)))
    win32api.mouse_event(MOVE_ABS, nx, ny, 0, 0)


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


def drag_abs_fine(hwnd, x1, y1, x2, y2, steps, hold, pre, post):
    sx1, sy1 = win32gui.ClientToScreen(hwnd, (int(x1), int(y1)))
    sx2, sy2 = win32gui.ClientToScreen(hwnd, (int(x2), int(y2)))
    move_abs(sx1, sy1)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    try:
        time.sleep(pre)
        for i in range(1, steps + 1):
            t = i / steps
            move_abs(int(sx1 + (sx2 - sx1) * t), int(sy1 + (sy2 - sy1) * t))
            time.sleep(hold)
        time.sleep(post)
    finally:
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def drag_relative(hwnd, x1, y1, dy_total, step_px, hold, pre, post):
    """相对位移拖动：每次发 (0, -step_px) 相对移动，累计 dy_total 像素。"""
    sx, sy = win32gui.ClientToScreen(hwnd, (int(x1), int(y1)))
    move_abs(sx, sy)
    time.sleep(0.06)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    try:
        time.sleep(pre)
        moved = 0
        sign = 1 if dy_total < 0 else -1
        while moved < abs(dy_total):
            win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, sign * step_px, 0, 0)
            moved += step_px
            time.sleep(hold)
        time.sleep(post)
    finally:
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def snap(task):
    return [(t.name.strip(), int(t.y)) for t in task._ocr_region() if t.name.strip()]


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
        results = []
        with borrow(self, TrainingTask, self.artifact_directory) as task:
            def run_case(name, fn):
                focus(hwnd)
                before = snap(task)
                ymap = {n: y for n, y in before}
                fn()
                time.sleep(1.0)
                after = snap(task)
                amap = {n: y for n, y in after}
                row = {
                    "case": name,
                    "new": sorted(n for n in amap if n not in ymap)[:6],
                    "shift": max([abs(amap[n] - ymap[n]) for n in amap if n in ymap],
                                 default=0),
                }
                results.append(row)
                self.log_info(f"{name}: {row}")
                frame = self.frame
                if frame is not None:
                    cv2.imwrite(os.path.join(SHOT_DIR, f"drag_{name}.jpg"), frame)
                time.sleep(0.4)

            run_case("N_绝对60步", lambda: drag_abs_fine(
                hwnd, w * 0.82, h * 0.62, w * 0.82, h * 0.30, 60, 0.010, 0.10, 0.08))
            run_case("O_绝对100步", lambda: drag_abs_fine(
                hwnd, w * 0.82, h * 0.68, w * 0.82, h * 0.24, 100, 0.008, 0.10, 0.08))
            run_case("P_相对3px", lambda: drag_relative(
                hwnd, w * 0.82, h * 0.62, -int(h * 0.30), 3, 0.012, 0.10, 0.08))
            run_case("Q_相对6px", lambda: drag_relative(
                hwnd, w * 0.82, h * 0.62, -int(h * 0.32), 6, 0.016, 0.12, 0.10))
        return {"cases": results}
