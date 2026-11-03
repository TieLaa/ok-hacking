"""顶栏精力条左端 ⚡ 图标的识别噪声：把多出来的那一位丢掉。

游戏顶栏精力条左端有一个 ⚡ 图标，**位置固定、纯装饰**。整帧 OCR 会把它
并进数字框，输出 `3 54/130` / `381/130` / `3130/130`。

2026-11-03 实测结论：图标在模型眼里**不是字符** —— 按 `ocr.txt` 的真实框
坐标把它单独裁出来放大 x3~x8 再识别，返回的是空列表；只有并进数字框、
被要求「输出一串字符」时才会被猜成 3。

用户口径（2026-11-03）：「把 ⚡ 排除出去，反正没有用，只是多个图标好看」。
所以这里不还原、不打 ⚡，直接把多出来的那一位**丢掉**。

判据只用文本，不看坐标和分辨率：**当前值 > 上限** 就是图标 ——
真实精力不可能超过上限。

    3 54/130   → 54/130
    381/130    → 81/130
    3130/130   → 130/130
    ─────────────────────
    105/145    → 不动（105 ≤ 145）
    13/20      → 不动（别的进度条）

实测覆盖 `logs/debug` 全部 177 条顶栏精力文本：129 条命中、73 条正常值
（含 `13/20`、`111/150+`）零误伤。

过滤点跟 `drop_icon_noise` 一样挂在 `_ocr_region`（全项目唯一的 OCR 出口），
handler / `_tick_texts` / 存证 `ocr.txt` 一次全干净。
"""

import re

# 首位多出来的数字 + 真实数字 + /上限
_ICON_PREFIX_RE = re.compile(r"^(\d)(\d{1,3})/(\d{1,3})$")


def strip_icon_digit(name):
    """`3 54/130` → `54/130`；不像「图标被并框」就原样返回。"""
    s = (name or "").replace(" ", "").strip()
    m = _ICON_PREFIX_RE.match(s)
    if not m:
        return name
    if int(m.group(1) + m.group(2)) <= int(m.group(3)):
        return name
    return f"{m.group(2)}/{m.group(3)}"


def strip_icon_reading(texts):
    """就地把一批 OCR 结果里被图标污染的文本改干净，返回同一个列表。

    只改 `name`，`x/y/width/height` 原样保留 —— 下游按坐标做的判读
    （`texts_in_region`、点击点）不受影响。
    """
    for t in texts or []:
        name = getattr(t, "name", None)
        if not name:
            continue
        fixed = strip_icon_digit(name)
        if fixed != name:
            t.name = fixed
    return texts
