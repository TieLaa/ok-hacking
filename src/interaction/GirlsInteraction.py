"""Unity 客户端输入后端。

为什么要自定义而不是直接用框架的 PostMessage：

1. **命中判定读的是系统光标**。Unity 处理 WM_LBUTTONDOWN 时，很多版本不看
   lParam 里的客户区坐标，而是读 `GetCursorPos()`。只发消息不移动光标，
   点击就会落在用户鼠标实际所在的位置（游戏里表现为「点了没反应」或点错）。
   参考实现：ok-end-field `src/interaction/EfInteraction.py`、ok-nte
   `src/interaction/NTEInteraction.py`，两者都是「SetCursorPos → PostMessage → 还原光标」。
2. **窗口要先「激活」一次**。后台窗口不接鼠标消息；发一条 WM_ACTIVATE(WA_ACTIVE)
   即可让 Unity 认为自己是活动窗口，不需要真的抢前台。
3. **光标必须还原**。否则脚本跑完会把用户的鼠标丢在游戏里的某个坐标上。

所以这里的 click 是「真光标 + 假消息」的混合模式：不改前台焦点，但保证命中点正确。
"""

import ctypes
import ctypes.wintypes
import time

import win32api
import win32con
import win32gui

from ok.device.interaction_methods.post_message import PostMessageInteraction
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

CURSOR_SETTLE = 0.012  # 移动光标后等待系统/引擎采样（实测 5ms 偶发丢点，12ms 稳定）


class GirlsInteraction(PostMessageInteraction):
    """PostMessage + 真实光标定位，适用于 Unity 窗口类游戏。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_cursor = None
        self._activated = False

    # ---------- 激活 ----------

    def try_activate(self):
        """只在需要时激活一次，且不改动真正的窗口焦点顺序。"""
        hwnd = self.hwnd
        if not hwnd:
            return
        try:
            if win32gui.GetForegroundWindow() == hwnd:
                self._activated = False
                return
            if not self._activated:
                self._activated = True
                self.post(win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
                time.sleep(0.01)
            self._unclip_cursor(hwnd)
        except Exception as e:
            logger.warning(f"try_activate failed: {e}")

    @staticmethod
    def _unclip_cursor(hwnd):
        """Unity 3D 场景会把光标锁在窗口内（ClipCursor）。

        后台点击时如果光标仍被裁剪，会被强行拉回游戏中心，命中点就偏了。
        检测到裁剪就解除（参考 ok-end-field EfInteraction.try_unclip）。
        """
        try:
            if win32gui.GetForegroundWindow() == hwnd:
                return
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetClipCursor(ctypes.byref(rect))
            screen_w = win32api.GetSystemMetrics(0)
            screen_h = win32api.GetSystemMetrics(1)
            if (rect.right - rect.left) < screen_w or (rect.bottom - rect.top) < screen_h:
                ctypes.windll.user32.ClipCursor(None)
                logger.debug("cursor clip released")
        except Exception:
            pass

    # ---------- 光标 ----------

    def _move_cursor(self, x, y):
        """把系统光标移到客户区坐标 (x, y)，返回是否移动过。"""
        try:
            abs_x, abs_y = self.capture.get_abs_cords(int(x), int(y))
        except Exception as e:
            logger.warning(f"get_abs_cords failed: {e}")
            return False
        self._saved_cursor = win32api.GetCursorPos()
        win32api.SetCursorPos((int(abs_x), int(abs_y)))
        time.sleep(CURSOR_SETTLE)
        return True

    def _restore_cursor(self):
        if self._saved_cursor:
            try:
                win32api.SetCursorPos(self._saved_cursor)
            except Exception as e:
                logger.warning(f"restore cursor failed: {e}")
            finally:
                self._saved_cursor = None

    # ---------- 鼠标 ----------

    def click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.02, move=True, key="left"):
        if x < 0 or y < 0:
            x = round(self.capture.width * 0.5)
            y = round(self.capture.height * 0.5)

        self.try_activate()
        moved = self._move_cursor(x, y) if move else False
        lparam = win32api.MAKELONG(int(x), int(y))

        buttons = {
            "left": (win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, win32con.WM_LBUTTONUP),
            "middle": (win32con.WM_MBUTTONDOWN, win32con.MK_MBUTTON, win32con.WM_MBUTTONUP),
            "right": (win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, win32con.WM_RBUTTONUP),
        }
        down, flag, up = buttons.get(key, buttons["left"])

        try:
            # 先补一条移动消息，让引擎更新 hover 状态，再按下
            self.post(win32con.WM_MOUSEMOVE, 0, lparam)
            time.sleep(0.008)
            self.post(down, flag, lparam)
            time.sleep(down_time)
        finally:
            # 无论按下是否抛异常，都必须抬起，否则会卡住鼠标键
            self.post(up, 0, lparam)
        if moved:
            self._restore_cursor()
        return True

    def mouse_down(self, x=-1, y=-1, name=None, key="left"):
        if x < 0 or y < 0:
            x = round(self.capture.width * 0.5)
            y = round(self.capture.height * 0.5)
        self.try_activate()
        self._move_cursor(x, y)
        self._down_pos = win32api.MAKELONG(int(x), int(y))
        action, flag = (
            (win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON)
            if key == "left"
            else (win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON)
        )
        self.post(action, flag, self._down_pos)

    def mouse_up(self, name=None, key="left"):
        pos = getattr(self, "_down_pos", 0)
        _, _, action = (
            (0, 0, win32con.WM_LBUTTONUP) if key == "left" else (0, 0, win32con.WM_RBUTTONUP)
        )
        self.post(action, 0, pos)
        self._restore_cursor()
        self._down_pos = 0

    # ---------- 键盘 ----------

    def send_key(self, key, down_time=0.02, after_sleep=0):
        """键盘仍走 PostMessage（Unity 对 WM_KEYDOWN 的接受度较高，且不抢焦点）。

        若实测无效，改成 ok-end-field 的做法：先激活窗口再用 pynput 真键盘。
        """
        self.try_activate()
        super().send_key(key, down_time=down_time)
        if after_sleep:
            time.sleep(after_sleep)
