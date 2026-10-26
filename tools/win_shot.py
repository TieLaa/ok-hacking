"""按 PID 截一个窗口，保存 png 或 jpeg（不需要游戏，也不依赖 PowerShell 的 DPI 行为）。

用法：
    .venv\\Scripts\\python.exe tools\\win_shot.py <pid> <out.jpg> [quality]

输出格式按扩展名决定：.jpg/.jpeg 用 JPEG（默认 q95 + 4:4:4，第三个参数可覆盖质量），
其他走 cv2 默认。截图给 agent 读时优先用 .jpg —— 1080p PNG 约 2.4 MB、
q95 约 0.48 MB，读图耗时与体积成正比（每 MB 约 3.5 秒）。
用 q95+4:4:4 而非 q85：项目有按像素取色的判断逻辑，色度抽样会把颜色糊到相邻像素，
实测单像素最大色差 4:2:0 是 94、4:4:4 是 20。

实现：
- 先把窗口 SW_RESTORE 并置前（Qt 的 PrintWindow 会返回全黑，只能抓屏幕）；
- 用桌面 DC 的 BitBlt 抓窗口矩形（物理像素，本进程已 DPI-aware）。
"""

import ctypes
import os
import sys
import time

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process
import win32ui

ctypes.windll.shcore.SetProcessDpiAwareness(2)

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



def find_hwnd(pid):
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid == pid:
                found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    if not found:
        return 0
    return max(found, key=lambda h: (lambda r: (r[2] - r[0]) * (r[3] - r[1]))(win32gui.GetWindowRect(h)))


def grab(rect, out, quality=JPEG_QUALITY):
    left, top, right, bottom = rect
    w, h = right - left, bottom - top
    hwnd_dc = win32gui.GetDC(0)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    save_dc.BitBlt((0, 0), (w, h), mfc_dc, (left, top), win32con.SRCCOPY)
    arr = np.frombuffer(bmp.GetBitmapBits(True), dtype=np.uint8).reshape(h, w, 4)
    imwrite_auto(out, arr[:, :, :3], quality)
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(0, hwnd_dc)
    return w, h


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("用法: win_shot.py <pid> <out.jpg|out.png> [quality]")
    pid, out = int(sys.argv[1]), sys.argv[2]
    quality = int(sys.argv[3]) if len(sys.argv) > 3 else JPEG_QUALITY
    hwnd = find_hwnd(pid)
    if not hwnd:
        raise SystemExit("找不到窗口")
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(0.03)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.5)
    rect = win32gui.GetWindowRect(hwnd)
    print("hwnd", hwnd, "rect", rect, "size", grab(rect, out, quality), "->", out,
          "fg:", win32gui.GetForegroundWindow() == hwnd)
