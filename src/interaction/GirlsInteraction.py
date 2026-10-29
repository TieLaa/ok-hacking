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
- **用户键鼠输入就让路**（见下面的 STEAL_* 常量）：每个动作前看系统"最后一次
  输入事件"时间戳有没有被脚本之外的东西更新过，有就暂停（默认 5 秒，用户还在
  操作就继续等），等手停下来再接着点。
"""

import ctypes
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

# ---------- 用户抢鼠标 / 键盘 ----------
# 2026-10-29 需求：**任意键鼠输入都算用户在操作**，检测到就暂停操作。
# 判据用 GetLastInputInfo：系统"最后一次输入事件"的时间戳（键盘、鼠标移动、
# 滚轮、点击都算）。脚本自己的 mouse_event / keybd_event 也会更新它，所以每次
# 脚本动作结束后把这个 tick 记成基准；下次动作前 tick 变了 = 中间有别人的输入。
# 比低级钩子（WH_KEYBOARD_LL / WH_MOUSE_LL）安全：钩子回调跑在 Python 里会拖慢
# 整个系统的输入响应，这里只是读一个时间戳。
STEAL_PAUSE = 5.0            # 秒：每次检测到用户输入，至少暂停这么久
STEAL_SETTLE = 1.0           # 秒：暂停到点后还得静这么久没输入才恢复
STEAL_TICK = 0.1             # 秒：暂停期间的采样间隔
STEAL_MAX_WAIT = 120.0       # 秒：兜底，别让"用户一直在操作"把任务永久挂住
STEAL_MARK_SETTLE = 0.02     # 秒：脚本动作结束后等系统把这次注入记进输入队列

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


class _LastInputInfo(ctypes.Structure):
    """GetLastInputInfo 的入参结构（cbSize + 最后一次输入的 GetTickCount 毫秒）。"""
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


class GirlsInteraction(BaseInteraction):
    """真实鼠标 + 真实键盘，必要时要先把游戏窗口置前。"""

    def __init__(self, capture, hwnd_window, foreground=True):
        super().__init__(capture)
        self.hwnd_window = hwnd_window
        self.foreground = foreground
        self._saved_cursor = None
        self._down_pos = None
        self._pressed_keys = {}
        self.steal_pause = STEAL_PAUSE   # 任务层可通过配置项覆盖；0 = 关闭检测
        self._script_input_tick = None   # 脚本最后一次动作对应的输入事件 tick
        self.steal_count = 0             # 累计让路次数（复盘用）

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

            # 1) Alt 技巧（只在游戏不在前台时才会走到这里：
            #    上面已经「前台就是游戏 → 直接 return True」）
            logger.debug(f"抢前台 attempt {attempt}：注入 Alt + SetForegroundWindow")
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

        # 不要用「最小化再恢复」抢前台：前台是管理员进程时恢复会失败，
        # 窗口会反复 visible=False，WGC 抓不到帧、点击也会全部落空
        # （实训 2026-10-26）。抢不到就明确放弃，让上层报错/等用户点一下游戏。
        fg_name = win32gui.GetWindowText(win32gui.GetForegroundWindow())
        logger.warning(f"游戏未能置前（目标 {hwnd}，当前前台「{fg_name}」），本次操作放弃")
        return False

    def is_foreground(self):
        return win32gui.GetForegroundWindow() == self.hwnd

    # ---------- 用户抢鼠标 / 键盘 ----------

    @staticmethod
    def _last_input_tick():
        """系统最后一次输入事件的 GetTickCount 毫秒；读不到返回 None。"""
        try:
            info = _LastInputInfo()
            info.cbSize = ctypes.sizeof(_LastInputInfo)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return int(info.dwTime)
        except Exception as e:                      # noqa: BLE001
            logger.debug(f"GetLastInputInfo 失败: {e}")
        return None

    def mark_script_input(self, settle=STEAL_MARK_SETTLE):
        """动作结束：把当前的「最后一次输入」认定成脚本自己的。

        脚本注入的 mouse_event / keybd_event 也会更新 GetLastInputInfo，不记账的话
        下一次检测会把脚本自己的点击当成用户输入，每拍都白停 5 秒。settle 是等
        系统把这次注入记进输入队列的时间（注入是异步入队的）。
        """
        if self.steal_pause <= 0:
            return
        if settle:
            time.sleep(settle)
        tick = self._last_input_tick()
        if tick is not None:
            self._script_input_tick = tick

    def wait_if_user_active(self):
        """动作前调用：检测到用户有键鼠输入就暂停，返回这次是否让路停过。

        tick 变了 = 脚本动作结束后有新的输入事件（键盘/鼠标移动/滚轮/点击）。
        暂停到点后如果还在输入就继续等，否则用户刚恢复操作立刻又被打断；
        超过 STEAL_MAX_WAIT 强制恢复，避免别的软件持续注入输入时任务永久卡死。
        """
        if self.steal_pause <= 0:
            return False
        tick = self._last_input_tick()
        if tick is None:
            return False
        if self._script_input_tick is None:
            self._script_input_tick = tick
            return False
        if tick == self._script_input_tick:
            return False

        start = time.monotonic()
        deadline = start + self.steal_pause
        last_input = start
        self.steal_count += 1
        logger.info(f"检测到键鼠输入 → 暂停操作至少 {self.steal_pause:.1f} 秒")
        while True:
            time.sleep(STEAL_TICK)
            now_tick = self._last_input_tick()
            now = time.monotonic()
            if now_tick is not None and now_tick != tick:
                tick, last_input = now_tick, now
            if now >= deadline and now - last_input >= STEAL_SETTLE:
                break
            if now - start >= STEAL_MAX_WAIT:
                logger.warning(f"用户持续操作超过 {STEAL_MAX_WAIT:.0f} 秒 → 强制恢复操作")
                break
        self._script_input_tick = tick
        logger.info(f"输入已停止，暂停 {time.monotonic() - start:.1f} 秒 → 继续操作")
        return True

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

        # 用户有键鼠输入就先让路（需求 2026-10-29），别跟人手抢鼠标
        self.wait_if_user_active()

        # 抢不到前台就**绝不点击**：真实鼠标事件一定会落到别的窗口上，
        # 轻则空点、重则把别的程序按坏（实测点到了 ok-script GUI 上）。
        if not self.ensure_foreground():
            raise RuntimeError("游戏窗口未能置前，放弃本次点击")
        saved = win32api.GetCursorPos()
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
            # 点完把物理光标还给用户：SetCursorPos **不产生 raw 输入**，
            # 游戏内部的光标仍停在点击点，下一次点击前会重新绝对定位，
            # 所以不会像「用 SetCursorPos 代替移动」那样把坐标搞错。
            # 2026-10-26 用户反馈：脚本跑起来抢不到鼠标。
            try:
                win32gui.SetCursorPos(int(saved[0]), int(saved[1]))
            except Exception:                       # noqa: BLE001
                pass
            # 这次点击的 mouse_event 会更新系统「最后一次输入」时间戳；记成脚本
            # 自己的，下次检测才不会把自己的点击误判成用户操作。
            self.mark_script_input()

    def mouse_down(self, x=-1, y=-1, name=None, key="left"):
        if x < 0 or y < 0:
            x = round(self.capture.width * 0.5)
            y = round(self.capture.height * 0.5)
        self.wait_if_user_active()
        self.ensure_foreground()
        self._move_cursor(x, y)
        self._down_key = key
        win32api.mouse_event(self._EVENTS.get(key, self._EVENTS["left"])[0], 0, 0, 0, 0)

    def mouse_up(self, name=None, key="left"):
        key = getattr(self, "_down_key", key)
        win32api.mouse_event(self._EVENTS.get(key, self._EVENTS["left"])[1], 0, 0, 0, 0)
        self.mark_script_input()

    def scroll(self, x, y, scroll_amount):
        self.wait_if_user_active()
        self.ensure_foreground()
        self._move_cursor(x, y)
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(scroll_amount) * 120, 0)
        self.mark_script_input()

    def move(self, x, y, down_btn=0):
        self.wait_if_user_active()
        self.ensure_foreground()
        self._move_cursor(int(x), int(y))
        self.mark_script_input()

    def swipe(self, x1, y1, x2, y2, duration=0.3, settle_time=0):
        """按住拖动（真实鼠标），用于列表/视角。"""
        self.wait_if_user_active()
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
            self.mark_script_input()

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
        self.mark_script_input()

    def send_key_up(self, key):
        name = str(key).lower()
        if self._pressed_keys.get(name, 0) <= 0:
            return
        self._pressed_keys[name] -= 1
        vk = self._vk(key)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        self.mark_script_input()

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
        self.mark_script_input()
