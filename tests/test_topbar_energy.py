"""顶栏精力条「⚡ 图标被并进数字」的清洗回归。

为什么要有：
    整帧 OCR 会把精力条左端固定位置的 ⚡ 图标并进数字框，输出 `3 54/130`
    / `381/130` / `3130/130`，记录和监控页看着像「数字错了」。图标在模型
    眼里不是字符（单独裁出来放大 x3~x8 都返回空），所以只能在 OCR 出口
    把多出来的那一位丢掉。

    2026-11-03 实跑又发现**第二种坏法**：斜杠被吃成 `1` —— `3921130`
    (=3 92/130)、`3 651130`(=3 65/130)、`521130`(=52/130)。这两种混在一起，
    判据很窄（当前值 > 上限才算图标），改一行就可能误伤正常数字，所以钉死。

样本：2026-11-03 扫 `logs/debug/**/ocr.txt` 的全部 177 条顶栏精力文本，
129 条带图标、73 条正常；B 支样本取自当天 19:15~19:26 的 MCP 实跑日志。

运行：
    .venv\\Scripts\\python.exe -m unittest tests.test_topbar_energy -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.ui.topbar import (strip_icon_digit,  # noqa: E402
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

    # A 支：图标被并进数字框（斜杠还在）时的原始文本 → 清洗结果
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

    # B 支：斜杠被 OCR 吃成 '1'（2026-11-03 实跑日志原文）
    SLASH_LOST = {
        "3921130": "92/130",     # 图标 3 + 92 + 1 + 130
        "3 651130": "65/130",
        "3 221130": "22/130",
        "3 921130": "92/130",
        "3 671130": "67/130",
        "3111130": "11/130",     # 图标 3 + 11 + 1 + 130（7 位）
        "311130": "31/130",      # 没有图标：31 + 1 + 130（6 位，别把 3 当图标）
        "521130": "52/130",      # 没有图标，斜杠丢了
        "451130": "45/130",
        # 天然歧义：「3 + 0/130(丢斜杠)」和「30/130」文本一模一样。
        # 无法区分时**优先不改坏**（30/130 是合法读数），只补回斜杠。
        "301130": "30/130",
    }

    # 实测：正常读数（含正文里其它进度条），一个都不能动
    WITHOUT_ICON = [
        "105/145", "105/130", "100/130", "0/130", "92/130",
        "13/20", "111/150+", "83/150+", "1250/1550", "/1722",
        # B 支的同形近邻：上限量级不对 / 结构不对，统统不许碰
        "21500",        # 零用钱：末尾 500 超出精力上限量级
        "1250",         # 零用钱 4 位
        "8191550",      # 六维：上限 4 位数
        "6908",         # 球队评分
        "1001/9",       # 价格/数量：上限 9 不在精力量级
        "494/70",       # 同上：上限 70 不在精力量级（历史上这类有一大批）
        "1250/8",
        "11228",        # 没图标又只剩一位前缀：分不清零用钱还是 1/228 → 不动
        "71117",
    ]

    def test_real_samples_are_cleaned(self):
        for raw, want in self.WITH_ICON.items():
            with self.subTest(raw=raw):
                self.assertEqual(strip_icon_digit(raw), want)

    def test_slash_lost_samples_are_cleaned(self):
        for raw, want in self.SLASH_LOST.items():
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

    def test_known_cap_pins_the_slash_lost_branch(self):
        """同帧有读对了的上限时，B 支只认它 —— 对不上就跳过（宁可少洗）。"""
        self.assertEqual(strip_icon_digit("3 651130", known_cap=130), "65/130")
        self.assertEqual(strip_icon_digit("3 651130", known_cap=145), "3 651130")

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

    def test_frame_cap_is_reused_for_slash_lost_neighbours(self):
        """同一帧先读到 `60/130` → 旁边那条 `3921130` 也按 130 洗。"""
        texts = [_Text("60/130", x=357, y=14, width=139, height=42),
                 _Text("3921130", x=307, y=13, width=172, height=43)]
        strip_icon_reading(texts)
        self.assertEqual([t.name for t in texts], ["60/130", "92/130"])

    def test_money_like_frame_cap_is_not_used(self):
        """帧里只有 4 位上限（六维 /1550）时不给 B 支定上限，那就走量级护栏。"""
        texts = [_Text("1250/1550"), _Text("3921130")]
        strip_icon_reading(texts)
        self.assertEqual([t.name for t in texts], ["1250/1550", "92/130"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
