"""重复代码门禁：连续 N 行规范化后相同，就报「同一块出现在多处」。

为什么要有它（2026-11-10）：
    这次人工排查发现 5 处重复（`GirlsInteraction` 六个动作各抄一遍闸门、
    `collect_roster`/`collect_ssr` 各抄合并写盘、`check_topbar_day` 两版各抄
    扫盘逻辑……）。单测抓不到、编译也抓不到，只能靠人翻或靠这种扫描。
    清完之后做成门禁，防止再长回来。

判定口径：
- 只比 `src/` 和 `tools/`；`tests/` 里的 fake 不计（测试允许相似样板）；
- 窗口 10 行（`MIN_LINES`），**8 行以下不算** —— 短片段重复是正常的
  （多 handler 开头都想先 `if self.exact_text(...) is None: return False`）；
- 同一文件同一函数内的重复不算（那是自己的循环/分支）；
- 跨函数或跨文件的同一块才报。

豁免：`tools/duplicates_allowlist.txt`，一行两个文件路径（相对仓库根），
表示「这两个文件之间允许重复」。真需要更细的再扩格式。

跑法：
    .venv\\Scripts\\python.exe tools/check_duplicates.py            # 报错并返回 1
    .venv\\Scripts\\python.exe tools/check_duplicates.py --strict    # 同上（给测试用）
"""

import ast
import hashlib
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_ROOTS = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tools")]
ALLOWLIST = os.path.join(ROOT, "tools", "duplicates_allowlist.txt")
MIN_LINES = 10


def _norm(line):
    line = line.split("#", 1)[0].strip()
    return " ".join(line.split())


def _blocks(path):
    """产出 (hash, 行号, 所属函数名, 块首行)。

    跳过：docstring 行（文档重复不算代码重复）、只含 import/from 的块、
    定义行、纯括号收尾行。剩下的才算代码重复。
    """
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except SyntaxError:
        return []
    spans = []
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            spans.append((node.name, node.lineno, node.end_lineno))
    # 所有 docstring 覆盖的行（模块/类/函数）
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node_doc = body[0]
            for ln in range(node_doc.lineno, (node_doc.end_lineno or node_doc.lineno) + 1):
                doc_lines.add(ln)

    def owner(lineno):
        best = ("<module>", 0)
        for name, a, b in spans:
            if a <= lineno <= b and a >= best[1]:
                best = (name, a)
        return best[0]

    lines = src.splitlines()
    normed = [_norm(l) for l in lines]
    out = []
    for i in range(len(normed) - MIN_LINES + 1):
        lo, hi = i + 1, i + MIN_LINES          # 1-based
        if any(ln in doc_lines for ln in range(lo, hi + 1)):
            continue                            # 文档重复不算
        chunk = normed[i:i + MIN_LINES]
        if sum(1 for c in chunk if c) < MIN_LINES * 0.7:
            continue
        if any(c in ("}", ")", "]", "},", "),") for c in chunk):
            continue
        if any(c.startswith(("def ", "class ", "@")) for c in chunk):
            continue
        if all(c.startswith(("import ", "from ", "sys.path", "ROOT ="))
               for c in chunk if c):
            continue                            # 样板 import / sys.path 块
        h = hashlib.md5("\n".join(chunk).encode()).hexdigest()
        out.append((h, i + 1, owner(i + 1), chunk[0]))
    return out


def _load_allowlist():
    pairs = set()
    if not os.path.isfile(ALLOWLIST):
        return pairs
    for line in open(ALLOWLIST, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise SystemExit(f"豁免清单格式不对（要两个文件路径）：{line!r}")
        pairs.add(frozenset(parts))
    return pairs


def check_all():
    """返回问题列表（空 = 干净）。"""
    index = defaultdict(list)
    for root in SCAN_ROOTS:
        for dp, dns, fns in os.walk(root):
            if "__pycache__" in dp:
                continue
            for f in fns:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, ROOT).replace("\\", "/")
                for h, ln, fn, first in _blocks(p):
                    index[h].append((rel, ln, fn, first))

    allowed = _load_allowlist()
    problems = []
    for _h, hits in index.items():
        uniq = {}
        for rel, ln, fn, first in hits:
            uniq.setdefault((rel, fn), (rel, ln, fn, first))
        if len(uniq) < 2:
            continue
        places = list(uniq.values())
        files = {p[0] for p in places}
        if len(files) == 2 and frozenset(files) in allowed:
            continue
        problems.append(places)
    problems.sort(key=len, reverse=True)
    return problems


def main():
    problems = check_all()
    if not problems:
        print(f"重复代码门禁：干净（{MIN_LINES} 行块，跨函数/跨文件）")
        return 0
    print(f"重复代码门禁：{len(problems)} 组")
    for places in problems:
        print(f"\n--- 同一块出现在 {len(places)} 处 ---")
        for rel, ln, fn, first in places[:6]:
            print(f"  {rel}:{ln}  [{fn}]  {first[:70]}")
    print(f"\n真的需要重复就写进 {os.path.relpath(ALLOWLIST, ROOT)}（一行两个文件）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
