"""Standalone frame grab for a window, reusing ok-script's capture implementation.

Usage: python wgc_capture.py <out.jpg|out.png> [title_keyword] [quality]
Works from a non-elevated process even when the target window is occluded.

抓帧后端按 config 同样的顺序选：WGC 优先，Win10（build < 20348）上 ok-script
直接判 WGC 不可用，自动回退 BitBlt_RenderFull。文件名保留 wgc_capture 是因为
tools 里多处引用，别重命名（要抓帧就 `from tools.wgc_capture import make_capture`）。

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
        # 几何必须同时给出 DWM 外框和客户区：BitBlt 系按两者的差算裁剪点
        # （get_crop_point 顶部多裁一条标题栏）。只填客户区会多抓 31px
        # 标题栏、让 OCR 坐标整体偏移（2026-11-10 实测 absdiff 26.97）。
        from ok.util.window import get_window_bounds

        x, y, window_width, window_height, width, height, scaling = get_window_bounds(hwnd)
        self.x = x
        self.y = y
        self.window_width = window_width
        self.window_height = window_height
        self.width = width
        self.height = height
        self.client_width = width
        self.client_height = height
        self.real_width = width
        self.real_height = height
        self.real_x_offset = 0
        self.real_y_offset = 0
        self.scaling = scaling or 1.0
        self.frame_aspect_ratio = width / height if height else 0

    @property
    def capture_target_signature(self):
        """WGC 捕获库用 getattr 读它判断窗口是否换了（不是死代码！）。

        2026-11-10 曾被死代码门禁误删，导致 `tools/wgc_capture.py` 直接
        AttributeError——门禁只静态扫引用，读不到这种动态属性。已列入豁免清单。
        """
        return (self.hwnd, self.top_hwnd, self.width, self.height, self.client_width, self.client_height)

    def get_top_window_cords(self, x, y):
        return x, y

    def get_abs_cords(self, x, y):
        return x, y


CAPTURE_PREFERENCE = ("WGC", "BitBlt_RenderFull")


def _usable(cap, tries=6, delay=0.15):
    for _ in range(tries):
        try:
            if cap.get_frame() is not None:
                return True
        except Exception:  # noqa: BLE001 - 选型阶段任何失败都只是"这个后端不行"
            pass
        time.sleep(delay)
    return False


def make_capture(hwnd, preference=CAPTURE_PREFERENCE):
    """按 config 同样的优先级建一个真能出帧的 capture 方法。

    WGC 在 Win10（build < 20348）上被 ok-script 的 windows_graphics_available()
    直接判不可用；回退 BitBlt_RenderFull 走 PrintWindow + PW_RENDERFULLCONTENT，
    实测与 WGC 逐像素一致、完全遮挡也能抓。tools 里的抓帧脚本统一走这里，
    不要再各抄一份 WindowsGraphicsCaptureMethod。
    """
    from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod
    from ok.util.window import windows_graphics_available

    window = MockWindow(hwnd)
    for name in preference:
        if name == "WGC":
            if not windows_graphics_available():
                continue
            cap = WindowsGraphicsCaptureMethod(window)
        elif name in ("BitBlt_RenderFull", "BitBlt"):
            import ok.device.capture_methods.bitblt as bitblt
            from ok.device.capture_methods.bitblt import BitBltCaptureMethod

            bitblt.render_full = name != "BitBlt"
            cap = BitBltCaptureMethod(window)
            cap.exit_event = threading.Event()
        else:
            continue
        if _usable(cap):
            return cap
        cap.close()
    raise SystemExit(f"no usable capture method: {preference}, hwnd={hwnd}")


def grab(out_path, keyword="篮球少女：飞跃巅峰", quality=JPEG_QUALITY):
    hwnd = find_hwnd(keyword)
    if not hwnd:
        raise SystemExit(f"window not found: {keyword}")
    cap = make_capture(hwnd)
    frame = None
    try:
        for _ in range(10):
            frame = cap.get_frame()
            if frame is not None:
                break
            time.sleep(0.3)
        if frame is None:
            raise SystemExit("no frame captured")
        imwrite_auto(out_path, frame, quality)
        print(f"hwnd={hwnd} method={cap.get_name()} frame={frame.shape[1]}x{frame.shape[0]} saved={out_path}")
    finally:
        cap.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法: wgc_capture.py <out.jpg|out.png> [title_keyword] [quality]")
    out = sys.argv[1]
    kw = sys.argv[2] if len(sys.argv) > 2 else "篮球少女：飞跃巅峰"
    q = int(sys.argv[3]) if len(sys.argv) > 3 else JPEG_QUALITY
    grab(out, kw, q)
