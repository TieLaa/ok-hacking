"""全局紧急停止热键。

为什么要自己再做一个：GirlsInteraction 每次点击都会把**物理光标**移到目标位置，
脚本跑起来以后人手基本抢不到鼠标，必须有一个不依赖界面焦点的中断键。

ok-script 自带 `Start/Stop`（默认 F9），但它有两个坑：

1. `RegisterHotKey` 是全局独占的。同时开着 ok-ef 这类默认同样用 F9 的程序时，
   只有一个能注册成功，另一个静默失败（日志里是 `Failed to register hotkey`）；
2. 它靠 GUI 线程的 `PeekMessageW` 轮询，GUI 忙/卡住时按了没反应。

这里自己开一条线程：`RegisterHotKey` + `GetMessageW` 阻塞等待，
命中后只置一个 `threading.Event`，任务主循环自己检查并退出。
热键线程里**不碰**任何 Qt 对象，避免跨线程调用。

按键是**按候选顺序抢**的（F12 经常被覆盖层/浏览器 DevTools 占掉，
实测本机 F12 = 1409 已被占用），抢到哪个日志里会写清楚。
"""

import ctypes
import ctypes.wintypes as wintypes
import threading

from ok import Logger

logger = Logger.get_logger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
WM_HOTKEY = 0x0312
_HOTKEY_ID = 0xB601          # 0x0000~0xBFFF 之间，避开 ok-script 自带的 999

# 单键优先（抢控制权时手忙，最好一下就到），其次才用组合键
DEFAULT_KEYS = (
    (0, 0x7B, "F12"),
    (0, 0x77, "F8"),
    (MOD_CONTROL | MOD_ALT, 0x7B, "Ctrl+Alt+F12"),
    (MOD_CONTROL | MOD_SHIFT, 0x7B, "Ctrl+Shift+F12"),
)


class EmergencyStop:
    """一个可复用的“紧急停止”信号。"""

    def __init__(self, keys=DEFAULT_KEYS):
        self.keys = tuple(keys)
        self.event = threading.Event()
        self.label = None           # 真正抢到的按键名
        self._thread = None
        self._lock = threading.Lock()
        self.registered = None      # None=还没试 / True / False

    # ---------- 对外 ----------

    def ensure_started(self):
        """首次调用时启动热键线程；注册失败只记警告，不影响任务本身。"""
        with self._lock:
            if self._thread is not None:
                return self.registered
            self._thread = threading.Thread(
                target=self._listen, name="EmergencyStopHotkey", daemon=True)
            self._thread.start()
            return True

    def consume(self):
        """任务循环里调用：这次按过键就返回 True（并清掉标记）。"""
        if self.event.is_set():
            self.event.clear()
            return True
        return False

    def request(self):
        """模拟一次按键（测试 / 外部兜底用）。"""
        self.event.set()

    # ---------- 热键线程 ----------

    def _listen(self):
        user32 = ctypes.windll.user32
        for mods, vk, label in self.keys:
            try:
                if user32.RegisterHotKey(None, _HOTKEY_ID, mods, vk):
                    self.label = label
                    self.registered = True
                    logger.info(f"紧急停止热键已就绪：{label}")
                    break
            except Exception as e:                  # noqa: BLE001
                logger.debug(f"热键 {label} 注册异常: {type(e).__name__}: {e}")
        if not self.registered:
            logger.warning("紧急停止热键全部被占用，只能用 ok-script 自带的 Start/Stop 热键")

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    logger.warning(f"按下 {self.label} → 请求停止")
                    self.event.set()
        finally:
            try:
                user32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:                       # noqa: BLE001
                pass


# 全局单例：任务和工具脚本共用，谁先启动谁注册
STOP = EmergencyStop()
