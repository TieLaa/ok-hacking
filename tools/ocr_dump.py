"""抓一帧 → OCR → 把识别框画回图上，用于核对「OCR 坐标是否和画面一致」。

用法：
    python tools/ocr_dump.py [输出目录]

产物：
    <输出目录>/ocr_frame.png    原始帧
    <输出目录>/ocr_marked.png   画了识别框的帧（红色框 + 文字）
    控制台打印每条识别结果：文字 @(x,y,w,h)

为什么要这个工具：ok-script 的 OCR 框曾经和画面位置对不上（差 ~70/95px），
靠肉眼比对才能发现。画回去一眼就能判断是 OCR 偏了，还是点击坐标换算错了。
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("TEMP", "."), "ocr-dump")
    os.makedirs(out_dir, exist_ok=True)

    frame = grab_frame()
    frame_path = os.path.join(out_dir, "ocr_frame.png")
    cv2.imwrite(frame_path, frame)
    print(f"frame {frame.shape[1]}x{frame.shape[0]} -> {frame_path}")

    items = run_ocr(frame)
    print(f"识别到 {len(items)} 条：")
    marked = frame.copy()
    for text, x, y, w, h, score in sorted(items, key=lambda i: (i[2], i[1])):
        print(f"  「{text}」 @({x},{y},{w},{h}) score={score:.2f}")
        cv2.rectangle(marked, (x, y), (x + w, y + h), (0, 0, 255), 2)
        cv2.putText(marked, text[:12], (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

    marked_path = os.path.join(out_dir, "ocr_marked.png")
    cv2.imwrite(marked_path, marked)
    print(f"marked -> {marked_path}")


if __name__ == "__main__":
    main()
