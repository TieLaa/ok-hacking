"""真人键鼠活动探针（低级钩子版）。

为什么不用 `GetLastInputInfo`（2026-11-08 换掉）：

`GetLastInputInfo` 只给「最后一次输入的时间戳」，**分不清这次输入是谁发的**。
脚本自己的 `mouse_event` / `keybd_event` 也会刷新它，于是旧做法只能「动作结束后
把当前时间戳记成自己的」。问题是动作不是瞬时的：一次点击约 0.15 秒、`ensure_foreground`
抢前台最多 0.6 秒、拖拽 0.3 秒以上。用户在这段时间里动一下鼠标，这次输入就被
记成「脚本的」，下一次比对结果是「没人动过」→ 立刻抢前台。用户感受到的就是
「我正用着鼠标，光标被拽走了」。

低级钩子的回调能拿到 `MSLLHOOKSTRUCT.flags` / `KBDLLHOOKSTRUCT.flags`：

- `LLMHF_INJECTED`(0x01) / `LLMHF_LOWER_IL_INJECTED`(0x02)：鼠标事件是注入的
- `LLKHF_INJECTED`(0x10)：键盘事件是注入的

`SendInput` / `mouse_event` / `keybd_event` 发的事件都带这个标志，**真人的不带**。
只记「没有注入标志」的事件时间，上面那些窗口就一起消失了。

代价与边界：

- 常驻一个低级钩子。回调里只做一次 float 赋值 + `CallNextHookEx`，不做 IO、
  不打日志、不加锁 —— 超过 `LowLevelHooksTimeout`（默认 1 秒）会被系统静默摘掉，
  这里远低于那个量级。
- 低级钩子只能看到完整度不高于自己的窗口的输入。本游戏必须管理员运行，
  ok-script 也是管理员，所以游戏在前台时照样收得到。
- 钩子装不上（被安全软件拦、缺少消息队列等）时 `start()` 返回 False，
  `GirlsInteraction` 会退回旧的 tick 判据，只是退化成「动作之间能挡、动作之中
  挡不住」。
"""

import ctypes
import threading
import time
from ctypes import wintypes

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000

LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
LLMHF_LOWER_IL_INJECTED = 0x02

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class UserActivityMonitor:
    """记录「最后一次真人键鼠事件」的单调时钟（注入事件一律忽略）。

    一个进程装一套钩子就够，所以外面用模块级单例 `USER_ACTIVITY`。多个
    `GirlsInteraction` 实例会共享它 —— 它们本来就共享同一块屏幕和同一只手。
    """

    def __init__(self):
        # 刚开始按「刚刚有人操作」算：宁可多等一个 user_idle_wait，也不要一上来就抢。
        self._last_user = time.monotonic()
        self._lock = threading.Lock()
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._started = False      # 钩子是否装上
        self._alive = False        # 钩子线程是否还活着
        self.events_seen = 0       # 收到的键鼠事件总数（含注入，仅诊断用）
        self.real_events = 0       # 其中真人事件数（不带 INJECTED 标志）
        self.error = None

    # ---------- 对外 ----------

    def start(self, wait=1.0):
        """懒启动钩子线程；返回钩子是否可用（False = 调用方自己走兜底判据）。"""
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="UserInputHook", daemon=True)
                self._thread.start()
            elif self._ready.is_set():
                return self.available
        self._ready.wait(wait)
        return self.available

    @property
    def available(self):
        return self._started and self._alive

    def idle_seconds(self):
        """距最后一次**真人**键鼠输入的秒数。"""
        return time.monotonic() - self._last_user

    def touch(self):
        """把「刚刚有人操作」记成现在（启动钩子时用，宁可多等一轮）。"""
        self._last_user = time.monotonic()

    def stop(self):
        """卸钩子（正常退出用；进程结束时会自动失效，不调也没事）。"""
        if self._thread_id is not None:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:                       # noqa: BLE001
                pass

    # ---------- 钩子线程 ----------

    def _run(self):
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except Exception as e:                      # noqa: BLE001
            self.error = e
            self._ready.set()
            return

        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), ctypes.c_void_p,
            wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self._user32 = user32
        # 回调对象必须由 Python 侧持有强引用，否则被回收后系统会调进野指针
        self._kb_proc = HOOKPROC(self._on_keyboard)
        self._ms_proc = HOOKPROC(self._on_mouse)
        self._thread_id = kernel32.GetCurrentThreadId()
        self._kb_hook = None
        self._ms_hook = None

        msg = wintypes.MSG()
        # 低级钩子的回调靠本线程的消息队列派发：先建队列，再挂钩子
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        hmod = kernel32.GetModuleHandleW(None)
        try:
            self._kb_hook = user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._kb_proc, hmod, 0)
            self._ms_hook = user32.SetWindowsHookExW(
                WH_MOUSE_LL, self._ms_proc, hmod, 0)
        except Exception as e:                      # noqa: BLE001
            self.error = e
        if not (self._kb_hook and self._ms_hook):
            if self.error is None:
                self.error = ctypes.get_last_error()
            for hook in (self._kb_hook, self._ms_hook):
                if hook:
                    user32.UnhookWindowsHookEx(hook)
            self._kb_hook = self._ms_hook = None
            self._ready.set()
            return

        self._started = True
        self._alive = True
        self.touch()                                # 从「现在」开始算空闲
        self._ready.set()

        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._alive = False
            for hook in (self._kb_hook, self._ms_hook):
                if hook:
                    try:
                        user32.UnhookWindowsHookEx(hook)
                    except Exception:               # noqa: BLE001
                        pass

    def _on_keyboard(self, n_code, w_param, l_param):
        """键盘钩子回调：只记真人按键。"""
        try:
            if n_code == HC_ACTION:
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self.events_seen += 1
                if not (info.flags & LLKHF_INJECTED):
                    self.real_events += 1
                    self._last_user = time.monotonic()
            return self._user32.CallNextHookEx(None, n_code, w_param, l_param)
        except Exception:                           # noqa: BLE001
            return 0

    def _on_mouse(self, n_code, w_param, l_param):
        """鼠标钩子回调：只记真人移动/点击/滚轮。"""
        try:
            if n_code == HC_ACTION:
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                self.events_seen += 1
                if not (info.flags & (LLMHF_INJECTED | LLMHF_LOWER_IL_INJECTED)):
                    self.real_events += 1
                    self._last_user = time.monotonic()
            return self._user32.CallNextHookEx(None, n_code, w_param, l_param)
        except Exception:                           # noqa: BLE001
            return 0


# 全进程一套：输入钩子是系统级资源，不需要每个 interaction 实例各装一个
USER_ACTIVITY = UserActivityMonitor()
