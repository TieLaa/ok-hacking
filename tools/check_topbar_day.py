"""离线校验：顶栏星期几的整帧 OCR vs 区域重读（2026-11-02 丢字事故的回归门）。

跑法（在仓库根目录）：
    .venv\\Scripts\\python.exe tools\\check_topbar_day.py

它做什么：
1. 扫 `logs/` 下所有存证帧（`logs/unknown/*.jpg` + `logs/debug/**/*.jpg`），
   取出旁边的 `.txt`（整帧 OCR 结果）；
2. 记录里带「第N周 星期X」或残框（星期 / 星期- / 期日 / 期大）的帧算候选；
3. 对每个候选跑一次区域重读，和记录值对比；
4. 报告：
   - 整帧 OCR 读得出时，区域重读是否与它**完全一致**（不该有分歧）；
   - 整帧 OCR 读不出时，区域重读救回了多少。

判定标准（与 docs/RULES.md「顶栏星期几」一节一致）：
  - 「一致率」必须是 100%：区域重读只允许在整帧读不出时改变结论；
  - 「救回率」必须 ≥ 95%。

退出码：0 = 通过，1 = 有回归。
"""
import os
import re
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402

from src.tasks.ui import topbar_day  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")

BOX_RE = re.compile(r"^(.*?)\s*\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)\s*$")
KNOWN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
# 整帧 OCR 读星期几时用的正则是 labels.DAY_RE（**包含**匹配，容许「星期天」）
DAY_LOOSE_RE = re.compile(r"星期\s*([一二三四五六日天])")


def ocr_engine():
    """和项目运行时用同一个引擎（src/config.py: onnxocr + openvino）。"""
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr

    return ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, show_log=False)


class _Engine:
    """把 onnxocr 包装成和 ok-script 的 `self.ocr` 一样的调用方式。

    ok-script 的 `ocr(box=..., frame=...)` 返回 Box 列表（.name/.confidence）；
    这里做同样的事，让 `topbar_day` 的代码在离线校验里能原样跑。
    """

    def __init__(self, engine):
        self._engine = engine

    def ocr(self, box=None, frame=None, **kwargs):
        img = frame
        res = self._engine.ocr(img)
        page = res[0] if isinstance(res, list) and len(res) == 1 else res
        out = []
        for entry in page or []:
            try:
                _b, (text, score) = entry[0], entry[1]
            except Exception:                            # noqa: BLE001
                continue
            if not text:
                continue
            out.append(_Box(text, float(score) if score is not None else 1.0))
        return out


class _Box:
    def __init__(self, name, confidence):
        self.name = name
        self.confidence = confidence
        self.x = self.y = self.width = self.height = 0


def collect_candidates():
    """扫 logs/，返回 [(记录里的星期形态, 图片路径), …]。"""
    out = []
    for sub in ("unknown", "debug"):
        base = os.path.join(LOGS, sub)
        if not os.path.isdir(base):
            continue
        for dp, _dn, fn in os.walk(base):
            for f in fn:
                if not f.endswith(".txt"):
                    continue
                p = os.path.join(dp, f)
                try:
                    content = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                form = None
                for line in content.splitlines():
                    m = BOX_RE.match(line)
                    if not m:
                        continue
                    s = m.group(1).strip()
                    x = int(m.group(2))
                    y = int(m.group(3))
                    if y < 60 and 120 <= x <= 300 and ("期" in s or "星" in s):
                        form = s
                        break
                if form is None:
                    continue
                jpg = os.path.splitext(p)[0] + ".jpg"
                if not os.path.exists(jpg):
                    jpg = os.path.join(dp, "frame.jpg")
                if not os.path.exists(jpg):
                    continue
                out.append((form, jpg))
    return out


def main():
    cands = collect_candidates()
    if not cands:
        print("logs/ 下没有可用的存证帧（先跑一次实机任务）")
        return 0
    print(f"候选帧：{len(cands)}")

    engine = _Engine(ocr_engine())

    same = 0
    same_total = 0
    diff = collections.Counter()
    rescued = 0
    rescue_total = 0
    rescue_by_form = collections.Counter()

    for form, jpg in cands:
        img = cv2.imread(jpg)
        if img is None:
            continue
        zoom = topbar_day.read_day(engine, img)
        zoom_name = f"星期{zoom}" if zoom else None
        m = DAY_LOOSE_RE.search(form.replace(" ", ""))
        loose = m.group(1) if m else None
        if loose:
            # 整帧读得出：区域重读必须**完全一致**
            same_total += 1
            if zoom == loose:
                same += 1
            else:
                diff[(loose, zoom)] += 1
        else:
            # 整帧读不出：区域重读应该救回来
            rescue_total += 1
            rescue_by_form[(form, zoom)] += 1
            if zoom:
                rescued += 1

    print()
    print("=== 整帧读得出时，区域重读是否一致 ===")
    print(f"  一致 {same}/{same_total}"
          + (f" ({same / same_total * 100:.1f}%)" if same_total else ""))
    for k, v in diff.most_common(20):
        print(f"    分歧 整帧={k[0]!r} 区域={k[1]!r}: {v}")

    print()
    print("=== 整帧读不出时，区域重读救回情况 ===")
    print(f"  救回 {rescued}/{rescue_total}"
          + (f" ({rescued / rescue_total * 100:.1f}%)" if rescue_total else ""))
    for (form, zoom), v in rescue_by_form.most_common(20):
        print(f"    整帧={form!r:12} 区域={('星期' + zoom) if zoom else None!r:12} {v}")

    ok = True
    if same_total and same != same_total:
        print()
        print("FAIL：区域重读与整帧 OCR 有分歧 —— 兜底会改变已读对的结论")
        ok = False
    if rescue_total and rescued / rescue_total < 0.95:
        print()
        print(f"FAIL：救回率 {rescued / rescue_total * 100:.1f}% < 95%")
        ok = False
    print()
    print("PASS" if ok else "有回归")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
