"""放大重读模块（`src/tasks/ui/zoom_reread.py`）的纯逻辑测试。

机制：裁一块 → 放大 x3 → 重跑 OCR。它替代的是过去散在 `topbar_day.py` 里的
一份私有实现，也接上了六维面板「读不准就重判」的路（2026-11-04 用户口径：
「故障多发的就用这个方法重判」）。

运行：
    .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_zoom_reread.py" -v
"""

import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ok.feature.Box import Box  # noqa: E402
from src.tasks.ui import topbar_day, zoom_reread  # noqa: E402
from tests.test_queue_stamina import box, task_with  # noqa: E402


def fake_box(name, score=0.95):
    return Box(x=0, y=0, width=10, height=10, name=name, confidence=score)


class Cropping(unittest.TestCase):

    def test_crop_px_clamps_to_frame(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        crop = zoom_reread.crop_px(frame, -50, -50, 500, 500)
        self.assertEqual(crop.shape[:2], (100, 200))

    def test_crop_px_rejects_degenerate(self):
        self.assertIsNone(zoom_reread.crop_px(None, 0, 0, 10, 10))
        self.assertIsNone(zoom_reread.crop_px(np.zeros((0, 0, 3)), 0, 0, 1, 1))

    def test_crop_region_is_normalized(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        crop = zoom_reread.crop_region(frame, (0.5, 0.5, 1.0, 1.0))
        self.assertEqual(crop.shape[:2], (50, 100))

    def test_enlarge_multiplies_size(self):
        frame = np.zeros((40, 60, 3), dtype=np.uint8)
        self.assertEqual(zoom_reread.enlarge(frame, 3).shape[:2], (120, 180))
        self.assertEqual(zoom_reread.enlarge(frame, 1).shape[:2], (40, 60))


class OcrCompatibility(unittest.TestCase):
    """三种调用形态都要能吃：OCR 引擎 / 任务实例 / 普通函数。"""

    def frame(self):
        return np.zeros((60, 90, 3), dtype=np.uint8)

    def test_engine_style(self):
        class Engine:
            def __init__(self):
                self.seen = []

            def ocr(self, img, **kwargs):
                self.seen.append(getattr(img, "shape", None))
                return [("星期一", 0.99), ("噪音", 0.10)]
        engine = Engine()
        out = zoom_reread.run_ocr(engine, self.frame())
        self.assertEqual(out, [("星期一", 0.99)])       # 低置信度被滤掉
        self.assertTrue(engine.seen)

    def test_engine_with_box_frame_kwargs_falls_back(self):
        class Engine:
            def ocr(self, img):
                return [("第4周", 1.0)]
        self.assertEqual(zoom_reread.run_ocr(Engine(), self.frame()), [("第4周", 1.0)])

    def test_task_style(self):
        class Task:
            def ocr(self, box=None, frame=None):
                assert frame is not None
                return [fake_box("B1016/1680")]
        self.assertEqual(zoom_reread.run_ocr(Task(), self.frame()), [("B1016/1680", 0.95)])

    def test_plain_function(self):
        self.assertEqual(zoom_reread.run_ocr(lambda img: [("星期天", 1.0)], self.frame()),
                         [("星期天", 1.0)])

    def test_ocr_exception_is_swallowed(self):
        def boom(img):
            raise ValueError("ocr 挂了")
        self.assertEqual(zoom_reread.run_ocr(boom, self.frame()), [])


class RereadPassesEnlargedCrop(unittest.TestCase):

    def test_reread_px_enlarges_before_ocr(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        seen = {}

        def ocr(img):
            seen["shape"] = img.shape
            return [("488/1680", 0.9)]

        out = zoom_reread.reread_px(frame, ocr, (10, 10, 60, 30), pad=0)
        self.assertEqual(out, [("488/1680", 0.9)])
        # 裁 50x20 → x3 → 150x60
        self.assertEqual(seen["shape"][:2], (60, 150))

    def test_pad_widens_the_crop(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        seen = {}

        def ocr(img):
            seen["shape"] = img.shape
            return []

        zoom_reread.reread_px(frame, ocr, (50, 40, 100, 60), pad=0.10)
        self.assertGreater(seen["shape"][1], 150)      # 外扩 10% 后更宽
        self.assertGreater(seen["shape"][0], 60)


class TopbarDayStillWorks(unittest.TestCase):
    """`topbar_day` 改用共用模块后，行为不能变（原本 116/117 帧救回的结论照旧）。"""

    def test_read_day_via_engine(self):
        class Engine:
            def ocr(self, img, **kwargs):
                return [("星期一", 0.99)]
        self.assertEqual(topbar_day.read_day(Engine(), np.zeros((871, 1549, 3), np.uint8)), "一")

    def test_read_day_returns_none_when_nothing_read(self):
        class Engine:
            def ocr(self, img, **kwargs):
                return []
        self.assertIsNone(topbar_day.read_day(Engine(), np.zeros((871, 1549, 3), np.uint8)))

    def test_read_week(self):
        class Engine:
            def ocr(self, img, **kwargs):
                return [("第4周", 0.99)]
        self.assertEqual(topbar_day.read_week(Engine(), np.zeros((871, 1549, 3), np.uint8)), 4)


class AttrCellReread(unittest.TestCase):
    """六维那一格读不准 → 放大重读把它救回来（2026-11-04 事故值 8116）。"""

    def page(self):
        return [
            box(179, 688, "投射", 74, 44), box(266, 688, "B1488/1680", 190, 45),
            box(610, 682, "体能", 100, 47), box(700, 682, "8116/1680", 190, 45),
            box(178, 749, "弹跳", 74, 44), box(266, 749, "C1277/1550", 190, 45),
            box(610, 733, "防守", 100, 45), box(700, 733, "C1282/1709", 190, 45),
            box(178, 794, "运控", 74, 44), box(266, 794, "C+1323/1565", 190, 45),
            box(610, 795, "意识", 100, 45), box(700, 795, "C1114/1689", 190, 45),
        ]

    def test_glitched_cell_is_reread(self):
        page = self.page()
        task = task_with(page, size=(1549, 871))
        calls = []

        def ocr(box=None, frame=None):
            calls.append(getattr(frame, "shape", None))
            return [fake_box("B1016/1680")]

        task.ocr = ocr
        values = task.read_attribute_values(page)
        self.assertEqual(values["体能"], 1016)          # 8116 被拒 → 重读取到 1016
        self.assertEqual(len(calls), 1)                  # 只重读了出问题的那一格
        self.assertEqual(values["投射"], 1488)

    def test_no_reread_when_panel_is_clean(self):
        page = self.page()
        page[3] = box(700, 682, "B1016/1680", 190, 45)
        task = task_with(page, size=(1549, 871))
        calls = []
        task.ocr = lambda box=None, frame=None: (calls.append(1), [])[1]
        values = task.read_attribute_values(page)
        self.assertEqual(values["体能"], 1016)
        self.assertEqual(calls, [])                      # 全读对了就不重读（省 OCR）


if __name__ == "__main__":
    unittest.main(verbosity=2)
