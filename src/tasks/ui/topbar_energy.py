"""顶栏精力条左端 ⚡ 图标的识别噪声：把多出来的那一位丢掉。

游戏顶栏精力条左端有一个 ⚡ 图标，**位置固定、纯装饰**。整帧 OCR 会把它
并进数字框，输出 `3 54/130` / `381/130` / `3130/130`。

2026-11-03 实测结论：图标在模型眼里**不是字符** —— 按 `ocr.txt` 的真实框
坐标把它单独裁出来放大 x3~x8 再识别，返回的是空列表；只有被并进数字框、
被要求「输出一串字符」时才会被猜成 3。

用户口径（2026-11-03）：「把 ⚡ 排除出去，反正没有用，只是多个图标好看」。
所以这里不还原、不打 ⚡，直接把多出来的那一位**丢掉**。

判据只用文本，不看坐标和分辨率。两种坏法都要处理：

**A. 图标并进数字框（斜杠还在）** —— **当前值 > 上限** 就是图标，
真实精力不可能超过上限：

    3 54/130   → 54/130
    381/130    → 81/130
    3130/130   → 130/130
    ─────────────────────
    105/145    → 不动（105 ≤ 145）
    13/20      → 不动（别的进度条）

**B. 斜杠被 OCR 吃成 `1`**（2026-11-03 11:19 实跑批次新发现）——
`9 2/130` 会读成 `921 1 30` 这类，典型样本：

    3921130    → 92/130      （图标 3 + 92 + 1 + 130）
    3 651130   → 65/130
    3 221130   → 22/130
    521130     → 52/130      （这一条**没有**图标，是斜杠丢了）
    451130     → 45/130
    ─────────────────────
    1250/1550  → 不动（六维，上限 4 位数，不进这一支）
    21500      → 不动（零用钱：上限量级不对）

B 支的判据：末尾 3 位是上限、它前面那个 `1` 就是斜杠，剩下的前缀是
「[图标] + 当前值」。前缀 > 上限 ⇒ 首位是图标，丢掉一位；丢完还不 ≤ 上限
就放弃（宁可不洗，也不要把正常数字改坏）。三道护栏（A 支同样只认第二道）：

- **上限必须落在 `MIN_CAP..MAX_CAP`**（实测精力上限 115~164，留到 250），
  这样 `/70`、`/9`、`/441`、`1250/1550` 这些别的进度条/价格不会被当成精力；
- **没图标又只剩一位数**（`11228` = 零用钱？还是 `1/228`？）分不清 → 不动；
- **同一帧里如果已经有一个读对了的 `/130`**，B 支只认这个上限（同一帧的
  精力上限是稳定的），对不上就跳过 —— 见 `strip_icon_reading`。

实测覆盖 `logs/debug` 全部 177 条顶栏精力文本：129 条命中、73 条正常值
（含 `13/20`、`111/150+`）零误伤；B 支用 336 万条历史 OCR 文本回扫过一遍。

过滤点跟 `drop_icon_noise` 一样挂在 `_ocr_region`（全项目唯一的 OCR 出口），
handler / `_tick_texts` / 存证 `ocr.txt` 一次全干净。
"""

import re

# A：首位多出来的数字 + 真实数字 + /上限
_ICON_PREFIX_RE = re.compile(r"^(\d)(\d{1,3})/(\d{1,3})$")

# B：斜杠被吃成 '1' —— 前缀 + '1' + 3 位上限
_SLASH_LOST_RE = re.compile(r"^(\d{1,4})1(\d{3})$")

# 同一帧里「读对了」的 3 位上限（用来给 B 支定上限，防止把别的数字改坏）
_CLEAN_CAP_RE = re.compile(r"^\d{1,4}/(\d{3})$")

# 精力上限的量级（实测 121 / 130 / 145 / 164）。超过这个量级的 3 位数
# （零用钱、六维之类的尾数）一律不碰。
MIN_CAP, MAX_CAP = 100, 250


def _known_cap(texts):
    """同一帧里唯一那个「读对了」的 3 位上限；没有或不止一个就返回 None。"""
    caps = set()
    for t in texts:
        name = (getattr(t, "name", "") or "").replace(" ", "").strip()
        m = _CLEAN_CAP_RE.match(name)
        if m and MIN_CAP <= int(m.group(1)) <= MAX_CAP:
            caps.add(int(m.group(1)))
    return caps.pop() if len(caps) == 1 else None


def strip_icon_digit(name, known_cap=None):
    """`3 54/130` → `54/130`、`3921130` → `92/130`；不像噪声就原样返回。"""
    s = (name or "").replace(" ", "").strip()

    m = _ICON_PREFIX_RE.match(s)
    if m:
        cap = int(m.group(3))
        if not MIN_CAP <= cap <= MAX_CAP:
            return name                      # 上限量级不对 → 不是精力条，别碰
        if int(m.group(1) + m.group(2)) <= cap:
            return name
        return f"{m.group(2)}/{m.group(3)}"

    m = _SLASH_LOST_RE.match(s)
    if not m:
        return name
    value, cap_text = m.group(1), m.group(2)
    cap = int(cap_text)
    if cap_text.startswith("0") or not MIN_CAP <= cap <= MAX_CAP:
        return name
    if known_cap is not None and cap != known_cap:
        return name
    if int(value) > cap:
        # 前缀 > 上限 ⇒ 首位是图标，丢掉那一位
        value = value[1:]
        if not value or int(value) > cap:
            return name
    elif len(value) < 2:
        # 没图标又只剩一位数（`11228` 这种 5 位数）→ 分不清是「零用钱」还是
        # 「1/228」，宁可不洗。实测 52/130、45/130 这类都是两位值。
        return name
    return f"{value}/{cap_text}"


def strip_icon_reading(texts):
    """就地把一批 OCR 结果里被图标污染的文本改干净，返回同一个列表。

    只改 `name`，`x/y/width/height` 原样保留 —— 下游按坐标做的判读
    （`texts_in_region`、点击点）不受影响。
    """
    texts = texts or []
    known_cap = _known_cap(texts)
    for t in texts:
        name = getattr(t, "name", None)
        if not name:
            continue
        fixed = strip_icon_digit(name, known_cap=known_cap)
        if fixed != name:
            t.name = fixed
    return texts
