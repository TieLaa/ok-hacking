"""抓一帧游戏画面（WGC），给离线核对脚本用。

为什么独立：`ocr_dump.py` / `probe_gacha.py` / `probe_history.py` / `probe_topbar.py`
各自抄了一份「WindowsGraphicsCaptureMethod + 等帧 + close」
（2026-11-10 重复扫描抓到）。

需要**写文件**的场合用 `tools/wgc_capture.py`（它管 JPEG 质量参数）；
只要内存里的帧就走这里。
"""

import time

DEFAULT_KEYWORD = "篮球少女：飞跃巅峰"


def grab_hwnd(hwnd, tries=10, delay=0.1):
    """按已知 hwnd 抓一帧（等帧重试）。抓不到返回 None。"""
    from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

    from tools.wgc_capture import MockWindow

    cap = WindowsGraphicsCaptureMethod(MockWindow(hwnd))
    frame = None
    try:
        for _ in range(tries):
            frame = cap.get_frame()
            if frame is not None:
                break
            time.sleep(delay)
    finally:
        cap.close()
    return frame


def grab_frame(keyword=DEFAULT_KEYWORD):
    """按窗口标题找游戏并抓一帧；找不到窗口/抓不到帧直接 SystemExit（命令行工具语义）。"""
    from tools.wgc_capture import find_hwnd

    hwnd = find_hwnd(keyword)
    if not hwnd:
        raise SystemExit("找不到游戏窗口")
    frame = grab_hwnd(hwnd)
    if frame is None:
        raise SystemExit("抓帧失败")
    return frame
