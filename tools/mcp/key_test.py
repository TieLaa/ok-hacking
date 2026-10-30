"""MCP 入口：选队长页用键盘翻页实验（鼠标拖动已确认无效）。"""

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

VK = {"down": 0x28, "up": 0x26, "next": 0x22, "prior": 0x21,
      "end": 0x23, "home": 0x24, "right": 0x27, "left": 0x25}


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


def press(hwnd, name, times=1):
    code = VK[name]
    for _ in range(times):
        win32api.keybd_event(code, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.12)


def click(hwnd, x, y):
    sx, sy = win32gui.ClientToScreen(hwnd, (int(x), int(y)))
    vx, vy, vw, vh = (win32api.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    nx = int(round((sx - vx) * 65535 / max(vw - 1, 1)))
    ny = int(round((sy - vy) * 65535 / max(vh - 1, 1)))
    m = (win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE
         | win32con.MOUSEEVENTF_VIRTUALDESK)
    win32api.mouse_event(m, nx, ny, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.12)
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
            def case(name, fn):
                focus(hwnd)
                before = snap(task)
                ymap = {n: y for n, y in before}
                fn()
                time.sleep(1.0)
                after = snap(task)
                amap = {n: y for n, y in after}
                row = {"case": name,
                       "new": sorted(n for n in amap if n not in ymap)[:6],
                       "shift": max([abs(amap[n] - ymap[n]) for n in amap if n in ymap],
                                    default=0)}
                results.append(row)
                self.log_info(f"{name}: {row}")
                time.sleep(0.3)

            case("R_PageDown", lambda: press(hwnd, "next"))
            case("S_Down_x5", lambda: press(hwnd, "down", 5))
            case("T_End", lambda: press(hwnd, "end"))
            case("U_先点卡再Down", lambda: (click(hwnd, w * 0.82, h * 0.30),
                                            time.sleep(0.4),
                                            press(hwnd, "down", 3)))
            case("V_滚轮上(负)", lambda: (
                win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, -120 * 5, 0)))
        return {"cases": results}
