"""离线回归：用**已有截图**验证所有 handler 的判据，不启动游戏。

为什么要有这个：
    之前每改一次判据都要「重启 ok-script → 等游戏加载 → 跑 60 秒 → 看日志」，
    一轮 5~10 分钟，而且只有真撞上那个界面才知道错。
    8 个界面就要跑 8 轮，还常常"修一个冒一个"。

    现在改成：截图 → OCR → 对 25 个 handler 干跑一遍 → 3 秒出结果。
    改完代码立刻知道哪张图判错了。

用法：
    python tools/check_handlers.py                  # 扫 docs/shots/*.png
    python tools/check_handlers.py --all docs/shots logs/unknown
    python tools/check_handlers.py 图.png            # 只测指定图片
    python tools/check_handlers.py --cache           # 复用 OCR 缓存（快 5 倍）

输出：
    每张图 → 命中的 handler → 打算点击的位置
    没命中的会标出来 —— 那就是需要补判据的地方。
"""

import glob
import json
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402

from src.tasks.BaseGirlsTask import BaseGirlsTask  # noqa: E402
from src.tasks.TrainingTask import TrainingTask  # noqa: E402

CACHE_DIR = os.path.join(ROOT, "docs", "shots", ".ocr_cache")


def run_ocr(frame):
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr

    ocr = ONNXPaddleOcr(use_angle_cls=False, use_openvino=True, show_log=False)
    return ocr


def ocr_to_boxes(ocr, frame):
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
        items.append(Box(x=min(xs), y=min(ys), width=max(xs) - min(xs),
                         height=max(ys) - min(ys), confidence=float(score), name=text))
    return items


class _FakeMethod:
    def __init__(self, width, height):
        self.width = width
        self.height = height


class _FakeExecutor:
    """`frame` / `width` / `height` 都是 BaseTask 的 property，底层读 self._executor，这里给个假的。"""

    def __init__(self, frame):
        self.frame = frame
        self.method = _FakeMethod(frame.shape[1], frame.shape[0])


class DryRunTask(TrainingTask):
    """只判断、不动作的 TrainingTask。

    覆盖掉所有会产生副作用的方法（点击 / 等待 / OCR 请求），
    这样可以把真实 handler 当纯函数跑。
    """

    def __init__(self, frame, texts):
        self._executor = _FakeExecutor(frame)
        self._texts = texts
        # 空配置即可：所有 handler 里都写了 `config.get(key, 默认值)`，
        # 这样回归测的就是**代码里的默认策略**，不依赖 configs/*.json。
        self.config = {}
        self.actions = []
        self.logs = []
        self.name = "dry-run"
        # handler 里会用到的一些状态
        self._steps = 1
        self._skip_tries = 0
        self._last_state = None
        self._picked_subject = False
        self._picked_index = 0
        self._match_flags = {"manual_fixed": False, "fast_clicked": False, "last_fast_at": 0.0}
        self._weekend = {"date": None, "shop_checked": False, "bought": False, "park_choice": None}
        self._shop_week = None
        self._shop_day = None
        self._shop_at = 0.0
        self._shop_seen = False
        self._speed_week = None
        self._speed_locked = False
        self._subject_cache = {"date": None, "best": None}
        self._force_rest = False

    # ---------- 坐标换算 ----------
    def width_of_screen(self, ratio):
        return self.width * ratio

    def height_of_screen(self, ratio):
        return self.height * ratio

    def box_of_screen(self, x1, y1, x2, y2):
        return Box(x=int(self.width * x1), y=int(self.height * y1),
                   width=int(self.width * (x2 - x1)), height=int(self.height * (y2 - y1)))

    # ---------- OCR ----------
    def _ocr_region(self, region_name=None, box=None):
        return self._texts

    # ---------- 动作（只记录） ----------
    def click(self, x=-1, y=-1, **kwargs):
        self.actions.append(("click", int(x), int(y)))

    def click_box(self, box, **kwargs):
        cx = int(box.x + box.width / 2)
        cy = int(box.y + box.height / 2)
        self.actions.append(("click_box", box.name, cx, cy))

    def click_text(self, names, region_name=None, box=None, must=True, **kwargs):
        target = self.find_text(names, region_name, box)
        if target is None:
            return False
        self.click_box(target)
        return True

    def click_region(self, region_name, **kwargs):
        self.actions.append(("click_region", region_name))
        return True

    def click_and_expect(self, x, y, expect, **kwargs):
        self.actions.append(("click_and_expect", int(x), int(y), str(expect)))
        return True

    def click_main_action(self, **kwargs):
        return self.click_region("main_action")

    def back(self, **kwargs):
        self.actions.append(("back",))

    def sleep(self, *args, **kwargs):
        pass

    def info_set(self, *args, **kwargs):
        pass

    # ---------- 日志 ----------
    def log_info(self, msg, **kwargs):
        self.logs.append(str(msg))

    log_debug = log_info
    log_warning = log_info
    log_error = log_info

    # ---------- 复用真实实现 ----------
    def find_text(self, names, region_name=None, box=None):
        return BaseGirlsTask.find_text(self, names, region_name, box)


def load_boxes(path, ocr_holder, use_cache=True):
    if use_cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache = os.path.join(CACHE_DIR, os.path.basename(path) + ".json")
        if os.path.exists(cache):
            with open(cache, "r", encoding="utf-8") as f:
                return [Box(**d) for d in json.load(f)]
    frame = cv2.imread(path)
    if frame is None:
        return None
    if ocr_holder.get("ocr") is None:
        ocr_holder["ocr"] = run_ocr(frame)
    boxes = ocr_to_boxes(ocr_holder["ocr"], frame)
    if use_cache:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump([{"x": b.x, "y": b.y, "width": b.width, "height": b.height,
                        "confidence": b.confidence, "name": b.name} for b in boxes], f,
                      ensure_ascii=False)
    return boxes


def probe(path, ocr_holder, use_cache=True, verbose=False):
    frame = cv2.imread(path)
    if frame is None:
        return path, None, []
    boxes = load_boxes(path, ocr_holder, use_cache)
    if boxes is None:
        return path, None, []
    task = DryRunTask(frame, boxes)
    for handler in TrainingTask.HANDLERS:
        try:
            if handler(task, boxes):
                return path, handler.__name__, task.actions
        except Exception as e:
            if verbose:
                print(f"    ! {handler.__name__} 抛异常: {type(e).__name__}: {e}")
    return path, None, []


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    verbose = "--verbose" in sys.argv
    use_cache = "--cache" in sys.argv

    if args:
        paths = []
        for a in args:
            paths.extend(glob.glob(os.path.join(a, "*.png")) if os.path.isdir(a) else [a])
    else:
        paths = sorted(glob.glob(os.path.join(ROOT, "docs", "shots", "*.png")))

    if not paths:
        print("没有找到图片")
        return 1

    ocr_holder = {"ocr": None}
    print(f"{'图片':<40} {'命中 handler':<26} 动作")
    print("-" * 100)
    misses = []
    for path in paths:
        name, handler, actions = probe(path, ocr_holder, use_cache, verbose)
        short = os.path.basename(name)[:38]
        if handler is None:
            misses.append(short)
            print(f"{short:<40} {'✗ 未命中':<26} -")
        else:
            act = ""
            if actions:
                a = actions[0]
                act = " ".join(str(x) for x in a)[:46]
            print(f"{short:<40} {handler:<26} {act}")

    print("-" * 100)
    if misses:
        print(f"需要补判据的界面（{len(misses)}）：")
        for m in misses:
            print(f"  - {m}")
    else:
        print("全部命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
