"""Unity 客户端输入后端（真实输入版）。

2026-10-25 实测结论（《篮球少女：飞跃巅峰》Windows 客户端）：

1. `PostMessage(WM_LBUTTONDOWN/UP)` **完全无效**：调用返回成功，游戏毫无反应。
   试过的组合：裸消息 / 加 WM_ACTIVATE / 同步移动光标 / 游戏前台，全部无效。
2. 真实 `mouse_event(LEFTDOWN/LEFTUP)` **有效**：能点开菜单。
3. 因此坐标不能靠猜像素——一律用 OCR/模板拿到的框换算出屏幕坐标再点。

实现要点：
- 点击前必须 `SetForegroundWindow`（游戏不在前台时真实点击会落到别的窗口）。
- 点击前把光标移到目标并留一点时间，点完还原光标，避免打扰用户。
- `mouse_down/mouse_up` 成对，异常也要抬起，否则会卡住鼠标键。
"""

import time

import win32api
import win32con
import win32gui

from ok.device.interaction_methods.base import BaseInteraction
from ok.device.interaction_methods.keys import vk_key_dict
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

CURSOR_SETTLE = 0.12   # 光标到位后等待引擎采样（实测 20ms 偶发丢点，120ms 稳定）
FOREGROUND_WAIT = 0.35
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_MOVE = 0x0001
MOVE_STEP = 12        # 每次相对移动的最大像素
MOVE_STEP_DELAY = 0.010


class GirlsInteraction(BaseInteraction):
    """真实鼠标 + 真实键盘，必要时要先把游戏窗口置前。"""

    def __init__(self, capture, hwnd_window, foreground=True):
        super().__init__(capture)
        self.hwnd_window = hwnd_window
        self.foreground = foreground
        self._saved_cursor = None
        self._down_pos = None
        self._pressed_keys = {}

    # ---------- 窗口 ----------

    @property
    def hwnd(self):
        return self.hwnd_window.top_hwnd or self.hwnd_window.hwnd

    def ensure_foreground(self):
        """把游戏窗口真正置前。

        Windows 默认禁止后台进程抢前台：`SetForegroundWindow` 会**静默失败**（返回成功但
        窗口没上来），于是鼠标/键盘事件全打到别的窗口。实测这是本项目点击无效的真正原因。
        标准绕过：先模拟按一下 Alt，让系统认为"用户有输入意图"，再置顶。
        """
        hwnd = self.hwnd
        if not hwnd:
            return False
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        if not self.foreground:
            return False

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)

        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        time.sleep(0.01)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.warning(f"SetForegroundWindow failed: {e}")
        finally:
            # Alt 必须抬起，否则后续所有按键都会变成 Alt 组合键
            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(FOREGROUND_WAIT)

        ok = win32gui.GetForegroundWindow() == hwnd
        if not ok:
            logger.warning(f"游戏未能置前（目标 {hwnd}，当前前台 {win32gui.GetForegroundWindow()}），点击可能落空")
        return ok

    def is_foreground(self):
        return win32gui.GetForegroundWindow() == self.hwnd

    # ---------- 光标 ----------

    def _to_screen(self, x, y):
        left, top = win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))
        return left, top

    @staticmethod
    def _glide_to(sx, sy):
        """用**相对位移**分小步移动光标。

        实测：这游戏按 raw input 的相对位移跟踪鼠标，直接 SetCursorPos 瞬移它感知不到，
        后续点击就会落在它「以为」光标所在的位置（表现为点了没反应或点错东西）。
        """
        cx, cy = win32api.GetCursorPos()
        dx, dy = int(sx) - cx, int(sy) - cy
        if dx == 0 and dy == 0:
            return
        steps = max(1, (abs(dx) + abs(dy)) // MOVE_STEP)
        prev_x = prev_y = 0
        for i in range(1, steps + 1):
            want_x, want_y = round(dx * i / steps), round(dy * i / steps)
            mx, my = want_x - prev_x, want_y - prev_y
            if mx or my:
                win32api.mouse_event(MOUSEEVENTF_MOVE, mx, my, 0, 0)
            prev_x, prev_y = want_x, want_y
            time.sleep(MOVE_STEP_DELAY)
        # 少量累计误差用一次绝对定位收尾（此时游戏已跟到附近）
        win32api.SetCursorPos((int(sx), int(sy)))

    def _move_cursor(self, x, y):
        self._saved_cursor = win32api.GetCursorPos()
        sx, sy = self._to_screen(x, y)
        self._glide_to(sx, sy)
        time.sleep(CURSOR_SETTLE)
        return sx, sy

    def _restore_cursor(self):
        if self._saved_cursor:
            try:
                self._glide_to(*self._saved_cursor)
            except Exception as e:
                logger.warning(f"restore cursor failed: {e}")
            finally:
                self._saved_cursor = None

    # ---------- 鼠标 ----------

    _EVENTS = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }

    def click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.03, move=True, key="left"):
        if x < 0 or y < 0:
            x = round(self.capture.width * 0.5)
            y = round(self.capture.height * 0.5)

        self.ensure_foreground()
        if move:
            self._move_cursor(x, y)
        # 按住时间不能太短：实测「启程」这类按钮 70ms 的点击会被丢弃，
        # 人手点击一般在 80~150ms，这里统一抬到 100ms 起步。
        down_time = max(float(down_time), 0.10)
        down, up = self._EVENTS.get(key, self._EVENTS["left"])
        try:
            win32api.mouse_event(down, 0, 0, 0, 0)
            time.sleep(down_time)
        finally:
            win32api.mouse_event(up, 0, 0, 0, 0)
        if move:
            self._restore_cursor()

    def mouse_down(self, x=-1, y=-1, name=None, key="left"):
        if x < 0 or y < 0:
            x = round(self.capture.width * 0.5)
            y = round(self.capture.height * 0.5)
        self.ensure_foreground()
        self._move_cursor(x, y)
        self._down_key = key
        win32api.mouse_event(self._EVENTS.get(key, self._EVENTS["left"])[0], 0, 0, 0, 0)

    def mouse_up(self, name=None, key="left"):
        key = getattr(self, "_down_key", key)
        win32api.mouse_event(self._EVENTS.get(key, self._EVENTS["left"])[1], 0, 0, 0, 0)
        self._restore_cursor()

    def scroll(self, x, y, scroll_amount):
        self.ensure_foreground()
        self._move_cursor(x, y)
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(scroll_amount) * 120, 0)
        self._restore_cursor()

    def move(self, x, y, down_btn=0):
        self.ensure_foreground()
        self._move_cursor(int(x), int(y))

    def swipe(self, x1, y1, x2, y2, duration=0.3, settle_time=0):
        """按住拖动（真实鼠标），用于列表/视角。"""
        self.ensure_foreground()
        self._saved_cursor = win32api.GetCursorPos()
        sx, sy = self._to_screen(x1, y1)
        win32api.SetCursorPos((sx, sy))
        time.sleep(CURSOR_SETTLE)
        win32api.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        steps = 12
        try:
            for i in range(1, steps + 1):
                nx = int(x1 + (x2 - x1) * i / steps)
                ny = int(y1 + (y2 - y1) * i / steps)
                px, py = self._to_screen(nx, ny)
                win32api.SetCursorPos((px, py))
                time.sleep(max(duration, 0.05) / steps)
        finally:
            win32api.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            self._restore_cursor()

    # ---------- 键盘 ----------

    def _vk(self, key):
        name = str(key).upper()
        if name in vk_key_dict:
            return vk_key_dict[name]
        return win32api.VkKeyScan(str(key)) & 0xFF

    def send_key_down(self, key):
        self.ensure_foreground()
        vk = self._vk(key)
        win32api.keybd_event(vk, 0, 0, 0)
        self._pressed_keys[str(key).lower()] = self._pressed_keys.get(str(key).lower(), 0) + 1

    def send_key_up(self, key):
        name = str(key).lower()
        if self._pressed_keys.get(name, 0) <= 0:
            return
        self._pressed_keys[name] -= 1
        vk = self._vk(key)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)

    def send_key(self, key, down_time=0.02):
        self.send_key_down(key)
        time.sleep(down_time)
        self.send_key_up(key)

    def input_text(self, text, **kwargs):
        for ch in str(text):
            vk = win32api.VkKeyScan(ch) & 0xFF
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.01)
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
