"""验证紧急停止热键真的能被系统热键机制触发（不需要游戏）。

用法：
    .venv\\Scripts\\python.exe tools\\test_hotkey.py
抢到哪个键就用哪个键模拟按一次，2 秒内收到事件就算通过。
"""

import ctypes
import sys
import time

sys.path.insert(0, ".")

from src.util.emergency_stop import MOD_ALT, MOD_CONTROL, MOD_SHIFT, STOP  # noqa: E402

KEYEVENTF_KEYUP = 0x0002


def tap(vk, mods=0):
    """模拟按键；带修饰键时先按下修饰键。"""
    user32 = ctypes.windll.user32
    held = []
    for bit, code in ((MOD_CONTROL, 0x11), (MOD_ALT, 0x12), (MOD_SHIFT, 0x10)):
        if mods & bit:
            user32.keybd_event(code, 0, 0, 0)
            held.append(code)
    user32.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    for code in reversed(held):
        user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)


def main():
    STOP.ensure_started()
    time.sleep(0.5)
    print("registered:", STOP.registered, "key:", STOP.label)
    if not STOP.registered:
        print("FAIL: 候选热键全被占用")
        return 1

    mods, vk = next((m, v) for m, v, label in STOP.keys if label == STOP.label)
    tap(vk, mods)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if STOP.consume():
            print(f"PASS: 收到 {STOP.label}，紧急停止标记已置位")
            return 0
        time.sleep(0.05)
    print("FAIL: 模拟按键后 2 秒内没收到 WM_HOTKEY")
    return 1


if __name__ == "__main__":
    sys.exit(main())
