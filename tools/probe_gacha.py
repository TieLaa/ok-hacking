"""离线自测：抓一帧当前画面 → OCR → 按真实的扭蛋解析/打分逻辑打印结果。

用法：
    python tools/probe_gacha.py [帧路径]

给帧路径时不抓屏，直接读那张图（调试包里的 frame.jpg 也行）。
只读，不点击 —— 用来回答「为什么选了中间那张」。
"""

import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402
from src.tasks.training.parsers import TrainingParsersMixin  # noqa: E402
from src.tasks.training.places.street import StreetPlace  # noqa: E402
from src.tasks.training.scoring import TrainingScoringMixin  # noqa: E402
from tools import _capture, _ocr  # noqa: E402


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
        # `gacha_candidates` 2026-11-14 起按流派判断专属卡；离线探针默认按扭蛋流。
        self.style = "扭蛋流"

    def log_info(self, msg, *a, **kw):
        print("[info]", msg)


def grab_frame(path=None):
    if path:
        # cv2.imread 不认中文路径（本项目的存证目录带中文局名），用 fromfile 解码。
        frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise SystemExit("读不到帧：" + path)
        return frame
    return _capture.grab_frame()


def run_ocr(frame):
    """一帧 → `[Box, …]`（confidence 保持 None，与本脚本原行为一致）。"""
    return _ocr.boxes(frame, with_confidence=False)


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
    # WEEK=7 用来复盘「第 7 周以后才买六维暴涨卡」；不填 = 读不到周数（按不买）。
    week = int(os.environ.get("WEEK", "0")) or None
    best, detail, _ = probe.gacha_candidates(
        cards, stamina, stamina_max, money, week=week)
    print(f"候选明细（精力 {stamina}/{stamina_max}，零用钱 {money}，周 {week}）：{detail}")
    print("选中：", None if best is None else
          f"{best['name']}（{best['quality']}，{best['price']}币，score={best['score']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
