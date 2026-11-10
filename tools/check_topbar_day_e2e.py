"""端到端验证：整帧 OCR 把星期几**读丢**时，区域重读能不能救回来。

跑法（仓库根目录）：
    .venv\\Scripts\\python.exe tools\\check_topbar_day_e2e.py

`tools/check_topbar_day.py` 验的是**统计口径**（整体一致率 / 救回率）；
这个脚本验的是**单帧真实链路**：拿 `logs/` 里一张整帧只读到「星期」的存证帧，
用真 OCR 引擎跑一遍，确认：
1. 整帧 OCR 确实读不出星期几；
2. `topbar_day.read_day()` 能把它救回「星期一」。

退出码：0 = 通过，1 = 有问题。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402

from src.tasks.ui import topbar_day  # noqa: E402
from tools._evidence_scan import iter_day_lines  # noqa: E402  新老结构都认（按局分目录）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESIDUAL_FORMS = ("星期", "星期-")


class Box:
    """和 ok-script 的 Box 一样的最小实现（只要 name / confidence）。"""

    def __init__(self, name, confidence=1.0):
        self.name = name
        self.confidence = confidence
        self.x = self.y = self.width = self.height = 0


class Engine:
    """模拟 ok-script 的 `self.ocr`：返回 Box 列表，并记录调用次数。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def ocr(self, box=None, frame=None, **kwargs):
        self.calls += 1
        res = self._inner.ocr(frame)
        page = res[0] if isinstance(res, list) and len(res) == 1 else res
        out = []
        for entry in page or []:
            try:
                _b, (text, score) = entry[0], entry[1]
            except Exception:                            # noqa: BLE001
                continue
            if text:
                out.append(Box(text, float(score) if score is not None else 1.0))
        return out


def find_residual_frame(engine):
    """找一张**当前**整帧 OCR 确实读不出星期几的存证帧。

    为什么不能只看 .txt 里记录的形态：丢字是**偶发**的 —— 同一张图，
    存证当时读成「星期-」，现在再跑一次可能就读出「星期一」了
    （实测 `logs/unknown/1030-183349_unknown_s375.jpg` 就是这样）。
    所以必须**当场重跑整帧 OCR** 来确认，不能信历史记录。
    """
    logs = os.path.join(ROOT, "logs")
    for _p, jpg, hits in iter_day_lines(logs):
        hit = next(((s, x) for s, x, _y in hits if s in RESIDUAL_FORMS), None)
        if not hit:
            continue
        img = cv2.imread(jpg)
        if img is None:
            continue
        # 当场重跑整帧 OCR，确认这帧此刻真的读不出
        names = [b.name for b in engine.ocr(frame=img)]
        joined = " ".join(names)
        if re.search(r"星期\s*([一二三四五六日天])", joined.replace(" ", "")):
            continue          # 现在读得出 → 不是丢字帧
        return hit, jpg, img, names
    return None, None, None, None


def main():
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr

    engine = Engine(ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, show_log=False))

    form, jpg, img, names = find_residual_frame(engine)
    if not jpg:
        print("logs/ 下找不到「此刻仍读不出星期几」的存证帧")
        print("（丢字是偶发的；这张图当时读丢了、现在能读出，说明引擎有随机性）")
        return 0
    print(f"残框帧：{jpg.split('logs')[-1]}  存证时整帧 OCR 记录为 {form!r}")
    print(f"整帧 OCR（此刻重跑）：{names[:8]}")
    joined = " ".join(names)
    m = re.search(r"星期\s*([一二三四五六日天])", joined.replace(" ", ""))
    print(f"  解析出的星期：{m.group(1) if m else None}")

    before = engine.calls
    day = topbar_day.read_day(engine, img)
    print(f"区域重读：星期{day}   （额外 OCR 调用 {engine.calls - before} 次）")

    if m:
        print()
        print("FAIL：这帧整帧就能读出星期几，测不到丢字路径")
        return 1
    if day != "一":
        print()
        print(f"FAIL：区域重读应救回「一」，实际 {day!r}")
        return 1
    print()
    print("PASS：整帧丢字 → 区域重读救回「星期一」")
    return 0


if __name__ == "__main__":
    sys.exit(main())
