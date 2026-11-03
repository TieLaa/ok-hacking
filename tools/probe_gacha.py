"""离线自测：抓一帧当前画面 → OCR → 按真实的扭蛋解析/打分逻辑打印结果。

用法：
    python tools/probe_gacha.py [帧路径]

给帧路径时不抓屏，直接读那张图（调试包里的 frame.jpg 也行）。
只读，不点击 —— 用来回答「为什么选了中间那张」。
"""

import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402
from src.tasks.training.parsers import TrainingParsersMixin  # noqa: E402
from src.tasks.training.places.street import StreetPlace  # noqa: E402
from src.tasks.training.scoring import TrainingScoringMixin  # noqa: E402


DEFAULT_CONFIG = {
    "扭蛋品质优先": ["彩", "金", "紫"],
    "扭蛋清空精力阈值": 30,
}


class Probe(TrainingParsersMixin, TrainingScoringMixin, StreetPlace):
    def __init__(self, frame, config=None):
        self.frame = frame
        self.width = frame.shape[1]
        self.height = frame.shape[0]
        self.config = dict(DEFAULT_CONFIG, **(config or {}))

    def log_info(self, msg, *a, **kw):
        print("[info]", msg)


def grab_frame(path=None):
    if path:
        frame = cv2.imread(path)
        if frame is None:
            raise SystemExit("读不到帧：" + path)
        return frame
    from tools.wgc_capture import find_hwnd, MockWindow

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
    page = result[0] if isinstance(result, list) and len(result) == 1 else result
    boxes = []
    for entry in page or []:
        try:
            box, (text, score) = entry[0], entry[1]
        except Exception:
            continue
        if not text or score is None or score < 0.5:
            continue
        xs = [int(p[0]) for p in box]
        ys = [int(p[1]) for p in box]
        boxes.append(Box(x=min(xs), y=min(ys),
                         width=max(xs) - min(xs), height=max(ys) - min(ys),
                         name=text))
    return boxes


def main():
    frame = grab_frame(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"frame {frame.shape[1]}x{frame.shape[0]}")
    boxes = run_ocr(frame)
    probe = Probe(frame)
    cards = probe.read_gacha_cards(boxes)
    print(f"读到 {len(cards)} 张卡：")
    for c in cards:
        print(f"  {c['name']!r} 品质={c['quality']} H/S={c['hue']:.0f}/{c['sat']:.0f}"
              f" 队友={c['mate']} 增强={c['enhanced']} 价={c['price']}"
              f" 恢复={c['recover']} 清空={c['clear']}"
              f" 点=({c['x']},{c['y']}) 效果={c['effect']!r}")
    stamina = int(os.environ.get("STAMINA", "54"))
    stamina_max = int(os.environ.get("STAMINA_MAX", "130"))
    money = int(os.environ.get("MONEY", "398"))
    best, detail, _ = probe.gacha_candidates(cards, stamina, stamina_max, money)
    print(f"候选明细（精力 {stamina}/{stamina_max}，零用钱 {money}）：{detail}")
    print("选中：", None if best is None else
          f"{best['name']}（{best['quality']}，{best['price']}币，score={best['score']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
