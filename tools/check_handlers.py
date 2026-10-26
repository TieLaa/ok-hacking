"""离线回归：用**已有截图**验证所有 handler 的判据，不启动游戏。

为什么要有这个：
    之前每改一次判据都要「重启 ok-script → 等游戏加载 → 跑 60 秒 → 看日志」，
    一轮 5~10 分钟，而且只有真撞上那个界面才知道错。
    现在改成：截图 → OCR → 对全部 handler 干跑一遍 → 3 秒出结果。

用法：
    python tools/check_handlers.py                  # 扫 docs/shots/*.png，比对 expected.json
    python tools/check_handlers.py --cache          # 复用 OCR 缓存（快 5 倍）
    python tools/check_handlers.py --all-hits       # 额外列出「同时命中」的 handler（查抢占）
    python tools/check_handlers.py 图.png            # 只测指定图片
    python tools/check_handlers.py --update-expected # 登记新图的期望值（人工确认后再提交）

退出码：有「未命中」或「和 expected.json 不一致」时返回 1，可以直接进 pre-commit / CI。
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
        self._shop_key = None
        self._shop_spend_all = False
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
    """返回 (path, [(handler_name, actions), ...])，按优先级列出**所有**命中。

    每个 handler 用独立的 DryRunTask，避免前面的命中污染后面的判据。
    """
    frame = cv2.imread(path)
    if frame is None:
        return path, []
    boxes = load_boxes(path, ocr_holder, use_cache)
    if boxes is None:
        return path, []
    hits = []
    for handler in TrainingTask.HANDLERS:
        task = DryRunTask(frame, boxes)
        try:
            if handler(task, boxes):
                hits.append((handler.__name__, task.actions))
        except Exception as e:
            if verbose:
                print(f"    ! {handler.__name__} 抛异常: {type(e).__name__}: {e}")
    return path, hits


EXPECTED_PATH = os.path.join(ROOT, "docs", "shots", "expected.json")


def load_expected():
    """期望表 {截图文件名: 应该命中的 handler}。

    新截图先跑 `--update-expected` 落盘，再人工确认一次结果是否合理。
    """
    if not os.path.exists(EXPECTED_PATH):
        return {}
    with open(EXPECTED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_expected(exp):
    with open(EXPECTED_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dict(sorted(exp.items())), f, ensure_ascii=False, indent=2)
        f.write("\n")


def describe(actions):
    if not actions:
        return "-"
    return " ".join(str(x) for x in actions[0])[:46]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    verbose = "--verbose" in sys.argv
    use_cache = "--cache" in sys.argv
    show_all = "--all-hits" in sys.argv
    update = "--update-expected" in sys.argv

    if args:
        paths = []
        for a in args:
            paths.extend(glob.glob(os.path.join(a, "*.png")) if os.path.isdir(a) else [a])
    else:
        paths = sorted(glob.glob(os.path.join(ROOT, "docs", "shots", "*.png")))

    if not paths:
        print("没有找到图片")
        return 1

    expected = load_expected()
    ocr_holder = {"ocr": None}
    fails, misses, new_shots, conflicts = [], [], [], []

    print(f"{'图片':<38} {'实际':<24} 期望 / 动作")
    print("-" * 96)
    for path in paths:
        name, hits = probe(path, ocr_holder, use_cache, verbose)
        base = os.path.basename(name)
        first = hits[0][0] if hits else None
        want = expected.get(base)

        if update and first:
            expected[base] = first
            want = first

        if first is None:
            misses.append(base)
            print(f"{base:<38} {'✗ 未命中':<24} {want or '-'}")
        elif want is None:
            new_shots.append(base)
            print(f"{base:<38} {first:<24} 新图（未登记） {describe(hits[0][1])}")
        elif first != want:
            fails.append((base, want, first))
            print(f"{base:<38} {first:<24} 期望 {want}  ✗ 抢错")
        else:
            print(f"{base:<38} {first:<24} {describe(hits[0][1])}")

        if show_all and len(hits) > 1:
            conflicts.append((base, [h[0] for h in hits[1:]]))
            for hname, _ in hits[1:]:
                print(f"    + 同时命中 {hname}")

    if update:
        save_expected(expected)
        print(f"已更新 {EXPECTED_PATH}（{len(expected)} 条）")

    print("-" * 96)
    if misses:
        print(f"未命中（{len(misses)}）：" + "、".join(misses))
    if fails:
        print(f"抢错（{len(fails)}）：" + "、".join(
            f"{b} 期望 {w} 实际 {a}" for b, w, a in fails))
    if new_shots:
        print(f"未登记（{len(new_shots)}）：" + "、".join(new_shots))
        print("  确认结果正确后跑：python tools/check_handlers.py --update-expected")
    if show_all and conflicts:
        print(f"多 handler 同时命中（{len(conflicts)}）：")
        for base, others in conflicts:
            print(f"  {base}: 首个 {expected.get(base, '?')}，另外 {'、'.join(others)}")

    if fails or misses:
        print("回归失败")
        return 1
    print("回归通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
