"""跑完一轮看报告的本地网页：**每个事件配一张截图** + 回复框。

格式按用户 2026-10-30 要求：
    图片
    序号 / 时间 / 类型 / 内容

数据来源：
- `logs/events/<时间>_<handler>.jpg|.json`（任务里 `snap_event` 存的，事件级截图）
- `logs/ok-script.log`（取该时刻的日志文本作为「内容」）

    GET  /            动态生成报告（从日志最后一条 `parsers:` 行算一轮）
    GET  /img?p=...   输出 logs/** 下的图片
    POST /reply       把留言追加到 logs/reports/replies.jsonl

用法：
    .venv\\Scripts\\python.exe tools\\report_web.py --port 8765
"""

import argparse
import glob
import html
import io
import json
import os
import re
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "ok-script.log")
EVENTS = os.path.join(ROOT, "logs", "events")
REPORTS = os.path.join(ROOT, "logs", "reports")
REPLIES = os.path.join(REPORTS, "replies.jsonl")

TYPE_LABEL = {
    "handle_event_choice": "事件选项",
    "handle_boost_training": "加强训练",
    "handle_restaurant": "餐厅",
    "handle_park": "公园",
    "handle_street": "步行街",
    "handle_arcade": "游戏厅",
    "handle_shop": "商店",
    "handle_stock_pick": "炒股选卡",
    "handle_stock_result": "炒股结算",
    "handle_skill_select": "技能三选一",
    "handle_weekend": "周末地图",
    "handle_go_out": "周末出行",
    "handle_training_plan": "训练计划",
    "handle_result": "结算",
    "handle_match_result": "比赛结算",
    "handle_prepare": "赛前准备",
    "handle_register_player": "注册球员",
    "handle_vacation": "度假",
    "handle_rest_confirm": "休息确认",
}


def read_log():
    if not os.path.exists(LOG):
        return ""
    return io.open(LOG, encoding="utf-8", errors="replace").read()


def last_window(text):
    idx = text.rfind("parsers:")
    if idx < 0:
        return text
    return text[text.rfind("\n", 0, idx) + 1:]


def window_start(window):
    first = next((l for l in window.splitlines() if l.strip()), "")
    return first[:19] or "-"


def log_lines(window):
    """[(hh:mm:ss, 文本)]，只留 TrainingTask 的业务行。"""
    out = []
    for line in window.splitlines():
        if len(line) < 19:
            continue
        if "TrainingTask" not in line or "info_set" in line:
            continue
        msg = line.split("TrainingTask:", 1)[-1].strip()
        out.append((line[11:19], msg))
    return out


def load_events(since):
    """读 logs/events 下这一轮的快照，按时间排序。"""
    rows = []
    for path in sorted(glob.glob(os.path.join(EVENTS, "*.json"))):
        try:
            with io.open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:                        # noqa: BLE001
            continue
        if meta.get("at", "") < since:
            continue
        img = path[:-5] + ".jpg"
        rows.append({
            "at": meta.get("at", ""),
            "handler": meta.get("handler", ""),
            "texts": meta.get("texts", []),
            "img": img if os.path.exists(img) else None,
        })
    return rows


def content_for(ev, lines):
    """这条快照对应的「内容」：取同一秒内的日志文本，否则退回 OCR 摘要。"""
    hhmmss = ev["at"][11:19]
    same = [m for t, m in lines if t == hhmmss]
    if same:
        return " / ".join(same[:3])
    near = [m for t, m in lines if t[:7] == hhmmss[:7]]
    if near:
        return " / ".join(near[:2])
    return "、".join(ev["texts"][:8])


def build_html(since=None):
    window = last_window(read_log())
    start = window_start(window)
    lines = log_lines(window)
    events = load_events(since or start)

    finished = "注册球员流程完成" in window
    stalls = [m for _t, m in lines if "秒没有点击" in m or "次点击界面没变化" in m]
    kinds = {}
    for _t, m in lines:
        for key in ("训练「", "商店：买", "炒股：", "周末点", "事件选项"):
            if m.startswith(key):
                kinds[key] = kinds.get(key, 0) + 1
    counts = "　".join(f"{k}×{v}" for k, v in kinds.items()) or "-"

    cards = ""
    for i, ev in enumerate(events, 1):
        img = (f"<a href='/img?p={quote(ev['img'])}' target=_blank>"
               f"<img src='/img?p={quote(ev['img'])}'></a>") if ev["img"] else "（无截图）"
        cards += f"""<div class=ev>
  <div class=pic>{img}</div>
  <div class=kv><b>#{i}</b></div>
  <div class=kv>时间：{html.escape(ev['at'][11:19])}</div>
  <div class=kv>类型：{html.escape(TYPE_LABEL.get(ev['handler'], ev['handler']))}</div>
  <div class=kv>内容：{html.escape(content_for(ev, lines))}</div>
</div>"""
    if not cards:
        cards = "<p>这一轮还没有事件截图（新代码从下一轮开始记录）。</p>"

    stall_html = "".join(f"<li>{html.escape(s)}</li>" for s in stalls) or "<li>无</li>"

    return f"""<!doctype html><html lang=zh><meta charset=utf-8>
<title>训练报告 {html.escape(start)}</title>
<style>
 body{{font:14px/1.6 -apple-system,'Segoe UI',sans-serif;margin:24px;max-width:1000px}}
 h1{{font-size:20px;margin:0 0 6px}} h2{{font-size:16px;margin:26px 0 10px}}
 .k{{background:#eef6ff;border-left:4px solid #2b8cff;padding:10px 14px}}
 .ev{{border:1px solid #e3e3e3;border-radius:8px;padding:10px;margin:14px 0;width:520px}}
 .pic img{{width:100%;border:1px solid #ccc;border-radius:6px}}
 .kv{{margin-top:4px;font-size:13px}}
 textarea{{width:100%;height:120px;font:14px/1.5 inherit;padding:8px}}
 button{{padding:8px 22px;font-size:15px;cursor:pointer}}
</style>
<h1>本轮报告（{html.escape(start)} → 现在）</h1>
<div class=k>
 <b>是否跑完一局：</b>{'是（已回大厅）' if finished else '否（仍在跑/中断）'}<br>
 <b>事件计数：</b>{html.escape(counts)}<br>
 <b>卡死/点不动：</b>{len(stalls)} 次　<b>事件快照：</b>{len(events)} 张
</div>

<h2>卡死记录</h2><ul>{stall_html}</ul>

<h2>事件（图片 / 序号 / 时间 / 类型 / 内容）</h2>
{cards}

<h2>给我留言</h2>
<form method=post action=/reply>
 <textarea name=text placeholder="这一轮哪里不对、下轮要改什么…"></textarea>
 <div style="margin-top:8px"><button type=submit>提交</button></div>
</form>
<p style="color:#888;font-size:12px">提交后写入 logs/reports/replies.jsonl，Agent 会读到并继续。</p>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/img":
            p = parse_qs(u.query).get("p", [""])[0]
            ap = os.path.abspath(p)
            if not ap.startswith(os.path.abspath(ROOT)) or not os.path.exists(ap):
                return self._send(404, "not found", "text/plain")
            ctype = "image/png" if ap.lower().endswith(".png") else "image/jpeg"
            return self._send(200, open(ap, "rb").read(), ctype)
        if u.path == "/":
            since = parse_qs(u.query).get("since", [None])[0]
            return self._send(200, build_html(since))
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/reply":
            return self._send(404, "not found", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        text = parse_qs(body).get("text", [""])[0]
        os.makedirs(REPORTS, exist_ok=True)
        with io.open(REPLIES, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "text": text}, ensure_ascii=False) + "\n")
        self._send(200, "<meta charset=utf-8><p style='font:16px sans-serif'>"
                        "已收到，Agent 会读取。<a href='/'>刷新看最新</a></p>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    os.makedirs(REPORTS, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"报告服务已启动：http://127.0.0.1:{args.port}/")
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{args.port}/")
    srv.serve_forever()


if __name__ == "__main__":
    main()
