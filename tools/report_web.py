"""跑完一轮看报告的本地网页（结论 + unknown 截图 + 回复框）。

为什么要有它：每轮结束后用户要看「这轮发生了什么、哪些界面是 unknown、长什么样」，
并且直接给我留言。写文件/剪贴板都拿不到回复，所以起一个只监听 127.0.0.1 的小服务：

    GET  /            动态生成报告（每次都读最新日志，刷新即可）
    GET  /img?p=...   把 logs/**/*.jpg|png 读出来给页面显示
    POST /reply       把回复追加到 logs/reports/replies.jsonl（Agent 读这个文件）

用法：
    .venv\\Scripts\\python.exe tools\\report_web.py            # 默认 8765
    .venv\\Scripts\\python.exe tools\\report_web.py --port 8800 --open

窗口（一轮）怎么切：从日志里**最后一条** `parsers:` 行开始算（每次开跑都会打），
一直到文件末尾；需要看更早的轮次可以用 `/?since=HH:MM:SS`。
"""

import argparse
import base64
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
UNKNOWN = os.path.join(ROOT, "logs", "unknown")
REPORTS = os.path.join(ROOT, "logs", "reports")
REPLIES = os.path.join(REPORTS, "replies.jsonl")

# 这些是「一轮里值得看」的事件：正则里第一个分组是标签
EVENT_PATTERNS = [
    ("流派", r"流派[：:]\s*(.+)$"),
    ("队长", r"(选定队长「.+?」)"),
    ("训练", r"训练「(.+?)」（失败率"),
    ("休息", r"体力 (.+?) < (\d+).*→ 休息"),
    ("比赛", r"(尝试跳过比赛|跳过不可用，进比赛|前往比赛)"),
    ("商店", r"(比赛日：.+?先进店买一次.*|商店：买「.+?」.+?）|每日商店：.+)$"),
    ("周末", r"(周末[点日六].+)$"),
    ("炒股", r"(炒股：.+)$"),
    ("技能", r"(技能评分 .+)$"),
    ("卡片", r"(事件选项.+)$"),
    ("日历", r"(训练计划：队长「.+?」→ 流派「.+?」)"),
]


def read_log():
    """读当前日志 + 当日轮转文件（合并后按时间排序）。"""
    chunks = []
    for path in (LOG,):
        if os.path.exists(path):
            chunks.append(io.open(path, encoding="utf-8", errors="replace").read())
    return "".join(chunks)


def last_window(text):
    """从最后一条 `parsers:` 行切到末尾 = 最近一轮。"""
    idx = text.rfind("parsers:")
    if idx < 0:
        return text
    # 回到该行行首
    start = text.rfind("\n", 0, idx) + 1
    return text[start:]


def summarize(window):
    lines = [l for l in window.splitlines() if l.strip()]
    out = {
        "start": lines[0][:19] if lines else "-",
        "end": lines[-1][:19] if lines else "-",
        "counts": {},
        "events": [],
        "stalls": [],
        "unknowns": [],
        "finished": "注册球员流程完成" in window,
    }
    for label, pat in EVENT_PATTERNS:
        rx = re.compile(pat)
        for l in lines:
            m = rx.search(l)
            if not m:
                continue
            text = (m.group(1) if m.groups() else m.group(0)).strip()
            out["events"].append((l[11:19], label, text))
            out["counts"][label] = out["counts"].get(label, 0) + 1
    for l in lines:
        if "秒没有点击" in l or "次点击界面没变化" in l:
            out["stalls"].append(l[:160])
    for l in lines:
        if "没见过这个界面" in l:
            out["unknowns"].append(l[:19])
    return out


def unknown_files(since_ts):
    """这一轮里存证的 unknown 帧（按时间前缀匹配）。"""
    files = []
    if not os.path.isdir(UNKNOWN):
        return files
    for path in sorted(glob.glob(os.path.join(UNKNOWN, "*.jpg"))):
        name = os.path.basename(path)
        m = re.match(r"(\d{4})-(\d{6})", name)
        if not m:
            continue
        stamp = f"{m.group(1)[:2]}-{m.group(1)[2:]}-{m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:]}"
        if stamp >= since_ts:
            txt = path[:-4] + ".txt"
            text = ""
            if os.path.exists(txt):
                text = " | ".join(
                    l.split("\t")[0] for l in
                    io.open(txt, encoding="utf-8", errors="replace").read().splitlines()[:10])
            files.append({"path": path, "name": name, "ocr": text})
    return files[-12:]


def build_html(since=None):
    window = last_window(read_log())
    info = summarize(window)
    since_ts = (since or info["start"])[11:19]
    unks = unknown_files(since_ts)
    rows = "".join(
        f"<tr><td>{html.escape(t)}</td><td>{html.escape(lab)}</td>"
        f"<td>{html.escape(txt)}</td></tr>"
        for t, lab, txt in info["events"][-60:])
    counts = "　".join(f"{k} {v}" for k, v in sorted(info["counts"].items()))
    stalls = "".join(f"<li>{html.escape(s)}</li>" for s in info["stalls"]) or "<li>无</li>"
    cards = ""
    for u in unks:
        cards += (
            f"<div class=card><a href='/img?p={quote(u['path'])}' target=_blank>"
            f"<img src='/img?p={quote(u['path'])}'></a>"
            f"<div class=meta>{html.escape(u['name'])}</div>"
            f"<div class=ocr>{html.escape(u['ocr'] or '（无 OCR 文本）')}</div></div>")
    if not cards:
        cards = "<p>这一轮没有 unknown 存证。</p>"
    return f"""<!doctype html><html lang=zh><meta charset=utf-8>
<title>训练报告 {html.escape(info['start'])}</title>
<style>
 body{{font:14px/1.6 -apple-system,'Segoe UI',sans-serif;margin:24px;max-width:1100px}}
 h1{{font-size:20px;margin:0 0 6px}} h2{{font-size:16px;margin:26px 0 8px}}
 .k{{background:#eef6ff;border-left:4px solid #2b8cff;padding:10px 14px;margin:12px 0}}
 table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ddd;padding:4px 8px;font-size:13px}}
 th{{background:#f6f6f6;text-align:left}}
 .grid{{display:flex;flex-wrap:wrap;gap:12px}} .card{{width:320px}}
 .card img{{width:100%;border:1px solid #ccc;border-radius:6px}}
 .meta{{color:#666;font-size:12px;margin:4px 0}} .ocr{{font-size:12px;color:#333;max-height:90px;overflow:auto}}
 textarea{{width:100%;height:120px;font:14px/1.5 inherit;padding:8px}}
 button{{padding:8px 22px;font-size:15px;cursor:pointer}}
</style>
<h1>本轮报告（{html.escape(info['start'])} → {html.escape(info['end'])}）</h1>
<div class=k>
 <b>是否跑完一局：</b>{'是（已回大厅）' if info['finished'] else '否（仍在跑或中断）'}<br>
 <b>事件计数：</b>{html.escape(counts) or '-'}<br>
 <b>卡死/点不动：</b>{len(info['stalls'])} 次　<b>unknown 存证：</b>{len(unks)} 张
</div>

<h2>卡死记录</h2><ul>{stalls}</ul>

<h2>关键事件（最近 60 条）</h2>
<table><tr><th>时间</th><th>类型</th><th>内容</th></tr>{rows}</table>

<h2>unknown 界面（点图看大图）</h2><div class=grid>{cards}</div>

<h2>给我留言</h2>
<form method=post action=/reply>
 <textarea name=text placeholder="这一轮哪里不对、下轮要改什么…"></textarea>
 <div style="margin-top:8px"><button type=submit>提交</button></div>
</form>
<p style="color:#888;font-size:12px">提交后写入 logs/reports/replies.jsonl，Agent 会读到并继续。</p>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):        # 安静点，别刷屏
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
            ext = os.path.splitext(ap)[1].lower()
            ctype = "image/png" if ext == ".png" else "image/jpeg"
            self._send(200, open(ap, "rb").read(), ctype)
            return
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
        rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text}
        with io.open(REPLIES, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._send(200, "<meta charset=utf-8><p style='font:16px sans-serif'>"
                        "已收到，Agent 会读取。<a href='/'>刷新看最新</a></p>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    os.makedirs(REPORTS, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"报告服务已启动：{url}（Ctrl+C 停止）")
    if args.open:
        webbrowser.open(url)
    srv.serve_forever()


if __name__ == "__main__":
    main()
