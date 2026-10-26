"""Standalone WGC frame grab for a window, reusing ok-script's capture implementation.

Usage: python wgc_capture.py <out.jpg|out.png> [title_keyword] [quality]
Works from a non-elevated process even when the target window is occluded.

输出格式按扩展名决定：.jpg/.jpeg 用 JPEG（默认 q95 + 4:4:4），其他走 cv2 默认。
截图给 agent 读时优先用 .jpg —— 1080p PNG 约 2.4 MB、q95 约 0.48 MB，
读图耗时与体积成正比（每 MB 约 3.5 秒）。用 q95+4:4:4 而非 q85：项目有按像素
取色的判断逻辑，色度抽样会把颜色糊到相邻像素（实测最大色差 4:2:0=94、4:4:4=20）。
"""
import ctypes
import os
import sys
import threading
import time

import cv2
import win32gui

user32 = ctypes.windll.user32
user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2

JPEG_QUALITY = 95


def jpeg_params(quality=JPEG_QUALITY):
    """JPEG 编码参数：给定质量 + 4:4:4 色度不降采样（旧版 OpenCV 自动降级）。"""
    params = [cv2.IMWRITE_JPEG_QUALITY, quality]
    if hasattr(cv2, "IMWRITE_JPEG_SAMPLING_FACTOR_444"):
        params += [cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
                   cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
    return params


def imwrite_auto(path, img, quality=JPEG_QUALITY):
    """按扩展名选编码器：.jpg/.jpeg 用 JPEG(q95+4:4:4)，其余交给 cv2 默认（png 等）。"""
    if os.path.splitext(path)[1].lower() in (".jpg", ".jpeg"):
        return cv2.imwrite(path, img, jpeg_params(quality))
    return cv2.imwrite(path, img)



def find_hwnd(keyword, hwnd_class="UnityWndClass"):
    """按标题找窗口。

    坑（2026-10-25）：浏览器开着 TapTap 攻略页时，标题是
    「赛篮娘开服流派详解 - 篮球少女：飞跃巅峰攻略 - TapTap ...」，
    子串匹配会**优先抓浏览器**（面积更大），截出来的全是网页。
    所以默认再按窗口类名过滤：游戏是 UnityWndClass。
    """
    out = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        if keyword not in win32gui.GetWindowText(hwnd):
            return
        if hwnd_class and win32gui.GetClassName(hwnd) != hwnd_class:
            return
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


def grab(out_path, keyword="篮球少女：飞跃巅峰", quality=JPEG_QUALITY):
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
    imwrite_auto(out_path, frame, quality)
    print(f"hwnd={hwnd} frame={frame.shape[1]}x{frame.shape[0]} saved={out_path}")
    cap.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法: wgc_capture.py <out.jpg|out.png> [title_keyword] [quality]")
    out = sys.argv[1]
    kw = sys.argv[2] if len(sys.argv) > 2 else "篮球少女：飞跃巅峰"
    q = int(sys.argv[3]) if len(sys.argv) > 3 else JPEG_QUALITY
    grab(out, kw, q)
