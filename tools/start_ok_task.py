"""一键启动 ok-script 的「自动训练」任务。

为什么要这个脚本：
- ok-script GUI 会自己乱飘（每次 ShowWindow/geometry 事件都可能换位置），
  所以每次点击都必须**当场读窗口矩形**，不能用记下来的屏幕坐标；
- 任务卡位置会变（跑完之后顶部会展开「已完成」面板，卡片整体下移），
  所以按候选 y 逐个试，用日志里的 `queued onetime_task` 确认是否真的点上了
  （**要连任务名一起核对**：4 张卡都会打这行日志，点歪到示例卡也"成功"，
  实训 2026-10-27 09:42 白跑一轮）；
- 点完不用最小化 GUI：输入层是**真实 mouse_event（物理鼠标）**，不是 PostMessage
  （2026-10-25 实测 PostMessage 对这个 Unity 客户端完全无效）。
  所以调用方要把游戏拉回前台——GirlsInteraction 每次点击前都会 ensure_foreground()。

用法：
    python tools/start_ok_task.py
"""

import io
import os
import sys
import time

import win32api
import win32con
import win32gui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools.abs_mouse import click_screen, ensure_foreground  # noqa: E402

LOG = os.path.join(ROOT, "logs", "ok-script.log")

# 相对于 ok-script 主窗口左上角的「开始」按钮候选位置（兜底用：
# 优先按 OCR 找「自动训练」那一行的「开始」按钮，因为用户可以拖窗口改尺寸，
# 2026-10-27 10:43 窗口被缩到 1170x749，固定坐标就点歪到第 4 张卡了）
CANDIDATE_Y = (89, 470, 350, 210)
BUTTON_X = 1089

# 只有排上这些任务名才算成功（否则是点到了别的任务卡）
TARGET_KEYWORDS = ("自动训练", "TrainingTask")


def locate_start_button(ok):
    """OCR 定位：「自动训练」标题同一行右侧的「开始」按钮，返回窗口内相对坐标。

    找不到就返回 None，交给固定候选坐标兜底。
    """
    try:
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr  # noqa: PLC0415

        from tools.wgc_capture import make_capture  # noqa: PLC0415

        cap = make_capture(ok)
        frame = None
        try:
            for _ in range(10):
                frame = cap.get_frame()
                if frame is not None:
                    break
        finally:
            cap.close()
        if frame is None:
            return None
        ocr = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, show_log=False)
        result = ocr.ocr(frame)
        page = result[0] if isinstance(result, list) and len(result) == 1 else result
        boxes = []
        for entry in page or []:
            try:
                poly, (text, score) = entry[0], entry[1]
            except Exception:                    # noqa: BLE001
                continue
            if not text or score is None or score < 0.5:
                continue
            xs = [int(p[0]) for p in poly]
            ys = [int(p[1]) for p in poly]
            boxes.append((text.strip(), min(xs), min(ys),
                          max(xs) - min(xs), max(ys) - min(ys)))

        title = None
        for text, x, y, w, h in boxes:
            if any(k in text for k in TARGET_KEYWORDS):
                title = (x, y, w, h)
                break
        if title is None:
            print("OCR 没找到「自动训练」标题，改用固定候选坐标")
            return None
        ty = title[1] + title[3] / 2
        best = None
        for text, x, y, w, h in boxes:
            if "开始" not in text:
                continue
            cy = y + h / 2
            if abs(cy - ty) > 40:
                continue
            if best is None or x > best[0]:
                best = (x + w / 2, cy)
        if best is None:
            print("OCR 找到标题但没找到同一行的「开始」按钮，改用固定候选坐标")
            return None
        print(f"OCR 定位开始按钮：窗口内 ({int(best[0])}, {int(best[1])})")
        return int(best[0]), int(best[1])
    except Exception as e:                        # noqa: BLE001
        print(f"OCR 定位失败（{type(e).__name__}: {e}），改用固定候选坐标")
        return None


def find_ok_window():
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and "ok-hacking" in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def real_click(x, y):
    # 绝对坐标一步到位（与 GirlsInteraction 相同），不再滑行 / SetCursorPos
    click_screen(x, y)


def log_tail(n=200):
    try:
        with io.open(LOG, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def queued_task_since(mark):
    """返回 mark 之后排队的任务名（没有则 None）。

    只判断「有没有排上队」不够：任务卡有 4 张，点歪到示例卡也会打日志
    （实训 2026-10-27 09:42：点成 Configuration Demo Task，白跑一轮）。
    """
    for line in log_tail(400):
        if "queued onetime_task" in line and line[:19] >= mark:
            return line.split("queued onetime_task", 1)[1].strip()
    return None


def is_target(name):
    return any(k in name for k in TARGET_KEYWORDS)


def main():
    ok = find_ok_window()
    if ok is None:
        print("找不到 ok-script 窗口")
        return 1

    # 先把 GUI 摆到一个不遮游戏的位置，并让它当前台（Qt 按钮需要激活）
    win32gui.ShowWindow(ok, win32con.SW_RESTORE)
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(0.02)
    try:
        win32gui.SetForegroundWindow(ok)
    except Exception:
        pass
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.6)

    mark = time.strftime("%Y-%m-%d %H:%M:%S")
    clicked = None
    wrong = None

    # 先按 OCR 找「自动训练」那一行的「开始」按钮（窗口尺寸被拖过也不会点歪）
    rect = win32gui.GetWindowRect(ok)
    located = locate_start_button(ok)
    candidates = []
    if located:
        candidates.append((rect[0] + located[0], rect[1] + located[1]))
    candidates += [(rect[0] + BUTTON_X, rect[1] + dy) for dy in CANDIDATE_Y]

    for x, y in candidates:
        print(f"点击结束位置 ({x}, {y})")
        real_click(x, y)
        # App 刚启动时头几秒的点击会被吞（2026-10-27 09:41 实训），所以每个位置
        # 都轮询 4 秒再换下一个候选
        name = None
        for _ in range(8):
            time.sleep(0.5)
            name = queued_task_since(mark)
            if name:
                break
        if name and is_target(name):
            clicked = (x, y)
            break
        if name:
            wrong = (y, name)
            break

    if clicked is None:
        if wrong:
            print(f"排上的是别的任务（y={wrong[0]} → {wrong[1]}），请先停掉它再重试")
            return 2
        print("点了所有候选位置都没排队成功")
        return 1
    print(f"已排队，点击位置 {clicked}")

    # 2026-10-27 起输入层是后台点击（PostMessage），不再需要最小化 GUI，
    # 也不需要把游戏拉回前台：GUI 留着让用户看见状态就行。
    return 0


if __name__ == "__main__":
    sys.exit(main())
