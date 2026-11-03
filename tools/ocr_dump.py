"""抓一帧 → OCR → 把识别框画回图上，用于核对「OCR 坐标是否和画面一致」。

用法：
    python tools/ocr_dump.py [输出目录]

产物：
    <输出目录>/ocr_frame.png    原始帧
    <输出目录>/ocr_marked.png   画了识别框的帧（红色框 + 文字）
    <输出目录>/ocr.txt          **识别结果（UTF-8）**：文字 + 框 + 置信度

为什么要这个工具：ok-script 的 OCR 框曾经和画面位置对不上（差 ~70/95px），
靠肉眼比对才能发现。画回去一眼就能判断是 OCR 偏了，还是点击坐标换算错了。

输出约定（2026-11-03）：**这不是打印给人看的工具，结果以 ocr.txt 为准**。
stdout 被重定向/接进 PowerShell cmdlet 时只打 ASCII 的件数与路径 ——
本机码页 936 会把 UTF-8 中文解坏（详见 `tools/_console.py` 的说明），
不需要谁去记「别用 cmdlet 过滤中文」这条规矩。
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools._console import utf8_stdout  # noqa: E402
from tools.wgc_capture import find_hwnd, MockWindow  # noqa: E402


def grab_frame():
    from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod

    hwnd = find_hwnd("篮球少女：飞跃巅峰")
    if not hwnd:
        raise SystemExit("找不到游戏窗口")
    cap = WindowsGraphicsCaptureMethod(MockWindow(hwnd))
    frame = None
    for _ in range(10):
        frame = cap.get_frame()
        if frame is not None:
            break
    cap.close()
    if frame is None:
        raise SystemExit("抓帧失败")
    return frame


def run_ocr(frame):
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr

    ocr = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, show_log=False)
    result = ocr.ocr(frame)
    items = []
    if not result:
        return items
    page = result[0] if isinstance(result, list) and len(result) == 1 else result
    for entry in page or []:
        try:
            box = entry[0]
            text, score = entry[1]
        except Exception:
            continue
        if not text or score is None or score < 0.5:
            continue
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        items.append((text, min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), float(score)))
    return items


def main():
    utf8_stdout()
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "ocr-dump")
    os.makedirs(out_dir, exist_ok=True)

    frame = grab_frame()
    frame_path = os.path.join(out_dir, "ocr_frame.png")
    cv2.imwrite(frame_path, frame)
    print(f"frame {frame.shape[1]}x{frame.shape[0]} -> {frame_path}")

    items = run_ocr(frame)
    rows = sorted(items, key=lambda i: (i[2], i[1]))
    # 结果先落盘（UTF-8）：stdout 可能被父进程按 cp936 解码，中文不可信。
    text_path = os.path.join(out_dir, "ocr.txt")
    with open(text_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(f"{t}\t({x},{y},{w},{h})\t{score:.2f}"
                          for t, x, y, w, h, score in rows))

    marked = frame.copy()
    for text, x, y, w, h, _score in rows:
        cv2.rectangle(marked, (x, y), (x + w, y + h), (0, 0, 255), 2)
        cv2.putText(marked, text[:12], (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

    marked_path = os.path.join(out_dir, "ocr_marked.png")
    cv2.imwrite(marked_path, marked)

    if sys.stdout.isatty():
        print(f"识别到 {len(rows)} 条：")
        for text, x, y, w, h, score in rows:
            print(f"  「{text}」 @({x},{y},{w},{h}) score={score:.2f}")
        print(f"marked -> {marked_path}")
        print(f"text   -> {text_path}")
    else:
        # 非交互（被重定向/进 cmdlet）：只打 ASCII，内容以文件为准。
        print(f"items={len(rows)} text={text_path} marked={marked_path}")


if __name__ == "__main__":
    main()
