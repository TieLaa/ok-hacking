"""顶栏精力条「⚡ 图标被并进数字」的清洗回归。

为什么要有：
    整帧 OCR 会把精力条左端固定位置的 ⚡ 图标并进数字框，输出 `3 54/130`
    / `381/130` / `3130/130`，记录和监控页看着像「数字错了」。图标在模型
    眼里不是字符（单独裁出来放大 x3~x8 都返回空），所以只能在 OCR 出口
    把多出来的那一位丢掉。

    判据很窄（当前值 > 上限才算），改一行就可能误伤正常进度条，所以钉死。

样本：2026-11-03 扫 `logs/debug/**/ocr.txt` 的全部 177 条顶栏精力文本，
129 条带图标、73 条正常。

运行：
    .venv\\Scripts\\python.exe -m unittest tests.test_topbar_energy -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.ui.topbar_energy import (strip_icon_digit,  # noqa: E402
                                        strip_icon_reading)


class _Text:
    """模拟 ok-script 的 OCR 结果对象（Box）。"""

    def __init__(self, name, x=300, y=20, width=170, height=43):
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class StripIconDigitTest(unittest.TestCase):
    """真实样本 + 不该动的边界。"""

    # 实测：图标被并进数字框时的原始文本 → 清洗结果
    WITH_ICON = {
        "3 54/130": "54/130",
        "3 164/164": "164/164",
        "381/130": "81/130",
        "349/130": "49/130",
        "3118/130": "118/130",
        "3130/130": "130/130",
        "3121/121": "121/121",
        "350/130": "50/130",
        "3104/130": "104/130",
    }

    # 实测：正常读数（含正文里其它进度条），一个都不能动
    WITHOUT_ICON = [
        "105/145", "105/130", "100/130", "0/130", "92/130",
        "13/20", "111/150+", "83/150+", "1250/1550", "/1722",
    ]

    def test_real_samples_are_cleaned(self):
        for raw, want in self.WITH_ICON.items():
            with self.subTest(raw=raw):
                self.assertEqual(strip_icon_digit(raw), want)

    def test_normal_values_are_untouched(self):
        for raw in self.WITHOUT_ICON:
            with self.subTest(raw=raw):
                self.assertEqual(strip_icon_digit(raw), raw)

    def test_non_number_text_is_ignored(self):
        for raw in ("", "第1周", "星期三", "精力+10", None):
            with self.subTest(raw=raw):
                self.assertEqual(strip_icon_digit(raw), raw)

    def test_readings_are_cleaned_in_place_without_moving_boxes(self):
        """只改 name，坐标不许动 —— 下游 `texts_in_region` / 点击点都靠它。"""
        texts = [_Text("3 54/130", x=307, y=13, width=172, height=43),
                 _Text("105/145", x=357, y=14, width=139, height=42),
                 _Text("", x=0, y=0)]
        out = strip_icon_reading(texts)
        self.assertIs(out, texts)
        self.assertEqual([t.name for t in out], ["54/130", "105/145", ""])
        self.assertEqual((out[0].x, out[0].y, out[0].width, out[0].height),
                         (307, 13, 172, 43))


if __name__ == "__main__":
    unittest.main(verbosity=2)
