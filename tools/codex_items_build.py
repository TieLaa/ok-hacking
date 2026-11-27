"""把商品图鉴 TSV 整理成分层分类表（不改运行时购买逻辑）。

分层（用户 2026-11-24 口径）：
  must  = 现金 / 牛奶 / 精力，不看品质，最先看
  style = 训练流：教科书/器材/神之庇佑/同心护符；商店流：教科书/器材
  color = 其余属性商品：品质 → 需求属性 > 推荐属性 > 其他属性
  never = 苦味/怪味（硬不买）；蓝/白由运行时按颜色挡

输入：logs/captures/codex/codex_items.tsv
输出：src/tasks/training/strategy/codex_items.json
      docs/codex_items.md

用法：
    .venv\\Scripts\\python.exe tools\\codex_items_build.py
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.tasks.training import attributes  # noqa: E402
from src.tasks.training.constants import SHOP_ITEMS, SHOP_NEVER_BUY  # noqa: E402
from src.tasks.training.places.shop import ShopPlace  # noqa: E402

TSV = os.path.join(ROOT, "logs", "captures", "codex", "codex_items.tsv")
OUT_JSON = os.path.join(ROOT, "src", "tasks", "training", "strategy",
                        "codex_items.json")
OUT_MD = os.path.join(ROOT, "docs", "codex_items.md")

CATEGORY_ORDER = ("cash", "milk", "stamina", "textbook",
                  "equipment_boost", "attribute", "never", "unknown")
LAYER_ORDER = ("must", "style", "color", "never")
LAYER_DOC = {
    "must": "现金 / 牛奶 / 精力，不看品质，最先看",
    "style": "本流派优先；同层按品质",
    "color": "其余属性商品：品质 → 需求 > 推荐 > 其他",
    "never": "硬不买；蓝/白由运行时按颜色挡",
}


def detail_lines(text):
    """TSV 详情 → 真换行；兼容新格式 `\\n` 转义和老格式 ` | ` 拼接。"""
    text = (text or "").replace(" | ", "\n").replace("\\n", "\n")
    return "\n".join(p.strip() for p in text.split("\n") if p.strip())


def clean(text):
    """去掉换行和所有空白，修复 OCR 把词拆行。"""
    return re.sub(r"\s+", "", (text or "").replace("|", ""))


def base_value(text):
    """详情里的「基础价值：N」；兼容 OCR 的 基础价i：N。"""
    hits = re.findall(r"基础价[^0-9]{0,6}(\d+)", text)
    return int(hits[-1]) if hits else None


def attr_kind(place, name, cleaned):
    """属性商品的颜色内子档：all / preferred / specific / weird / random。"""
    if attributes.is_random_effect(name):
        return "random"
    target = place.shop_attr_target(name)
    if target is None:
        target = attributes.effect_attr_target(cleaned)
    if "奇妙" in name:
        return "weird"
    if target == "ALL":
        return "all"
    if target == "PREFERRED" or "专属" in name:
        return "preferred"
    if target:
        return "specific"
    return "other"


def layer_of(place, name, category, cleaned):
    """返回 (layer, styles, attr_kind, never_reason)。"""
    if category in ("cash", "milk", "stamina"):
        return "must", (), "", ""
    if category in ("textbook", "equipment_boost"):
        return "style", ("训练流", "商店流"), "", ""
    if category == "never":
        if "神之庇佑" in name or "同心护符" in name:
            return "style", ("训练流",), "", ""
        return "never", (), "", "negative/random"
    if category == "attribute":
        return "color", (), attr_kind(place, name, cleaned), ""
    return "never", (), "", "unknown"


def build():
    place = ShopPlace()
    items = []
    with io.open(TSV, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 4:
                continue
            page, cell, name, detail = parts
            detail = detail_lines(detail)
            if any(mark in name for mark in ("继续训练有机会", "解锁更多")):
                continue
            cleaned = clean(detail)
            never_by_name = any(w in name for w in SHOP_NEVER_BUY)
            detail_cat = place.parse_shop_detail(cleaned)
            name_cat = place.shop_category(name)
            known_name = place.shop_known(name)
            known_exact = name in SHOP_ITEMS

            if never_by_name:
                category, source = "never", "never_name"
            elif detail_cat:
                category, source = detail_cat, "detail"
            elif known_name:
                category, source = name_cat, "name"
            else:
                category, source = "unknown", "default"

            layer, styles, akind, reason = layer_of(
                place, name, category, cleaned)
            items.append({
                "page": page,
                "cell": cell,
                "name": name,
                "base_name": place.shop_base_name(name),
                "variant": name[len(place.shop_base_name(name)):],
                "category": category,
                "category_source": source,
                "layer": layer,
                "styles": list(styles),
                "attr_kind": akind,
                "never_reason": reason,
                "known_exact": known_exact,
                "known_name": known_name,
                "base_value": base_value(cleaned),
                "detail": detail,
            })
    return items


def write_json(items):
    data = {
        "schema_version": 1,
        "source": "logs/captures/codex/codex_items.tsv",
        "layer_doc": LAYER_DOC,
        "items": items,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_md(items):
    layer_count = {x: sum(1 for i in items if i["layer"] == x)
                   for x in LAYER_ORDER}
    unknown = [i for i in items if i["category"] == "unknown"]
    name_gap = [i for i in items if not i["known_name"]]

    lines = [
        "# 商品图鉴分层",
        "",
        "来源：`logs/captures/codex/codex_items.tsv`（%d 条）。" % len(items),
        "由 `tools/codex_items_build.py` 生成；改 TSV 后重跑。",
        "颜色（彩/金/紫/蓝/白）不在 TSV 里，运行时读卡底色；本表只分层。",
        "",
        "## 分层汇总",
        "",
        "| 层 | 数量 | 说明 |",
        "|---|---|---|",
    ]
    for x in LAYER_ORDER:
        lines.append("| %s | %d | %s |" % (x, layer_count[x], LAYER_DOC[x]))
    lines += ["", "## 待确认", ""]
    if unknown:
        lines.append("**归不进任何类别（unknown）**：")
        for i in unknown:
            lines.append("- `%s`：%s" % (i["name"], i["detail"]))
    else:
        lines.append("- unknown：0")
    if name_gap:
        lines.append("")
        lines.append("**不在名字/精确表里（靠详情效果归类）**：")
        for i in name_gap:
            lines.append("- `%s` → %s（%s）" % (
                i["name"], i["category"], i["layer"]))
    else:
        lines.append("- 名字/精确表缺口：0")

    for layer in LAYER_ORDER:
        rows = [i for i in items if i["layer"] == layer]
        if not rows:
            continue
        lines += ["", "## %s（%d）" % (layer, len(rows)), ""]
        if layer == "must":
            head = "| 名字 | 类别 | 基础价值 | 详情 |"
            sep = "|---|---|---|---|"
        elif layer == "style":
            head = "| 名字 | 类别 | 流派 | 基础价值 | 详情 |"
            sep = "|---|---|---|---|---|"
        elif layer == "color":
            head = "| 名字 | 属性档 | 基础价值 | 详情 |"
            sep = "|---|---|---|---|"
        else:
            head = "| 名字 | 原因 | 基础价值 | 详情 |"
            sep = "|---|---|---|---|"
        lines += [head, sep]
        for i in rows:
            detail = i["detail"].replace("\n", "<br>").replace("|", "/")
            if layer == "must":
                cells = [i["name"], i["category"],
                         "" if i["base_value"] is None else i["base_value"],
                         detail]
            elif layer == "style":
                cells = [i["name"], i["category"], "、".join(i["styles"]),
                         "" if i["base_value"] is None else i["base_value"],
                         detail]
            elif layer == "color":
                cells = [i["name"], i["attr_kind"],
                         "" if i["base_value"] is None else i["base_value"],
                         detail]
            else:
                cells = [i["name"], i["never_reason"],
                         "" if i["base_value"] is None else i["base_value"],
                         detail]
            lines.append("| " + " | ".join(str(x) for x in cells) + " |")

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def main():
    items = build()
    write_json(items)
    write_md(items)
    layer_count = {x: sum(1 for i in items if i["layer"] == x)
                   for x in LAYER_ORDER}
    style_count = {}
    for i in items:
        for s in i["styles"]:
            style_count[s] = style_count.get(s, 0) + 1
    unknown = [i["name"] for i in items if i["category"] == "unknown"]
    print("items", len(items))
    print("layers", layer_count)
    print("styles", style_count)
    print("unknown", len(unknown), unknown)
    print("name_gap", [i["name"] for i in items if not i["known_name"]])
    print("json", OUT_JSON)
    print("md", OUT_MD)
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
