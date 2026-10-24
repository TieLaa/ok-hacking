"""Standalone WGC frame grab for a window, reusing ok-script's capture implementation.

Usage: python wgc_capture.py <out.png> [title_keyword]
Works from a non-elevated process even when the target window is occluded.
"""
import ctypes
import sys
import threading
import time

import cv2
import win32gui

user32 = ctypes.windll.user32
user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2


def find_hwnd(keyword):
    out = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and keyword in win32gui.GetWindowText(hwnd):
            out.append(hwnd)

    win32gui.EnumWindows(cb, None)
    if not out:
        return None
    return max(out, key=lambda h: (lambda l, t, r, b: (r - l) * (b - t))(*win32gui.GetWindowRect(h)))


class MockWindow:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.top_hwnd = 0
        self.exists = True
        self.visible = True
        self.hwnds = []
        self.app_exit_event = threading.Event()
        rect = ctypes.c_long * 4
        cr = rect()
        user32.GetClientRect(hwnd, cr)
        self.width = cr[2] - cr[0]
        self.height = cr[3] - cr[1]
        self.client_width = self.width
        self.client_height = self.height
        self.real_width = self.width
        self.real_height = self.height
        self.real_x_offset = 0
        self.real_y_offset = 0
        self.scaling = 1.0
        self.frame_aspect_ratio = self.width / self.height if self.height else 0
        self.x = self.y = 0
        self.window_width = self.width
        self.window_height = self.height

    @property
    def capture_target_signature(self):
        return (self.hwnd, self.top_hwnd, self.width, self.height, self.client_width, self.client_height)

    def get_top_window_cords(self, x, y):
        return x, y

    def get_abs_cords(self, x, y):
        return x, y


def grab(out_path, keyword="篮球少女：飞跃巅峰"):
    from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

    hwnd = find_hwnd(keyword)
    if not hwnd:
        raise SystemExit(f"window not found: {keyword}")
    mock = MockWindow(hwnd)
    cap = WindowsGraphicsCaptureMethod(mock)
    frame = None
    for _ in range(10):
        frame = cap.get_frame()
        if frame is not None:
            break
        time.sleep(0.3)
    if frame is None:
        cap.close()
        raise SystemExit("no frame captured")
    cv2.imwrite(out_path, frame)
    print(f"hwnd={hwnd} frame={frame.shape[1]}x{frame.shape[0]} saved={out_path}")
    cap.close()


if __name__ == "__main__":
    grab(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "篮球少女：飞跃巅峰")
