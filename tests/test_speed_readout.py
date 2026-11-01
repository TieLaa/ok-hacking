"""倍速档位判读的离线回归（整帧样本 → 判读结果）。

为什么要有这些用例：
    倍速图标「实心三角个数 = 档位」这条判据，是靠 2026-11-01 现场逐档点一遍才定下来的。
    判据一改就可能悄悄退化（比如又回去判「右三角 ROI 蓝占比」），而现场验证要 40 分钟。
    这里用 `logs/topbar/full_states/` 里的整帧当固定样本，几秒钟就能验一遍。

样本从哪来：
    `tools/probe_topbar.py --cycle 4` 现场拍的四档整帧；另外加一张 2026-10-26 的旧帧
    （那时只解锁到 2x，图标里只有两个三角）——它证明判据不依赖「上限=3」。

判据（详见 docs/UI_MAP.md）：
    · 还有白色空心三角 → 没到当前存档的最高档（at_max=False）
    · 一个空心都没有   → 已到顶（at_max=True）
    · OFF 是灰字「OFF 加速」，没有三角，像素上和全实心几乎一样 → 只能靠 OCR 文字判

运行：
    .venv\\Scripts\\python.exe -m unittest tests.test_speed_readout -v
"""

import os
import sys
import unittest

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.ui import topbar  # noqa: E402

STATE_DIR = os.path.join(ROOT, "logs", "topbar", "full_states")


class _OcrText:
    """模拟 ok-script 的 OCR 结果对象（属性都是像素坐标）。"""

    def __init__(self, name, x, y, width, height):
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class SpeedReadoutTest(unittest.TestCase):
    """四档整帧 + 旧存档对照帧。"""

    # (文件名, 期望 at_max, 该帧右上角的 OCR 文本)
    CASES = [
        ("full_2.png", False, [_OcrText("1x", 1165, 50, 40, 30)]),
        ("full_3.png", False, []),          # 2x 的「2x」小字实测会漏 → 只能靠像素
        ("full_0.png", True, []),           # 3x：全实心
        ("full_1.png", True, [_OcrText("OFF", 1150, 20, 50, 30),
                              _OcrText("加速", 1150, 55, 50, 30)]),   # OFF：像素不可信，靠 OCR
    ]

    def test_band_stats_are_discriminative(self):
        """实心/空心占比要能把「到顶」和「没到顶」分开（空心占比 0.00 vs 0.12/0.25）。"""
        ratios = {}
        for name in ("full_0.png", "full_3.png", "full_2.png"):
            path = os.path.join(STATE_DIR, name)
            if not os.path.exists(path):
                self.skipTest(f"缺少样本 {path}")
            stats = topbar.tri_band_stats(cv2.imread(path))
            self.assertIsNotNone(stats)
            ratios[name] = stats[1]
        self.assertLess(ratios["full_0.png"], topbar.SPEED_HOLLOW_MIN)
        self.assertGreater(ratios["full_3.png"], topbar.SPEED_HOLLOW_MIN)
        self.assertGreater(ratios["full_2.png"], topbar.SPEED_HOLLOW_MIN)

    def test_at_max_per_state(self):
        """逐帧判「是否已到本存档最高档」。"""
        for name, want_max, _texts in self.CASES:
            path = os.path.join(STATE_DIR, name)
            if not os.path.exists(path):
                self.skipTest(f"缺少样本 {path}")
            with self.subTest(frame=name):
                self.assertEqual(topbar.speed_at_max(cv2.imread(path)), want_max,
                                 f"{name} 的 at_max 判错了（判据见 docs/UI_MAP.md）")

    def test_off_detected_by_text_only(self):
        """OFF 态只能靠 OCR 文字判，像素判读在 OFF 上不可信（会误判成已到顶）。"""
        for name, _want_max, texts in self.CASES:
            path = os.path.join(STATE_DIR, name)
            if not os.path.exists(path):
                self.skipTest(f"缺少样本 {path}")
            frame = cv2.imread(path)
            h, w = frame.shape[:2]
            off = topbar.off_text(texts, w, h)
            with self.subTest(frame=name, off=off):
                # OFF 帧必须判出 off=True；其它帧不许误判成 OFF
                self.assertEqual(off, any("OFF" in t.name for t in texts))

    def test_old_save_with_2x_cap(self):
        """2026-10-26 的旧帧：当时只解锁到 2x，2x 就是顶 —— 判据不该依赖「上限=3」。"""
        path = os.path.join(ROOT, "docs", "shots", "2026-10-26-subject-crit.png")
        if not os.path.exists(path):
            self.skipTest(f"缺少样本 {path}")
        self.assertTrue(topbar.speed_at_max(cv2.imread(path)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
