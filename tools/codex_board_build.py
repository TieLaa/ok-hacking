"""生成图鉴分层工作台（单文件 HTML，可离线打开、拖动、备注、导出）。

输入：
  src/tasks/training/strategy/codex_items.json   （商品 85 条）
  logs/captures/codex/codex_items_gacha.tsv      （扭蛋 74 条）

输出：
  docs/codex_board.html

用法：
  .venv\\Scripts\\python.exe tools\\codex_board_build.py
"""
import io
import json
import os
import re


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOP_JSON = os.path.join(ROOT, "src", "tasks", "training", "strategy",
                         "codex_items.json")
GACHA_TSV = os.path.join(ROOT, "logs", "captures", "codex",
                        "codex_items_gacha.tsv")
OUT = os.path.join(ROOT, "docs", "codex_board.html")


def detail_lines(text):
    """详情字符串 → 真换行；兼容 `\\n` 转义、` | ` 拼接和已有换行。"""
    text = (text or "").replace(" | ", "\n").replace("\\n", "\n")
    return "\n".join(p.strip() for p in text.split("\n") if p.strip())


def base_value(text):
    hits = re.findall(r"基础价[^0-9]{0,6}(\d+)", text or "")
    return int(hits[-1]) if hits else None


def shop_items():
    data = json.load(io.open(SHOP_JSON, encoding="utf-8"))
    out = []
    for i in data["items"]:
        layer = i["layer"]
        cat = i.get("category", "")
        if layer == "must":
            if cat == "cash":
                layer = "essential"
            elif cat in ("milk", "stamina"):
                layer = "conditional"
            else:
                layer = "unassigned"
        styles = list(i.get("styles") or []) if layer == "style" else []
        if layer == "style" and not styles:
            layer = "unassigned"
        attr = (i.get("attr_kind") or "") if layer == "color" else ""
        if layer == "color" and not attr:
            attr = "specific"
        out.append({
            "id": "shop:" + i["name"],
            "name": i["name"],
            "source": "shop",
            "category": cat,
            "detail": detail_lines(i.get("detail", "")),
            "baseValue": i.get("base_value"),
            "layer": layer,
            "styles": styles,
            "attrKind": attr,
            "note": "",
        })
    return out


def gacha_draft(name):
    if name in ("笔记继承", "球员位置"):
        return "essential", [], ""
    if any(k in name for k in ("精力恢复", "精力补充", "精力转换", "专注", "清空")):
        return "conditional", [], ""
    if any(k in name for k in ("器材暴击", "器材熟练度", "训练增强",
                               "熟练度暴涨", "必定成功", "教科书", "升级包")):
        return "style", ["训练流"], ""
    if "小游戏" in name:
        return "style", ["小游戏流"], ""
    if "额外零用钱" in name:
        return "style", ["商店流", "扭蛋流", "小游戏流"], ""
    if "全属性" in name or "延迟满足" in name:
        return "color", [], "all"
    if "推荐属性" in name:
        return "color", [], "preferred"
    if "随机" in name:
        return "color", [], "random"
    if "奇妙" in name:
        return "color", [], "weird"
    if any(k in name for k in ("增加", "暴涨", "猛增", "意识")):
        return "color", [], "specific"
    return "unassigned", [], ""


def gacha_items():
    out = []
    if not os.path.isfile(GACHA_TSV):
        return out
    for line in io.open(GACHA_TSV, encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 4:
            continue
        name, detail = parts[2], parts[3]
        if any(k in name for k in ("继续训练有机会", "解锁更多")):
            continue
        layer, styles, attr = gacha_draft(name)
        out.append({
            "id": "gacha:" + name,
            "name": name,
            "source": "gacha",
            "category": "gacha",
            "detail": detail_lines(detail),
            "baseValue": base_value(detail),
            "layer": layer,
            "styles": styles,
            "attrKind": attr,
            "note": "",
        })
    # 图鉴只点亮了 3 张「专注」（投射/体能/运控）；六维推论共 6 张
    # （用户 2026-11-24 口径）。标 inferred，导出时能单独区分，不冒充实测。
    have = {i["name"] for i in out}
    inferred = []
    for subject in ("弹跳", "防守", "意识"):
        name = subject + "专注"
        if name in have:
            continue
        inferred.append({
            "id": "gacha:" + name,
            "name": name,
            "source": "gacha",
            "category": "inferred",
            # 按同族 3 张已点亮的卡推的：训练主力 / 消耗所有精力 /
            # 获得<科目>+48 / 队友属性中幅提升 / 基础价值 60。
            "detail": "\n".join((name, "训练主力", "消耗所有精力，",
                                 "获得%s+48" % subject,
                                 "队友属性中幅提升", "基础价值：60")),
            "baseValue": 60,
            "layer": "conditional",
            "styles": [],
            "attrKind": "",
            "note": "",
            "inferred": True,
        })
    idx = next((n for n, i in enumerate(out) if "专注" in i["name"]), len(out) - 1)
    out[idx + 1:idx + 1] = inferred

    # 精力转换-XX：图鉴只点亮 3 张（体能/运控/意识），按六维补另外 3 张。
    # 效果同族：消耗所有精力，<科目>训练等级+2，队友属性中幅提升，基础价值 60。
    have.update(i["name"] for i in inferred)
    conv = []
    for subject in ("投射", "弹跳", "防守"):
        name = "精力转换-" + subject
        if name in have:
            continue
        conv.append({
            "id": "gacha:" + name,
            "name": name,
            "source": "gacha",
            "category": "inferred",
            "detail": "\n".join((name, "消耗所有精力，",
                                 "%s训练等级+2" % subject,
                                 "队友属性中幅提升", "基础价值：60")),
            "baseValue": 60,
            "layer": "conditional",
            "styles": [],
            "attrKind": "",
            "note": "",
            "inferred": True,
        })
    idx = next((n for n, i in enumerate(out)
                if i["name"].startswith("精力转换")), len(out) - 1)
    out[idx + 1:idx + 1] = conv

    # 升级包基础款：图鉴只点亮了「运控升级包」，其余五科按同族补齐。
    # 效果：<科目>属性+20，<科目>训练等级+1，队友属性小幅提升，基础价值 60。
    have.update(i["name"] for i in conv)
    bases = []
    for subject in ("投射", "弹跳", "防守", "体能", "意识"):
        name = subject + "升级包"
        if name in have:
            continue
        bases.append({
            "id": "gacha:" + name,
            "name": name,
            "source": "gacha",
            "category": "inferred",
            "detail": "\n".join((name, "%s属性+20，" % subject,
                                 "%s训练等级+1" % subject,
                                 "队友属性小幅提升", "基础价值：60")),
            "baseValue": 60,
            "layer": "style",
            "styles": ["训练流"],
            "attrKind": "",
            "note": "",
            "inferred": True,
        })
    idx = next((n for n, i in enumerate(out)
                if "升级包" in i["name"]), len(out) - 1)
    out[idx + 1:idx + 1] = bases

    # XX暴涨 / XX猛增：图鉴各点亮 2 张（防守/意识），按六维补其余 4 张。
    # 暴涨 = 获得 250<科目>，主力永久不能再从训练获得该科目（负面）；
    # 猛增 = 获得 100<科目>，接下来一周不能从训练获得该科目（负面更轻）。
    subjects = ("投射", "弹跳", "防守", "体能", "运控", "意识")
    have.update(i["name"] for i in bases)
    for suffix, gain, seal, anchor in (
            ("暴涨", 250, "主力不再能从训练中获得%s。", "暴涨"),
            ("猛增", 100, "接下来的一周，不再能从训练中获得%s。", "猛增")):
        extra = []
        for subject in ("投射", "弹跳", "运控", "体能"):
            name = subject + suffix
            if name in have:
                continue
            extra.append({
                "id": "gacha:" + name,
                "name": name,
                "source": "gacha",
                "category": "inferred",
                "detail": "\n".join((name, "获得%d%s，" % (gain, subject),
                                     seal % subject,
                                     "队友属性大幅提升", "基础价值：100")),
                "baseValue": 100,
                "layer": "color",
                "styles": [],
                "attrKind": "specific",
                "note": "",
                "inferred": True,
            })
        anchor_names = {s + anchor for s in subjects}
        idx = next((n for n, i in enumerate(out)
                    if i["name"] in anchor_names), len(out) - 1)
        out[idx + 1:idx + 1] = extra
    return out


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>图鉴分层台 | Codex Sorting Desk</title>
<style>
:root{
  --paper:#f3eee3; --paper-2:#ece5d7; --card:#fffaf0; --ink:#211d18;
  --muted:#6b6256; --line:#d7ccba; --line-strong:#b9ab94;
  --essential:#b6402c; --conditional:#2c746a; --style:#4d5f9d;
  --color:#a0691f; --never:#5a5046; --unknown:#7a4a8c; --unassigned:#8b8172;
  --focus:#0b57d0; --shadow:0 1px 0 rgba(33,29,24,.12);
  --radius:10px;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
html{scroll-behavior:smooth}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:"Segoe UI","Microsoft YaHei",sans-serif;
  font-size:15px; line-height:1.5;
}
body::before{
  content:""; position:fixed; inset:0; pointer-events:none; z-index:0;
  background-image:
    linear-gradient(rgba(33,29,24,.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(33,29,24,.035) 1px, transparent 1px);
  background-size:46px 46px;
}
.visually-hidden:where(:not(:focus-within,:active)){
  position:absolute!important; clip-path:inset(50%)!important;
  overflow:hidden!important; width:1px!important; height:1px!important;
  margin:-1px!important; padding:0!important; border:0!important;
  white-space:nowrap!important;
}
.skip{position:absolute; left:16px; top:-60px; z-index:20; background:var(--ink);
  color:var(--paper); padding:10px 14px; border-radius:6px; text-decoration:none}
.skip:focus{top:12px}
a{color:inherit}
button,select,textarea,input{font:inherit; color:inherit}
button{cursor:pointer}
:where(button,select,textarea,input,a):focus-visible{
  outline:3px solid var(--focus); outline-offset:3px;
}
.shell{position:relative; z-index:1; max-width:1680px; margin:0 auto; padding:0 28px 60px}
.top{
  display:flex; gap:32px; align-items:flex-end; justify-content:space-between;
  padding:34px 0 22px; border-bottom:2px solid var(--ink);
}
.eyebrow,.mono{font-family:"Cascadia Mono","Consolas","Courier New",monospace;
  text-transform:uppercase; letter-spacing:.12em; font-size:11px}
.eyebrow{color:var(--essential); margin:0 0 8px}
h1{
  font-family:Georgia,"Songti SC","SimSun",serif; font-weight:700;
  font-size:clamp(2.3rem,5.4vw,5.2rem); line-height:.94; letter-spacing:-.035em;
  margin:0;
}
.lede{margin:12px 0 0; max-width:56ch; color:var(--muted); font-size:14px}
.stats{display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; max-width:520px}
.stat{
  display:grid; gap:2px; min-width:74px; padding:8px 10px; border:1px solid var(--line);
  background:rgba(255,250,240,.58); border-radius:8px;
}
.stat b{font-family:"Cascadia Mono",monospace; font-size:18px; line-height:1}
.stat span{font-size:10px; color:var(--muted); letter-spacing:.08em; text-transform:uppercase}
.stat.essential{border-top:3px solid var(--essential)}
.stat.conditional{border-top:3px solid var(--conditional)}
.stat.style{border-top:3px solid var(--style)}
.stat.color{border-top:3px solid var(--color)}
.stat.never{border-top:3px solid var(--never)}
.stat.unknown{border-top:3px solid var(--unknown)}
.stat.unassigned{border-top:3px solid var(--unassigned)}
.toolbar{
  position:sticky; top:0; z-index:10; display:flex; flex-wrap:wrap; gap:12px;
  align-items:center; padding:14px 0; margin-bottom:18px;
  background:color-mix(in srgb, var(--paper) 92%, transparent);
  backdrop-filter:blur(8px); border-bottom:1px solid var(--line-strong);
}
search{display:flex; align-items:center; gap:8px; flex:1 1 280px}
search input{
  width:100%; min-height:40px; padding:8px 12px; background:var(--card);
  border:1px solid var(--line-strong); border-radius:7px;
}
.filters{display:flex; gap:10px; align-items:center; flex-wrap:wrap}
.filters label{display:flex; gap:6px; align-items:center; color:var(--muted); font-size:13px}
.filters select{min-height:40px; padding:6px 10px; background:var(--card);
  border:1px solid var(--line-strong); border-radius:7px}
.actions{display:flex; gap:8px; flex-wrap:wrap; margin-inline-start:auto}
.view-toggle{display:flex; border:1px solid var(--line-strong); border-radius:7px; overflow:hidden}
.view-toggle button{border:0; border-radius:0; background:transparent; color:var(--ink); min-height:40px; padding:8px 12px; font-size:13px}
.view-toggle button[aria-pressed="true"]{background:var(--ink); color:var(--paper)}
.family-list{display:grid; gap:10px; margin:4px 0 30px}
.family{
  background:rgba(255,250,240,.55); border:1px solid var(--line);
  border-left:4px solid var(--line-strong); border-radius:8px; padding:10px 12px;
}
.family[data-layer="essential"]{border-left-color:var(--essential)}
.family[data-layer="conditional"]{border-left-color:var(--conditional)}
.family[data-layer="style"]{border-left-color:var(--style)}
.family[data-layer="color"]{border-left-color:var(--color)}
.family[data-layer="never"]{border-left-color:var(--never)}
.family[data-layer="unknown"]{border-left-color:var(--unknown)}
.family[data-layer="unassigned"]{border-left-color:var(--unassigned); border-style:dashed; border-left-style:solid}
.family-head{display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; cursor:pointer}
.family-head > *{cursor:auto}
.family-head .fold{flex:0 0 auto; cursor:pointer}
.family-head h3{margin:0; font-family:Georgia,"Songti SC","SimSun",serif; font-size:19px; line-height:1.2}
.family-head .fold{
  flex:0 0 auto; width:auto; height:26px; padding:0 8px; border:1px solid var(--line);
  background:transparent; border-radius:6px; color:var(--muted); line-height:1;
  font-size:11px;
}
.family-head .fold:hover{border-color:var(--ink); color:var(--ink)}
.family.collapsed{padding-bottom:10px}
.family-count,.family-cover,.family-state{
  font-family:"Cascadia Mono",monospace; font-size:11px; color:var(--muted);
}
.family-cover.miss{color:var(--color)}
.family-state{margin-inline-start:auto}
.family-members{display:flex; flex-wrap:wrap; gap:4px; margin:7px 0 2px}
.chip{
  font-size:11px; border:1px solid var(--line); border-radius:999px;
  padding:1px 7px; background:var(--card); color:var(--muted); cursor:pointer;
}
.chip:hover{border-color:var(--ink); color:var(--ink)}
.family-controls{display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:7px}
.family-controls select{
  min-height:32px; padding:2px 8px; font-size:12px; background:var(--card);
  border:1px solid var(--line-strong); border-radius:6px;
}
.family-controls .checkline{font-size:11px; gap:8px; padding-top:0}
.family-controls .checkline label{font-size:12px}
.family-note{flex:1 1 300px; min-width:200px}
.family-note textarea{
  width:100%; min-height:34px; padding:5px 8px; font-size:12px; resize:vertical;
  background:color-mix(in srgb,var(--card) 72%, transparent);
  border:1px dashed var(--line-strong); border-radius:6px;
}
.family-note textarea:focus{border-style:solid; border-color:var(--ink); background:var(--card)}
.btn{
  min-height:40px; padding:8px 13px; border:1px solid var(--ink);
  background:var(--ink); color:var(--paper); border-radius:7px; font-size:13px;
}
.btn.ghost{background:transparent; color:var(--ink); border-color:var(--line-strong)}
.btn:hover{transform:translateY(-1px)}
.layer{
  --accent:var(--unassigned);
  margin:18px 0 26px; padding:0 0 18px; border-bottom:1px solid var(--line-strong);
}
.layer-head{display:flex; gap:14px; align-items:baseline; flex-wrap:wrap}
.layer-head .fold{margin-inline-start:auto; align-self:center}
.layer.collapsed > .cards,.layer.collapsed > .subgrid{display:none}
.fold{
  min-height:28px; padding:0 9px; border:1px solid var(--line-strong);
  background:transparent; border-radius:6px; color:var(--muted); font-size:11px;
  font-family:"Cascadia Mono",monospace;
}
.fold:hover{border-color:var(--ink); color:var(--ink)}
.layer-num{font-family:"Cascadia Mono",monospace; color:var(--accent); font-size:13px}
.layer h2{
  font-family:Georgia,"Songti SC","SimSun",serif; font-size:clamp(1.5rem,2.4vw,2.2rem);
  margin:0; letter-spacing:-.02em;
}
.layer .count{
  font-family:"Cascadia Mono",monospace; font-size:12px; color:var(--accent);
  border:1px solid currentColor; border-radius:999px; padding:1px 9px;
}
.layer .hint{margin:0; color:var(--muted); font-size:13px; flex-basis:100%}
.unassigned{--accent:var(--unassigned); border:1px dashed var(--line-strong);
  border-radius:var(--radius); padding:14px; background:rgba(255,250,240,.35)}
.essential{--accent:var(--essential)}
.conditional{--accent:var(--conditional)}
.style{--accent:var(--style)}
.color{--accent:var(--color)}
.never{--accent:var(--never)}
.unknown{--accent:var(--unknown)}
.cards{
  display:grid; grid-template-columns:repeat(auto-fill,minmax(232px,1fr));
  gap:10px; margin-top:12px; min-height:54px; border-radius:8px;
  transition:background .15s, box-shadow .15s;
}
.cards.over,.subbucket.over{background:color-mix(in srgb,var(--accent) 11%,transparent);
  box-shadow:inset 0 0 0 2px var(--accent)}
.subgrid{
  display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr));
  gap:12px; margin-top:12px;
}
.subbucket{
  border:1px solid var(--line); border-radius:8px; padding:10px;
  background:rgba(255,250,240,.38); min-height:120px;
  transition:background .15s, box-shadow .15s;
}
.subbucket h3{
  margin:0 0 6px; font-size:13px; letter-spacing:.06em; text-transform:uppercase;
  font-family:"Cascadia Mono",monospace; color:var(--muted);
  display:flex; justify-content:space-between; gap:8px;
}
.subbucket h3 span{color:var(--accent)}
.subbucket .cards{grid-template-columns:repeat(auto-fill,minmax(206px,1fr)); margin-top:7px}
.card{
  position:relative; display:grid; gap:7px; align-content:start;
  background:var(--card); border:1px solid var(--line); border-left:4px solid var(--accent);
  border-radius:8px; padding:9px 10px 10px; box-shadow:var(--shadow);
  transition:transform .14s, box-shadow .14s, opacity .14s;
}
.card:hover{transform:translateY(-2px); box-shadow:0 8px 18px rgba(33,29,24,.10)}
.card.dragging{opacity:.45}
.card[data-layer="essential"]{--accent:var(--essential)}
.card[data-layer="conditional"]{--accent:var(--conditional)}
.card[data-layer="style"]{--accent:var(--style)}
.card[data-layer="color"]{--accent:var(--color)}
.card[data-layer="never"]{--accent:var(--never)}
.card[data-layer="unknown"]{--accent:var(--unknown)}
.card.inferred{border-style:dashed; border-left-style:solid; background:color-mix(in srgb,var(--card) 80%, transparent)}
.badge.inferred{border-color:var(--unknown); color:var(--unknown)}
.chip.inferred{border-style:dashed; color:var(--unknown)}
.card[data-layer="unassigned"]{--accent:var(--unassigned)}
.card-top{display:flex; gap:6px; align-items:center; min-width:0}
.card-top .check{
  flex:0 0 26px; width:26px; height:26px; padding:0; border:1px solid var(--line-strong);
  background:transparent; border-radius:6px; color:transparent; line-height:1;
  font-size:12px;
}
.card-top .check[aria-pressed="true"]{background:var(--conditional); border-color:var(--conditional); color:#fff}
.card.reviewed .card-title{color:var(--muted)}
.family-check[aria-pressed="true"]{background:var(--conditional); border-color:var(--conditional); color:#fff}
.grab{
  flex:0 0 30px; width:30px; height:30px; border:0; border-radius:6px;
  background:transparent; color:var(--muted); font-size:18px; line-height:1;
  cursor:grab; touch-action:none; -webkit-user-select:none; user-select:none;
}
.grab:hover{background:var(--paper-2); color:var(--ink)}
.card.dragging .grab{cursor:grabbing}
.card-title{
  flex:1 1 auto; min-width:0; padding:2px 0; border:0; background:none;
  text-align:left; font-family:Georgia,"Songti SC","SimSun",serif;
  font-size:16px; font-weight:700; line-height:1.25; overflow-wrap:anywhere;
}
.card-title:hover{text-decoration:underline; text-underline-offset:3px}
.remove-style{
  flex:0 0 26px; width:26px; height:26px; border:1px solid var(--line);
  background:transparent; color:var(--muted); border-radius:50%; line-height:1;
}
.remove-style:hover{color:var(--essential); border-color:var(--essential)}
.badges{display:flex; flex-wrap:wrap; gap:5px; align-items:center}
.badge{
  font-family:"Cascadia Mono",monospace; font-size:10px; letter-spacing:.05em;
  border:1px solid var(--line-strong); border-radius:999px; padding:1px 7px;
  color:var(--muted); white-space:nowrap;
}
.badge.shop{border-color:#9b6b2b; color:#7b4f16}
.badge.gacha{border-color:#4d5f9d; color:#3d4e88}
.badge.note{border-color:var(--essential); color:var(--essential)}
.badge.shared{border-color:var(--style); color:var(--style)}
.card-fx{
  margin:0; color:var(--muted); font-size:12px; line-height:1.45;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
  overflow:clip; overflow-wrap:anywhere;
}
.empty{margin:6px 0 0; color:var(--muted); font-size:12px; font-style:italic}
.bucket-note{margin:10px 0 0}
.subbucket .bucket-note{margin-top:6px}
.bucket-note textarea{
  width:100%; min-height:42px; padding:6px 9px; resize:vertical;
  font-size:12.5px; line-height:1.55; background:color-mix(in srgb,var(--card) 72%, transparent);
  border:1px dashed var(--line-strong); border-radius:6px;
}
.bucket-note textarea:hover{border-color:var(--muted)}
.bucket-note textarea:focus{border-style:solid; border-color:var(--ink); background:var(--card)}
.bucket-note textarea::placeholder{color:var(--muted); opacity:.8}
.dialog{
  width:min(760px,94vw); max-height:88dvh; padding:0; border:1px solid var(--ink);
  border-radius:12px; background:var(--paper); color:var(--ink);
  box-shadow:0 30px 80px rgba(33,29,24,.35);
}
.dialog:modal{overscroll-behavior:contain}
.dialog::backdrop{background:rgba(33,29,24,.55); overscroll-behavior:contain}
.dialog-inner{padding:22px 24px 26px; overflow:auto; max-height:88dvh}
.dialog-head{display:flex; gap:14px; align-items:flex-start; justify-content:space-between}
.dialog-kicker{margin:0; color:var(--essential); font-family:"Cascadia Mono",monospace;
  font-size:10px; letter-spacing:.14em; text-transform:uppercase}
.dialog h2{margin:4px 0 0; font-family:Georgia,"Songti SC","SimSun",serif;
  font-size:clamp(1.5rem,3vw,2.3rem); line-height:1.15}
.detail-meta{margin:4px 0 16px; color:var(--muted); font-size:12px;
  font-family:"Cascadia Mono",monospace}
.detail-text{
  margin:0 0 18px; padding:12px 14px; background:var(--card);
  border:1px solid var(--line); border-radius:8px; white-space:pre-wrap;
  font-size:13px; line-height:1.65; max-height:230px; overflow:auto;
}
.form-grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px}
.field{display:grid; gap:5px}
.field label,.field legend{font-size:12px; color:var(--muted); letter-spacing:.05em}
.field select,.field textarea{
  width:100%; min-height:42px; padding:8px 10px; background:var(--card);
  border:1px solid var(--line-strong); border-radius:7px;
}
fieldset{border:0; padding:0; margin:0}
.checkline{display:flex; flex-wrap:wrap; gap:10px; padding-top:4px}
.checkline label{display:flex; gap:5px; align-items:center; font-size:13px; color:var(--ink)}
.note-field{display:grid; gap:6px; margin-top:16px}
.note-field textarea{
  min-height:120px; resize:vertical; line-height:1.7;
  background:repeating-linear-gradient(var(--card),var(--card) 27px,var(--line) 28px);
}
.save-status{margin:8px 0 0; color:var(--muted); font-size:11px; min-height:1.2em}
.close{min-height:34px; padding:6px 11px; border:1px solid var(--line-strong);
  background:transparent; border-radius:7px; font-size:12px}
.dialog-actions{display:flex; gap:6px; align-items:flex-start; flex-wrap:wrap}
.footer{margin-top:34px; padding-top:14px; border-top:1px solid var(--line-strong);
  color:var(--muted); font-size:12px; display:flex; justify-content:space-between; gap:14px; flex-wrap:wrap}
@media (max-width:900px){
  .shell{padding-inline:16px}
  .top{align-items:flex-start; flex-direction:column; gap:18px}
  .stats{justify-content:flex-start}
  .toolbar{align-items:stretch}
  .actions{margin-inline-start:0}
  .subgrid{grid-template-columns:1fr}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{scroll-behavior:auto!important; transition:none!important; animation:none!important}
  .card:hover{transform:none}
}
</style>
</head>
<body>
<a class="skip" href="#board">跳到分类板</a>
<div class="shell">
  <header class="top">
    <div>
      <p class="eyebrow">Codex Sorting Desk · draft</p>
      <h1>图鉴分层台</h1>
      <p class="lede">商品 85 条、扭蛋 74 条。拖动每张卡左上角的把手改层；点名字打开详情和备注。草稿会保存在本机浏览器，也可以导出 JSON。</p>
    </div>
    <div class="stats" id="stats" aria-label="分类统计"></div>
  </header>

  <main id="board">
    <div class="toolbar">
      <search>
        <label class="visually-hidden" for="q">搜索名字或效果</label>
        <input id="q" type="search" placeholder="搜索名字 / 效果…" autocomplete="off" spellcheck="false">
      </search>
      <div class="filters">
        <label>来源
          <select id="source-filter">
            <option value="all">全部</option>
            <option value="shop">商品</option>
            <option value="gacha">扭蛋</option>
          </select>
        </label>
        <label><input id="notes-only" type="checkbox"> 只看有备注</label>
      </div>
      <div class="view-toggle" role="group" aria-label="视图">
        <button type="button" data-view="board" aria-pressed="true">分类板</button>
        <button type="button" data-view="family" aria-pressed="false">按族</button>
      </div>
      <div class="actions">
        <button class="btn ghost" id="open-state-btn" type="button">打开存档</button>
        <button class="btn ghost" id="save-as-btn" type="button">新建存档</button>
        <button class="btn ghost" id="export-btn" type="button">导出 JSON</button>
        <button class="btn ghost" id="import-btn" type="button">导入 JSON</button>
        <button class="btn ghost" id="reset-btn" type="button">重置草稿</button>
        <input id="import-file" type="file" accept="application/json" hidden>
      </div>
    </div>

    <div id="family-view" hidden></div>

    <section class="layer unassigned" data-drop="unassigned" aria-labelledby="h-unassigned">
      <div class="layer-head">
        <span class="layer-num">00</span>
        <h2 id="h-unassigned">待分类</h2>
        <span class="count" data-count="unassigned">0</span>
        <p class="hint">草稿里还没归层的条目；拖到下面任一层。</p>
      </div>
      <div class="cards" data-cards="unassigned"></div>
    </section>

    <section class="layer essential" data-drop="essential" aria-labelledby="h-essential">
      <div class="layer-head">
        <span class="layer-num">01</span>
        <h2 id="h-essential">essential · 刚需</h2>
        <span class="count" data-count="essential">0</span>
        <p class="hint">无条件优先，不看品质；只保留资格/余额门槛。</p>
      </div>
      <div class="cards" data-cards="essential"></div>
    </section>

    <section class="layer conditional" data-drop="conditional" aria-labelledby="h-conditional">
      <div class="layer-head">
        <span class="layer-num">02</span>
        <h2 id="h-conditional">conditional · 有条件优先</h2>
        <span class="count" data-count="conditional">0</span>
        <p class="hint">牛奶、精力、能量卡；过门槛才进候选。</p>
      </div>
      <div class="cards" data-cards="conditional"></div>
    </section>

    <section class="layer style" data-drop="style" aria-labelledby="h-style">
      <div class="layer-head">
        <span class="layer-num">03</span>
        <h2 id="h-style">style · 流派优先</h2>
        <span class="count" data-count="style">0</span>
        <p class="hint">先按当前流派分桶，再按品质；同一张卡可以进多个桶。</p>
      </div>
      <div class="subgrid">
        <div class="subbucket" data-drop="style" data-style="训练流" aria-labelledby="h-style-training">
          <h3 id="h-style-training">训练流 <span data-count="style:训练流">0</span></h3>
          <div class="cards" data-cards="style:训练流"></div>
        </div>
        <div class="subbucket" data-drop="style" data-style="商店流" aria-labelledby="h-style-shop">
          <h3 id="h-style-shop">商店流 <span data-count="style:商店流">0</span></h3>
          <div class="cards" data-cards="style:商店流"></div>
        </div>
        <div class="subbucket" data-drop="style" data-style="扭蛋流" aria-labelledby="h-style-gacha">
          <h3 id="h-style-gacha">扭蛋流 <span data-count="style:扭蛋流">0</span></h3>
          <div class="cards" data-cards="style:扭蛋流"></div>
        </div>
        <div class="subbucket" data-drop="style" data-style="小游戏流" aria-labelledby="h-style-mini">
          <h3 id="h-style-mini">小游戏流 <span data-count="style:小游戏流">0</span></h3>
          <div class="cards" data-cards="style:小游戏流"></div>
        </div>
      </div>
    </section>

    <section class="layer color" data-drop="color" aria-labelledby="h-color">
      <div class="layer-head">
        <span class="layer-num">04</span>
        <h2 id="h-color">color · 颜色层</h2>
        <span class="count" data-count="color">0</span>
        <p class="hint">先品质（彩→金→紫→其他），同品质再按属性档。</p>
      </div>
      <div class="subgrid">
        <div class="subbucket" data-drop="color" data-attr="all" aria-labelledby="h-color-all">
          <h3 id="h-color-all">全属性 <span data-count="color:all">0</span></h3>
          <div class="cards" data-cards="color:all"></div>
        </div>
        <div class="subbucket" data-drop="color" data-attr="preferred" aria-labelledby="h-color-preferred">
          <h3 id="h-color-preferred">推荐 / 专属 <span data-count="color:preferred">0</span></h3>
          <div class="cards" data-cards="color:preferred"></div>
        </div>
        <div class="subbucket" data-drop="color" data-attr="specific" aria-labelledby="h-color-specific">
          <h3 id="h-color-specific">单科 <span data-count="color:specific">0</span></h3>
          <div class="cards" data-cards="color:specific"></div>
        </div>
        <div class="subbucket" data-drop="color" data-attr="weird" aria-labelledby="h-color-weird">
          <h3 id="h-color-weird">奇妙 <span data-count="color:weird">0</span></h3>
          <div class="cards" data-cards="color:weird"></div>
        </div>
        <div class="subbucket" data-drop="color" data-attr="random" aria-labelledby="h-color-random">
          <h3 id="h-color-random">偶然 / 随机 <span data-count="color:random">0</span></h3>
          <div class="cards" data-cards="color:random"></div>
        </div>
      </div>
    </section>

    <section class="layer never" data-drop="never" aria-labelledby="h-never">
      <div class="layer-head">
        <span class="layer-num">05</span>
        <h2 id="h-never">never · 不买</h2>
        <span class="count" data-count="never">0</span>
        <p class="hint">负面/随机坑；蓝白品质仍由运行时挡。</p>
      </div>
      <div class="cards" data-cards="never"></div>
    </section>

    <section class="layer unknown" data-drop="unknown" aria-labelledby="h-unknown">
      <div class="layer-head">
        <span class="layer-num">06</span>
        <h2 id="h-unknown">unknown · 没分好</h2>
        <span class="count" data-count="unknown">0</span>
        <p class="hint">看过但拿不准的条目放这里，不算最终分层。</p>
      </div>
      <div class="cards" data-cards="unknown"></div>
    </section>
  </main>

  <footer class="footer">
    <span>草稿不是最终规则；导出 JSON 后交给会话写入 catalog。</span>
    <span class="mono" id="file-status">存档未连接</span>
    <span class="mono" id="saved-at"></span>
  </footer>
</div>

<dialog class="dialog" id="detail" closedby="any" aria-labelledby="detail-title">
  <div class="dialog-inner">
    <div class="dialog-head">
      <div>
        <p class="dialog-kicker">Item</p>
        <h2 id="detail-title"></h2>
      </div>
      <div class="dialog-actions">
        <button class="close" id="delete-inferred" type="button" hidden>删除推断</button>
        <button class="close" id="close-detail" type="button">关闭</button>
      </div>
    </div>
    <p class="detail-meta" id="detail-meta"></p>
    <p class="detail-text" id="detail-text"></p>
    <div class="form-grid">
      <div class="field">
        <label for="f-layer">层</label>
        <select id="f-layer">
          <option value="essential">essential · 刚需</option>
          <option value="conditional">conditional · 有条件优先</option>
          <option value="style">style · 流派优先</option>
          <option value="color">color · 颜色层</option>
          <option value="never">never · 不买</option>
          <option value="unknown">unknown · 没分好</option>
          <option value="unassigned">待分类</option>
        </select>
      </div>
      <fieldset class="field" id="f-styles-wrap">
        <legend>流派桶（可多选）</legend>
        <div class="checkline">
          <label><input type="checkbox" value="训练流"> 训练流</label>
          <label><input type="checkbox" value="商店流"> 商店流</label>
          <label><input type="checkbox" value="扭蛋流"> 扭蛋流</label>
          <label><input type="checkbox" value="小游戏流"> 小游戏流</label>
        </div>
      </fieldset>
      <div class="field" id="f-attr-wrap">
        <label for="f-attr">属性档</label>
        <select id="f-attr">
          <option value="all">全属性</option>
          <option value="preferred">推荐 / 专属</option>
          <option value="specific">单科</option>
          <option value="weird">奇妙</option>
          <option value="random">偶然 / 随机</option>
        </select>
      </div>
    </div>
    <div class="note-field">
      <label for="f-note">备注</label>
      <textarea id="f-note" rows="5" placeholder="为什么放这里、待确认的条件……" autofocus></textarea>
      <p class="save-status" id="save-status" role="status"></p>
    </div>
  </div>
</dialog>

<div id="live" class="visually-hidden" aria-live="polite" aria-atomic="true"></div>
<script>
const INITIAL = __DATA__;
const LS_KEY = "codex-board-v1";
const SOURCE_LABEL = {shop:"商品", gacha:"扭蛋"};
const LAYER_LABEL = {
  essential:"刚需", conditional:"有条件优先", style:"流派优先",
  color:"颜色层", never:"不买", unknown:"没分好", unassigned:"待分类"
};
const ATTR_LABEL = {
  all:"全属性", preferred:"推荐/专属", specific:"单科",
  weird:"奇妙", random:"随机"
};
const state = [];
const bucketNotes = {};
const familyNotes = {};
const NOTE_ZONES = [];
const extraItems = [];
const removedInferred = new Set();
const collapsedFamilies = new Set();
const collapsedLayers = new Set();
const SUBJECTS = ["投射", "弹跳", "防守", "体能", "运控", "意识"];
const STYLE_BUCKETS = ["训练流", "商店流", "扭蛋流", "小游戏流"];
const ATTRS = ["all", "preferred", "specific", "weird", "random"];
let currentView = "board";
const byId = new Map();
let seq = 0;
let dragId = null;
let openId = null;
let saveTimer = null;
const filter = {q:"", source:"all", notes:false};

function storageGet(){
  try{ return JSON.parse(localStorage.getItem(LS_KEY) || "null"); }
  catch(e){ return null; }
}
function storageSet(data){
  try{ localStorage.setItem(LS_KEY, JSON.stringify(data)); return true; }
  catch(e){ return false; }
}
function storageRemove(){
  try{ localStorage.removeItem(LS_KEY); }catch(e){}
}
function stateData(){
  return {
    version:1,
    savedAt:new Date().toISOString(),
    bucketNotes:bucketNotes,
    familyNotes:familyNotes,
    extraItems:extraItems,
    removedInferred:[...removedInferred],
    collapsedFamilies:[...collapsedFamilies],
    collapsedLayers:[...collapsedLayers],
    items:state.map(i => ({id:i.id, layer:i.layer, styles:i.styles,
                           attrKind:i.attrKind, note:i.note,
                           reviewed:!!i.reviewed}))
  };
}
function saveState(){
  const data = stateData();
  const ok = storageSet(data);
  try { window.name = "codex-board:" + JSON.stringify(data); } catch (e) {}
  const el = document.getElementById("save-status");
  if (el) el.textContent = ok ? "备注与分类已保存在本机浏览器。" : "本机存储不可用，请用“导出 JSON”。";
  document.getElementById("saved-at").textContent = ok ? "saved " + new Date().toLocaleTimeString() : "not saved";
  scheduleFileWrite();
}
function hydrate(saved){
  const savedMap = new Map();
  if (saved && Array.isArray(saved.items)) {
    for (const i of saved.items) savedMap.set(i.id, i);
  }
  if (saved && saved.bucketNotes && typeof saved.bucketNotes === "object") {
    Object.assign(bucketNotes, saved.bucketNotes);
  }
  if (saved && saved.familyNotes && typeof saved.familyNotes === "object") {
    Object.assign(familyNotes, saved.familyNotes);
  }
  if (saved && Array.isArray(saved.removedInferred)) {
    for (const id of saved.removedInferred) removedInferred.add(id);
  }
  if (saved && Array.isArray(saved.collapsedFamilies)) {
    for (const key of saved.collapsedFamilies) collapsedFamilies.add(key);
  }
  if (saved && Array.isArray(saved.collapsedLayers)) {
    for (const key of saved.collapsedLayers) collapsedLayers.add(key);
  }
  for (const base of INITIAL) {
    if (removedInferred.has(base.id)) continue;
    const s = savedMap.get(base.id) || {};
    const item = Object.assign({}, base);
    if (s.layer) item.layer = s.layer;
    if (Array.isArray(s.styles)) item.styles = s.styles.slice();
    if (s.attrKind !== undefined) item.attrKind = s.attrKind;
    if (typeof s.note === "string") item.note = s.note;
    if (s.reviewed) item.reviewed = true;
    if (item.layer === "style" && !item.styles.length) item.layer = "unassigned";
    if (item.layer === "color" && !item.attrKind) item.attrKind = "specific";
    state.push(item);
    byId.set(item.id, item);
  }
  if (saved && Array.isArray(saved.extraItems)) {
    for (const raw of saved.extraItems) {
      if (!raw || !raw.id || byId.has(raw.id)) continue;
      const item = Object.assign({note:"", styles:[], attrKind:"", inferred:true}, raw);
      item.inferred = true;
      state.push(item);
      byId.set(item.id, item);
      extraItems.push(item);
    }
  }
}
function loadState(){
  const saved = storageGet();
  if (saved) { hydrate(saved); return; }
  // 应用内预览每次刷新换 origin → localStorage 被清空，但 window.name
  // 在同一个标签页里跨刷新保留，用它兜底，刷新后不用手动打开存档。
  try {
    if ((window.name || "").startsWith("codex-board:")) {
      hydrate(JSON.parse(window.name.slice(12)));
      return;
    }
  } catch (e) {}
  hydrate(null);
}
function clearState(){
  state.length = 0;
  byId.clear();
  extraItems.length = 0;
  removedInferred.clear();
  collapsedFamilies.clear();
  collapsedLayers.clear();
  for (const k of Object.keys(bucketNotes)) delete bucketNotes[k];
  for (const k of Object.keys(familyNotes)) delete familyNotes[k];
}
let stateFile = null;
let fileTimer = null;
function fileStatus(text){
  const el = document.getElementById("file-status");
  if (el) el.textContent = text;
}
function scheduleFileWrite(){
  if (!stateFile) return;
  clearTimeout(fileTimer);
  fileTimer = setTimeout(writeStateFile, 250);
}
async function writeStateFile(){
  if (!stateFile) return;
  try {
    const w = await stateFile.createWritable();
    await w.write(JSON.stringify(stateData(), null, 2));
    await w.close();
    fileStatus("已保存 " + new Date().toLocaleTimeString() + " · " + stateFile.name);
  } catch (e) {
    stateFile = null;
    fileStatus("存档写入失败，点「新建存档」重新连接");
  }
}
function initFileSave(){
  const openBtn = document.getElementById("open-state-btn");
  const saveBtn = document.getElementById("save-as-btn");
  if (!window.showOpenFilePicker || !window.showSaveFilePicker) {
    openBtn.hidden = true;
    saveBtn.hidden = true;
    fileStatus("当前浏览器不支持直接写文件，请用导出 JSON");
    return;
  }
  const types = [{description:"JSON", accept:{"application/json":[".json"]}}];
  openBtn.addEventListener("click", async () => {
    try {
      const [h] = await window.showOpenFilePicker({types, multiple:false});
      const data = JSON.parse(await (await h.getFile()).text());
      if (!data || !Array.isArray(data.items)) throw new Error("bad");
      clearState();
      hydrate(data);
      stateFile = h;
      syncBucketNotes();
      syncLayerFolds();
      saveState();
      render();
      fileStatus("已连接 " + h.name + " · 自动保存");
    } catch (e) {
      if (e && e.name !== "AbortError") alert("打开失败：不是有效的存档文件");
    }
  });
  saveBtn.addEventListener("click", async () => {
    try {
      const h = await window.showSaveFilePicker({
        suggestedName:"codex_board_state.json", types});
      stateFile = h;
      await writeStateFile();
    } catch (e) {}
  });
  fileStatus("存档未连接：刷新后点「打开存档」");
}
function announce(msg){
  const live = document.getElementById("live");
  live.textContent = "";
  setTimeout(() => { live.textContent = msg; }, 30);
}
function effectSnippet(detail){
  const parts = (detail || "").split("\n").map(s => s.trim()).filter(Boolean);
  if (parts.length < 2) return "";
  // 第 1 段是名字；类型标签（MILK / YOGURT•）和「基础价值」不是效果，跳过。
  const body = parts.slice(1).filter(p =>
    !/^[A-Z0-9\s•·.、]+$/.test(p) && !p.startsWith("基础价值"));
  return body.join(" · ").slice(0, 160);
}
function matches(item){
  const q = filter.q.trim().toLowerCase();
  if (q && !(item.name.toLowerCase().includes(q) ||
             (item.detail || "").toLowerCase().includes(q))) return false;
  if (filter.source !== "all" && item.source !== filter.source) return false;
  if (filter.notes && !(item.note || "").trim()) return false;
  return true;
}
function makeCard(item, contextStyle){
  const card = document.createElement("article");
  card.className = "card" + (item.inferred ? " inferred" : "")
    + (item.reviewed ? " reviewed" : "");
  card.dataset.id = item.id;
  card.dataset.layer = item.layer;
  const title = document.createElement("button");
  title.type = "button";
  title.className = "card-title";
  title.textContent = item.name;
  title.id = "card-title-" + (++seq);
  card.setAttribute("aria-labelledby", title.id);
  title.addEventListener("click", () => openDetail(item.id));

  const top = document.createElement("div");
  top.className = "card-top";
  const grab = document.createElement("button");
  grab.type = "button";
  grab.className = "grab";
  grab.draggable = true;
  grab.textContent = "⠿";
  grab.title = "拖动到其他分类；或按 Enter 打开分类";
  grab.setAttribute("aria-label", "拖动 " + item.name + " 到其他分类，或打开分类");
  let dragged = false;
  grab.addEventListener("dragstart", e => {
    dragged = true;
    dragId = item.id;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", item.id);
    try { e.dataTransfer.setDragImage(card, 24, 20); } catch(err) {}
    card.classList.add("dragging");
  });
  grab.addEventListener("dragend", () => {
    card.classList.remove("dragging");
    dragId = null;
    setTimeout(() => { dragged = false; }, 0);
  });
  grab.addEventListener("click", () => {
    if (dragged) return;
    openDetail(item.id, true);
  });
  top.append(grab, title);
  const done = document.createElement("button");
  done.type = "button";
  done.className = "check";
  done.textContent = "✓";
  done.setAttribute("aria-pressed", String(!!item.reviewed));
  done.setAttribute("aria-label", "标记 " + item.name + " 已处理");
  done.title = item.reviewed ? "已处理（点击取消）" : "标记已处理";
  done.addEventListener("click", () => {
    item.reviewed = !item.reviewed;
    saveState(); render();
  });
  top.append(done);

  const badges = document.createElement("div");
  badges.className = "badges";
  const src = document.createElement("span");
  src.className = "badge " + item.source;
  src.textContent = SOURCE_LABEL[item.source] || item.source;
  badges.append(src);
  if (item.inferred) {
    const inf = document.createElement("span");
    inf.className = "badge inferred";
    inf.textContent = "推断";
    inf.title = "图鉴未点亮：按族推论，未实测";
    badges.append(inf);
  }
  if (item.baseValue !== null && item.baseValue !== undefined) {
    const bv = document.createElement("span");
    bv.className = "badge";
    bv.textContent = "价值 " + item.baseValue;
    badges.append(bv);
  }
  if (item.layer === "style" && contextStyle && item.styles.length > 1) {
    const sh = document.createElement("span");
    sh.className = "badge shared";
    sh.textContent = "共享 ×" + item.styles.length;
    badges.append(sh);
  }
  if ((item.note || "").trim()) {
    const note = document.createElement("span");
    note.className = "badge note";
    note.textContent = "✎ 备注";
    badges.append(note);
  }
  if (contextStyle) {
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "remove-style";
    rm.textContent = "×";
    rm.title = "从“" + contextStyle + "”桶移除";
    rm.setAttribute("aria-label", "从 " + contextStyle + " 桶移除 " + item.name);
    rm.addEventListener("click", e => {
      e.stopPropagation();
      const styles = item.styles.filter(s => s !== contextStyle);
      item.styles = styles;
      if (!styles.length) item.layer = "unassigned";
      saveState();
      render();
      announce(item.name + " 移出 " + contextStyle);
    });
    top.append(rm);
  }

  const fx = document.createElement("p");
  fx.className = "card-fx";
  fx.textContent = effectSnippet(item.detail)
    || (item.inferred ? "图鉴未点亮，效果未知" : "");
  card.append(top, badges, fx);
  return card;
}
function bucketFor(item){
  if (item.layer === "style") return null;
  if (item.layer === "color") return null;
  return item.layer;
}
function render(){
  document.querySelectorAll(".cards").forEach(c => { c.replaceChildren(); });
  const counts = {};
  for (const item of state) {
    counts[item.layer] = (counts[item.layer] || 0) + 1;
    if (!matches(item)) continue;
    if (item.layer === "style") {
      for (const s of item.styles) {
        const box = document.querySelector('.cards[data-cards="style:' + s + '"]');
        if (box) box.append(makeCard(item, s));
      }
    } else if (item.layer === "color") {
      const a = item.attrKind || "specific";
      const box = document.querySelector('.cards[data-cards="color:' + a + '"]');
      if (box) box.append(makeCard(item));
    } else {
      const box = document.querySelector('.cards[data-cards="' + item.layer + '"]');
      if (box) box.append(makeCard(item));
    }
  }
  const setCount = (key, val) => {
    document.querySelectorAll('[data-count="' + key + '"]').forEach(el => {
      el.textContent = val;
    });
  };
  for (const l of ["essential","conditional","style","color","never","unknown","unassigned"]) {
    setCount(l, counts[l] || 0);
  }
  for (const s of ["训练流","商店流","扭蛋流","小游戏流"]) {
    setCount("style:" + s, state.filter(i => i.layer === "style" && i.styles.includes(s)).length);
  }
  for (const a of ["all","preferred","specific","weird","random"]) {
    setCount("color:" + a, state.filter(i => i.layer === "color" && (i.attrKind || "specific") === a).length);
  }
  document.querySelectorAll(".cards").forEach(box => {
    if (!box.children.length) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "拖到这里";
      box.append(p);
    }
  });
  renderStats(counts);
  if (currentView === "family") renderFamilies();
}
function renderStats(counts){
  const stats = document.getElementById("stats");
  stats.replaceChildren();
  const total = state.length;
  const notes = state.filter(i => (i.note || "").trim()).length;
  const defs = [
    ["total", total, ""],
    ["essential", counts.essential || 0, "essential"],
    ["conditional", counts.conditional || 0, "conditional"],
    ["style", counts.style || 0, "style"],
    ["color", counts.color || 0, "color"],
    ["never", counts.never || 0, "never"],
    ["unknown", counts.unknown || 0, "unknown"],
    ["unassigned", counts.unassigned || 0, "unassigned"],
    ["checked", state.filter(i => i.reviewed).length, ""],
    ["notes", notes, ""]
  ];
  for (const [label, val, cls] of defs) {
    const d = document.createElement("div");
    d.className = "stat " + cls;
    const b = document.createElement("b");
    b.textContent = val;
    const s = document.createElement("span");
    s.textContent = label;
    d.append(b, s);
    stats.append(d);
  }
}
function familyKey(name){
  let s = (name || "").replace(/[+＋]+\d*$/, "").trim();
  let pre = "";
  for (const p of ["奇妙", "偶然", "专属"]) {
    if (s.startsWith(p)) { pre = p; s = s.slice(p.length); break; }
  }
  for (const sub of SUBJECTS) {
    if (s.startsWith(sub)) { s = s.slice(sub.length); break; }
    if (s.endsWith("-" + sub)) { s = s.slice(0, -sub.length - 1); break; }
  }
  if (!s) s = "其他";
  return pre ? pre + "·" + s : s;
}
function familyGroups(){
  const groups = new Map();
  for (const item of state) {
    if (!matches(item)) continue;
    const fam = item.family || familyKey(item.name);
    const key = item.source + ":" + fam;
    if (!groups.has(key)) {
      groups.set(key, {key, source:item.source, family:fam, items:[]});
    }
    groups.get(key).items.push(item);
  }
  return [...groups.values()];
}
function layerText(item){
  if (item.layer === "style") {
    return "流派 · " + ((item.styles || []).join(" / ") || "未选桶");
  }
  if (item.layer === "color") {
    return "颜色 · " + (ATTR_LABEL[item.attrKind] || "单科");
  }
  return LAYER_LABEL[item.layer] || item.layer;
}
function familyCoverage(group){
  const seen = new Set();
  for (const item of group.items) {
    for (const sub of SUBJECTS) {
      if (item.name.includes(sub)) { seen.add(sub); break; }
    }
  }
  if (seen.size < 2) return null;
  return {have: seen.size, missing: SUBJECTS.filter(s => !seen.has(s))};
}
function familyUniform(group){
  const first = group.items[0];
  return group.items.every(i => i.layer === first.layer
    && (i.attrKind || "") === (first.attrKind || "")
    && (i.styles || []).join() === (first.styles || []).join());
}
function addInferred(group){
  const name = (prompt("推断卡名（例如 弹跳专注）") || "").trim();
  if (!name) return;
  if (name.length < 2 || name.length > 12) {
    alert("名字长度 2–12 个字");
    return;
  }
  if (byId.has("inferred:" + group.source + ":" + name)
      || state.some(i => i.name === name && i.source === group.source)) {
    alert("已经有一条同名条目了");
    return;
  }
  const first = group.items[0];
  const item = {
    id:"inferred:" + group.source + ":" + name,
    name, source:group.source, category:"inferred",
    detail:"", baseValue:null,
    layer:first.layer, styles:(first.styles || []).slice(),
    attrKind:first.attrKind || "", note:"", inferred:true,
    family:group.family
  };
  state.push(item);
  byId.set(item.id, item);
  extraItems.push(item);
  saveState(); render();
  announce("已加推断条目 " + name);
}
function makeFamily(group){
  const el = document.createElement("article");
  el.className = "family";
  const first = group.items[0];
  el.dataset.layer = first.layer;

  const head = document.createElement("div");
  head.className = "family-head";
  const src = document.createElement("span");
  src.className = "badge " + group.source;
  src.textContent = SOURCE_LABEL[group.source] || group.source;
  const h3 = document.createElement("h3");
  h3.textContent = group.family;
  const count = document.createElement("span");
  count.className = "family-count";
  const inferredCount = group.items.filter(i => i.inferred).length;
  count.textContent = group.items.length + " 张"
    + (inferredCount ? "（推断 " + inferredCount + "）" : "");
  head.append(src, h3, count);
  const cover = familyCoverage(group);
  if (cover) {
    const c = document.createElement("span");
    c.className = "family-cover" + (cover.missing.length ? " miss" : "");
    c.textContent = "六维 " + cover.have + "/6"
      + (cover.missing.length ? "，缺 " + cover.missing.join("、") : "");
    head.append(c);
  }
  const stateEl = document.createElement("span");
  stateEl.className = "family-state";
  stateEl.textContent = "当前：" + layerText(first)
    + (familyUniform(group) ? "" : " · 混合");
  head.append(stateEl);
  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "chip";
  addBtn.textContent = "＋ 推断";
  addBtn.title = "这一族再补一条图鉴未点亮的卡";
  addBtn.addEventListener("click", () => addInferred(group));
  const allDone = group.items.every(i => i.reviewed);
  const famCheck = document.createElement("button");
  famCheck.type = "button";
  famCheck.className = "chip family-check";
  famCheck.setAttribute("aria-pressed", String(allDone));
  famCheck.textContent = allDone ? "✓ 已处理" : "标记已处理";
  famCheck.title = "整族标记为已处理";
  famCheck.addEventListener("click", () => {
    const next = !allDone;
    for (const it of group.items) it.reviewed = next;
    saveState(); render();
    announce(group.family + (next ? "：整族已处理" : "：取消已处理"));
  });
  head.append(famCheck, addBtn);

  const members = document.createElement("div");
  members.className = "family-members";
  for (const item of group.items) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip" + (item.inferred ? " inferred" : "");
    chip.textContent = item.name;
    chip.title = item.inferred ? "推断条目（图鉴未点亮）"
      : (item.detail || "").split("\n").map(s => s.trim()).filter(Boolean).join(" · ");
    chip.addEventListener("click", () => openDetail(item.id));
    members.append(chip);
  }

  const controls = document.createElement("div");
  controls.className = "family-controls";
  const layerSel = document.createElement("select");
  layerSel.setAttribute("aria-label", group.family + " 整族的层");
  for (const l of ["essential", "conditional", "style", "color", "never", "unknown", "unassigned"]) {
    const o = document.createElement("option");
    o.value = l;
    o.textContent = LAYER_LABEL[l];
    layerSel.append(o);
  }
  layerSel.value = first.layer;

  const styleWrap = document.createElement("div");
  styleWrap.className = "checkline";
  const styleBoxes = STYLE_BUCKETS.map(name => {
    const lab = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = name;
    cb.checked = (first.styles || []).includes(name);
    lab.append(cb, document.createTextNode(" " + name));
    styleWrap.append(lab);
    return cb;
  });

  const attrSel = document.createElement("select");
  attrSel.setAttribute("aria-label", group.family + " 整族的属性档");
  for (const a of ATTRS) {
    const o = document.createElement("option");
    o.value = a;
    o.textContent = ATTR_LABEL[a];
    attrSel.append(o);
  }
  attrSel.value = first.attrKind || "specific";

  const noteWrap = document.createElement("div");
  noteWrap.className = "family-note";
  const note = document.createElement("textarea");
  note.rows = 1;
  note.value = familyNotes[group.key] || "";
  note.placeholder = "这族怎么定的？图鉴没采到的成员怎么处理…";
  note.setAttribute("aria-label", group.family + " 整族备注");
  noteWrap.append(note);

  controls.append(layerSel, styleWrap, attrSel, noteWrap);
  const body = document.createElement("div");
  body.className = "family-body";
  body.append(members, controls);
  const fold = document.createElement("button");
  fold.type = "button";
  fold.className = "fold";
  fold.addEventListener("click", e => {
    e.stopPropagation();
    setOpen(body.hidden);
    saveState();
  });
  const setOpen = open => {
    body.hidden = !open;
    fold.textContent = open ? "▾ 折叠" : "▸ 展开";
    fold.setAttribute("aria-expanded", String(open));
    fold.setAttribute("aria-label", (open ? "折叠 " : "展开 ") + group.family);
    el.classList.toggle("collapsed", !open);
    if (open) collapsedFamilies.delete(group.key);
    else collapsedFamilies.add(group.key);
  };
  head.addEventListener("click", e => {
    if (e.target.closest("button, select, input, textarea, label, a")) return;
    setOpen(body.hidden);
    saveState();
  });
  head.prepend(fold);
  el.append(head, body);
  setOpen(!collapsedFamilies.has(group.key));

  const sync = () => {
    const layer = layerSel.value;
    styleWrap.hidden = layer !== "style";
    attrSel.hidden = layer !== "color";
  };
  sync();

  layerSel.addEventListener("change", () => {
    for (const item of group.items) applyItemLayer(item, layerSel.value);
    saveState(); render();
    announce(group.family + " 整族 → " + LAYER_LABEL[layerSel.value]);
  });
  for (const cb of styleBoxes) {
    cb.addEventListener("change", () => {
      const picked = styleBoxes.filter(x => x.checked).map(x => x.value);
      for (const item of group.items) {
        if (picked.length) { item.layer = "style"; item.styles = picked.slice(); }
        else if (item.layer === "style") item.layer = "unassigned";
      }
      saveState(); render();
      announce(group.family + " 流派桶：" + (picked.join("、") || "无"));
    });
  }
  attrSel.addEventListener("change", () => {
    for (const item of group.items) {
      item.layer = "color";
      item.attrKind = attrSel.value;
      item.styles = [];
    }
    saveState(); render();
    announce(group.family + " 整族 → " + ATTR_LABEL[attrSel.value]);
  });
  let timer = null;
  note.addEventListener("input", () => {
    familyNotes[group.key] = note.value;
    clearTimeout(timer);
    timer = setTimeout(saveState, 250);
  });
  return el;
}
function renderFamilies(){
  const box = document.getElementById("family-view");
  const groups = familyGroups();
  const list = document.createElement("div");
  list.className = "family-list";
  for (const group of groups) list.append(makeFamily(group));
  if (!groups.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "没有匹配的条目";
    list.append(p);
  }
  box.replaceChildren(list);
}
function setView(view){
  currentView = view;
  document.querySelectorAll("#board > section").forEach(s => { s.hidden = view !== "board"; });
  document.getElementById("family-view").hidden = view !== "family";
  document.querySelectorAll(".view-toggle button").forEach(b => {
    b.setAttribute("aria-pressed", String(b.dataset.view === view));
  });
  if (view === "family") renderFamilies(); else render();
}
function openDetail(id, focusLayer){
  const item = byId.get(id);
  if (!item) return;
  openId = id;
  document.getElementById("detail-title").textContent = item.name;
  const meta = [SOURCE_LABEL[item.source],
                item.inferred ? "推断（图鉴未点亮）" : item.category,
                item.layer === "style" ? item.styles.join(" / ") : LAYER_LABEL[item.layer],
                item.baseValue !== null && item.baseValue !== undefined ? "价值 " + item.baseValue : ""]
               .filter(Boolean).join(" · ");
  document.getElementById("detail-meta").textContent = meta;
  const lines = (item.detail || "").split("\n").map(s => s.trim()).filter(Boolean).join("\n");
  document.getElementById("detail-text").textContent =
    lines || (item.inferred ? "图鉴未点亮，没有实测效果文本。" : "");
  document.getElementById("delete-inferred").hidden = !item.inferred;
  const layer = document.getElementById("f-layer");
  layer.value = item.layer;
  const checkboxes = document.querySelectorAll('#f-styles-wrap input[type="checkbox"]');
  checkboxes.forEach(cb => { cb.checked = item.styles.includes(cb.value); });
  document.getElementById("f-attr").value = item.attrKind || "specific";
  document.getElementById("f-note").value = item.note || "";
  syncDetailFields();
  const dialog = document.getElementById("detail");
  if (!dialog.open) dialog.showModal();
  if (focusLayer) layer.focus();
  else document.getElementById("f-note").focus();
}
function syncDetailFields(){
  const layer = document.getElementById("f-layer").value;
  document.getElementById("f-styles-wrap").hidden = layer !== "style";
  document.getElementById("f-attr-wrap").hidden = layer !== "color";
}
function applyItemLayer(item, layer){
  item.layer = layer;
  if (layer !== "style") item.styles = [];
  if (layer !== "color") item.attrKind = "";
  if (layer === "style" && !item.styles.length) item.styles = ["训练流"];
  if (layer === "color" && !item.attrKind) item.attrKind = "specific";
}
function moveItem(id, zone){
  const item = byId.get(id);
  if (!item) return;
  const layer = zone.dataset.drop;
  if (layer === "style") {
    const s = zone.dataset.style;
    item.layer = "style";
    if (!item.styles.includes(s)) item.styles = item.styles.concat(s);
    item.attrKind = "";
    announce(item.name + " 加入 " + s + " 桶");
  } else if (layer === "color") {
    item.layer = "color";
    item.attrKind = zone.dataset.attr;
    item.styles = [];
    announce(item.name + " → " + ATTR_LABEL[item.attrKind]);
  } else {
    applyItemLayer(item, layer);
    announce(item.name + " → " + (LAYER_LABEL[layer] || layer));
  }
  saveState();
  render();
}
function bindDrops(){
  document.querySelectorAll("[data-drop]").forEach(zone => {
    zone.addEventListener("dragover", e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      zone.classList.add("over");
    });
    zone.addEventListener("dragleave", e => {
      if (!zone.contains(e.relatedTarget)) zone.classList.remove("over");
    });
    zone.addEventListener("drop", e => {
      e.preventDefault();
      zone.classList.remove("over");
      const id = e.dataTransfer.getData("text/plain") || dragId;
      if (id) moveItem(id, zone);
    });
  });
}
function bucketKey(zone){
  if (zone.dataset.style) return "style:" + zone.dataset.style;
  if (zone.dataset.attr) return "color:" + zone.dataset.attr;
  return zone.dataset.drop;
}
function initBucketNotes(){
  document.querySelectorAll("[data-drop]").forEach(zone => {
    const key = bucketKey(zone);
    const title = zone.querySelector("h2,h3").textContent.replace(/\s+/g, " ").replace(/\s*\d+$/, "").trim();
    const wrap = document.createElement("div");
    wrap.className = "bucket-note";
    const ta = document.createElement("textarea");
    ta.rows = 2;
    ta.value = bucketNotes[key] || "";
    ta.placeholder = "这一类怎么定的，写一句备忘…";
    ta.setAttribute("aria-label", title + " 分类备注");
    let timer = null;
    ta.addEventListener("input", () => {
      bucketNotes[key] = ta.value;
      clearTimeout(timer);
      timer = setTimeout(saveState, 250);
    });
    wrap.append(ta);
    zone.querySelector(".layer-head, h3").insertAdjacentElement("afterend", wrap);
    NOTE_ZONES.push({key, ta});
  });
}
function syncBucketNotes(){
  for (const {key, ta} of NOTE_ZONES) ta.value = bucketNotes[key] || "";
}
function syncLayerFolds(){
  document.querySelectorAll("#board > section.layer").forEach(sec => {
    const btn = sec.querySelector(".layer-head .fold");
    if (!btn) return;
    const open = !collapsedLayers.has(sec.dataset.drop);
    sec.classList.toggle("collapsed", !open);
    btn.textContent = open ? "▾ 折叠" : "▸ 展开";
    btn.setAttribute("aria-expanded", String(open));
    const h2 = sec.querySelector(".layer-head h2");
    if (h2) {
      btn.setAttribute("aria-label",
        (open ? "折叠 " : "展开 ") + h2.textContent.trim());
    }
  });
}
function initLayerFolds(){
  document.querySelectorAll("#board > section.layer").forEach(sec => {
    const head = sec.querySelector(".layer-head");
    if (!head || head.querySelector(".fold")) return;
    const key = sec.dataset.drop;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fold";
    btn.addEventListener("click", () => {
      if (collapsedLayers.has(key)) collapsedLayers.delete(key);
      else collapsedLayers.add(key);
      syncLayerFolds();
      saveState();
    });
    const hint = head.querySelector(".hint");
    if (hint) head.insertBefore(btn, hint); else head.append(btn);
  });
  syncLayerFolds();
}
function bindControls(){
  document.querySelectorAll(".view-toggle button").forEach(b => {
    b.addEventListener("click", () => setView(b.dataset.view));
  });
  const q = document.getElementById("q");
  q.addEventListener("input", () => { filter.q = q.value; render(); });
  const source = document.getElementById("source-filter");
  source.addEventListener("change", () => { filter.source = source.value; render(); });
  document.getElementById("notes-only").addEventListener("change", e => {
    filter.notes = e.target.checked; render();
  });
  document.getElementById("detail").addEventListener("close", () => {
    openId = null; render();
  });
  document.getElementById("close-detail").addEventListener("click", () => {
    document.getElementById("detail").close();
  });
  document.getElementById("delete-inferred").addEventListener("click", () => {
    const item = byId.get(openId);
    if (!item || !item.inferred) return;
    if (!confirm("删除推断条目「" + item.name + "」？")) return;
    const idx = state.indexOf(item);
    if (idx >= 0) state.splice(idx, 1);
    byId.delete(item.id);
    const ei = extraItems.findIndex(x => x.id === item.id);
    if (ei >= 0) extraItems.splice(ei, 1);
    else removedInferred.add(item.id);
    document.getElementById("detail").close();
    saveState(); render();
    announce("已删除推断条目 " + item.name);
  });
  document.getElementById("f-layer").addEventListener("change", e => {
    const item = byId.get(openId);
    if (!item) return;
    applyItemLayer(item, e.target.value);
    syncDetailFields();
    const boxes = document.querySelectorAll('#f-styles-wrap input[type="checkbox"]');
    boxes.forEach(cb => { cb.checked = item.styles.includes(cb.value); });
    document.getElementById("f-attr").value = item.attrKind || "specific";
    saveState(); render();
    announce(item.name + " → " + (LAYER_LABEL[item.layer] || item.layer));
  });
  document.querySelectorAll('#f-styles-wrap input[type="checkbox"]').forEach(cb => {
    cb.addEventListener("change", () => {
      const item = byId.get(openId);
      if (!item) return;
      const styles = [...document.querySelectorAll('#f-styles-wrap input:checked')]
        .map(x => x.value);
      item.styles = styles;
      if (styles.length) item.layer = "style";
      else if (item.layer === "style") item.layer = "unassigned";
      saveState(); render();
      announce(item.name + " 流派桶：" + (styles.join("、") || "无"));
    });
  });
  document.getElementById("f-attr").addEventListener("change", e => {
    const item = byId.get(openId);
    if (!item) return;
    item.layer = "color";
    item.attrKind = e.target.value;
    item.styles = [];
    saveState(); render();
    announce(item.name + " → " + ATTR_LABEL[item.attrKind]);
  });
  document.getElementById("f-note").addEventListener("input", e => {
    const item = byId.get(openId);
    if (!item) return;
    item.note = e.target.value;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveState, 250);
  });
  document.getElementById("export-btn").addEventListener("click", exportJson);
  document.getElementById("import-btn").addEventListener("click", () => {
    document.getElementById("import-file").click();
  });
  document.getElementById("import-file").addEventListener("change", importJson);
  document.getElementById("reset-btn").addEventListener("click", () => {
    if (!confirm("清空本机保存的拖动和备注，回到生成的草稿？")) return;
    storageRemove();
    window.name = "";
    location.reload();
  });
}
function exportJson(){
  const data = {
    version:1,
    exportedAt:new Date().toISOString(),
    items:state.map(i => ({
      id:i.id, name:i.name, source:i.source, category:i.category,
      layer:i.layer, styles:i.styles, attrKind:i.attrKind, note:i.note,
      inferred:!!i.inferred, family:i.family || "", reviewed:!!i.reviewed
    })),
    bucketNotes:bucketNotes,
    familyNotes:familyNotes,
    removedInferred:[...removedInferred]
  };
  const blob = new Blob([JSON.stringify(data, null, 2)], {type:"application/json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "codex-board-" + new Date().toISOString().slice(0,10) + ".json";
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  announce("已导出 JSON");
}
function importJson(e){
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      if (!data || !Array.isArray(data.items)) throw new Error("bad json");
      const map = new Map(data.items.map(i => [i.id, i]));
      for (const item of state) {
        const saved = map.get(item.id);
        if (!saved) continue;
        if (saved.layer) item.layer = saved.layer;
        if (Array.isArray(saved.styles)) item.styles = saved.styles.slice();
        if (saved.attrKind !== undefined) item.attrKind = saved.attrKind;
        if (typeof saved.note === "string") item.note = saved.note;
        if (saved.reviewed !== undefined) item.reviewed = !!saved.reviewed;
        if (item.layer === "style" && !item.styles.length) item.layer = "unassigned";
        if (item.layer === "color" && !item.attrKind) item.attrKind = "specific";
      }
      if (data.bucketNotes && typeof data.bucketNotes === "object") {
        for (const k of Object.keys(bucketNotes)) delete bucketNotes[k];
        Object.assign(bucketNotes, data.bucketNotes);
        syncBucketNotes();
      }
      if (data.familyNotes && typeof data.familyNotes === "object") {
        for (const k of Object.keys(familyNotes)) delete familyNotes[k];
        Object.assign(familyNotes, data.familyNotes);
      }
      for (const raw of data.items) {
        if (!raw || !raw.id || byId.has(raw.id) || !raw.inferred) continue;
        const item = Object.assign({note:"", styles:[], attrKind:""}, raw);
        item.inferred = true;
        state.push(item); byId.set(item.id, item); extraItems.push(item);
      }
      if (Array.isArray(data.removedInferred)) {
        for (const id of data.removedInferred) {
          removedInferred.add(id);
          const item = byId.get(id);
          if (!item) continue;
          const idx = state.indexOf(item);
          if (idx >= 0) state.splice(idx, 1);
          byId.delete(id);
        }
      }
      saveState(); render(); announce("已导入 JSON");
    } catch (err) {
      alert("导入失败：不是有效的导出文件。");
    } finally {
      e.target.value = "";
    }
  };
  reader.readAsText(file);
}
loadState();
if (location.protocol === "oc-preview:") {
  const tip = document.createElement("p");
  tip.className = "empty";
  tip.style.margin = "0 0 14px";
  tip.textContent = "注意：应用内预览刷新会换来源，但同一标签页会自动恢复；关闭标签页后需点「打开存档」或导出 JSON。";
  document.getElementById("family-view").before(tip);
}
initBucketNotes();
initLayerFolds();
initFileSave();
bindDrops();
bindControls();
render();
</script>
</body>
</html>
"""


def main():
    items = shop_items() + gacha_items()
    data = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")
    html = HTML.replace("__DATA__", data)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    from collections import Counter
    print("items", len(items))
    print("sources", Counter(i["source"] for i in items))
    print("layers", Counter(i["layer"] for i in items))
    print("out", OUT)


if __name__ == "__main__":
    main()
