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
import win32process

from ok.device.interaction_methods.base import BaseInteraction
from ok.device.interaction_methods.keys import vk_key_dict
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

CURSOR_SETTLE = 0.04     # 绝对定位后等引擎采样（一步到位，不用滑行）
FOREGROUND_WAIT = 0.15
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

# 虚拟桌面度量（多屏时坐标要按整个虚拟桌面归一化）
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


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

    def ensure_foreground(self, tries=4):
        """把游戏窗口真正置前。**抢不到就返回 False，调用方不能硬点。**

        Windows 默认禁止后台进程抢前台：`SetForegroundWindow` 会**静默失败**（返回成功但
        窗口没上来），于是鼠标/键盘事件全打到别的窗口。2026-10-25 实测：ok-script GUI
        自己弹到前台时，点「开始」变成点 GUI，游戏界面毫发无伤 —— 表现就是"点在没反应的地方"。

        三重保险，逐个加码：
        1. 模拟按一下 Alt —— 让系统认为"用户有输入意图"；
        2. `AttachThreadInput` 把当前线程挂到前台窗口的输入队列，绕过前台锁；
        3. `SetWindowPos(... HWND_TOPMOST)` 后再撤回，强行上浮。
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

        for attempt in range(tries):
            fg = win32gui.GetForegroundWindow()

            # 1) Alt 技巧
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            time.sleep(0.02)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception as e:
                logger.debug(f"SetForegroundWindow attempt {attempt}: {e}")
            finally:
                # Alt 必须抬起，否则后续所有按键都会变成 Alt 组合键
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)

            if win32gui.GetForegroundWindow() == hwnd:
                return True

            # 2) AttachThreadInput 兜底
            try:
                cur_tid = win32api.GetCurrentThreadId()
                fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
                if fg_tid and fg_tid != cur_tid:
                    win32process.AttachThreadInput(cur_tid, fg_tid, True)
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    finally:
                        win32process.AttachThreadInput(cur_tid, fg_tid, False)
            except Exception as e:
                logger.debug(f"AttachThreadInput attempt {attempt}: {e}")

            if win32gui.GetForegroundWindow() == hwnd:
                return True

            # 3) 置顶再撤回
            try:
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
            except Exception as e:
                logger.debug(f"SetWindowPos attempt {attempt}: {e}")

            time.sleep(FOREGROUND_WAIT)
            if win32gui.GetForegroundWindow() == hwnd:
                return True

        fg_name = win32gui.GetWindowText(win32gui.GetForegroundWindow())
        logger.warning(f"游戏未能置前（目标 {hwnd}，当前前台「{fg_name}」），本次操作放弃")
        return False

    def is_foreground(self):
        return win32gui.GetForegroundWindow() == self.hwnd

    # ---------- 光标 ----------

    def _to_screen(self, x, y):
        """客户区坐标 -> 屏幕像素。用框架自带换算（会算上捕获区域偏移）。"""
        try:
            return self.capture.get_abs_cords(int(x), int(y))
        except Exception:
            return win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))

    @staticmethod
    def _virtual_desktop():
        m = win32api.GetSystemMetrics
        return (m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN),
                m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN))

    def _move_to(self, x, y):
        """把光标一步移动到客户区 (x, y)。

        三种做法实测对比（2026-10-25，《篮球少女》）：
        1. `SetCursorPos`：游戏**完全感知不到**——它读 raw input，这个 API 不产生 raw 事件；
        2. 相对位移 `mouse_event(MOVE, dx, dy)` 分步滑行：有效，但要几十上百步，
           慢、且全程霸占用户鼠标；
        3. `mouse_event(MOVE|ABSOLUTE|VIRTUALDESK, ...)`：**既有 raw 事件又能一步到位**，
           坐标按虚拟桌面归一化到 0~65535，跟窗口大小/分辨率无关。

        所以统一走第 3 种。注意：绝不能再用 SetCursorPos 收尾，那会让系统光标和
        游戏内部位置错位，之后每一次点击都会偏 —— 这就是"点在没交互的地方"的根因。
        """
        sx, sy = self._to_screen(x, y)
        vx, vy, vw, vh = self._virtual_desktop()
        nx = int(round((sx - vx) * 65535 / max(vw - 1, 1)))
        ny = int(round((sy - vy) * 65535 / max(vh - 1, 1)))
        win32api.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                             nx, ny, 0, 0)
        time.sleep(CURSOR_SETTLE)
        return sx, sy

    def _move_cursor(self, x, y):
        return self._move_to(x, y)

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

        # 抢不到前台就**绝不点击**：真实鼠标事件一定会落到别的窗口上，
        # 轻则空点、重则把别的程序按坏（实测点到了 ok-script GUI 上）。
        if not self.ensure_foreground():
            raise RuntimeError("游戏窗口未能置前，放弃本次点击")
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

    def scroll(self, x, y, scroll_amount):
        self.ensure_foreground()
        self._move_cursor(x, y)
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(scroll_amount) * 120, 0)

    def move(self, x, y, down_btn=0):
        self.ensure_foreground()
        self._move_cursor(int(x), int(y))

    def swipe(self, x1, y1, x2, y2, duration=0.3, settle_time=0):
        """按住拖动（真实鼠标），用于列表/视角。"""
        self.ensure_foreground()
        self._move_to(x1, y1)
        win32api.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        steps = 12
        try:
            for i in range(1, steps + 1):
                nx = int(x1 + (x2 - x1) * i / steps)
                ny = int(y1 + (y2 - y1) * i / steps)
                self._move_to(nx, ny)
                time.sleep(max(duration, 0.05) / steps)
        finally:
            win32api.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

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
