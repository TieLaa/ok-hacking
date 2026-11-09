"""死代码门禁：未用导入 / 没人引用的函数 / 无对应控件的 QSS 规则 / 布局里没人读的键。

为什么要这个（2026-11-10）
    删功能以后残留的东西（比如编辑模式的 `_slot_edit`、已删按钮的 QSS 规则、
    布局里没人读的 `starting_text`）单测抓不到、编译也抓不到，只能靠人肉。
    这个脚本把它们变成 exit != 0。

用法
    python tools/check_dead_code.py            # 报告并返回退出码（0 = 干净）
    python tools/check_dead_code.py --strict   # 同一套规则，供测试调用

豁免
    tools/dead_code_allowlist.txt，一行一条，支持：
        import  <文件> <名字>        —— 故意保留的导入
        func    <文件> <名字>        —— 故意保留的函数
        qss     <对象名>             —— 故意保留的 QSS 选择器
        layout  <键名>               —— 故意保留的布局键
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIRS = [ROOT / 'src', ROOT / 'tools', ROOT / 'tests']
QSS_FILES = [ROOT / 'assets' / 'ui' / 'monitor.dark.qss',
             ROOT / 'assets' / 'ui' / 'monitor.light.qss']
LAYOUT_FILE = ROOT / 'configs' / 'monitor_layout.json'
ALLOWLIST = ROOT / 'tools' / 'dead_code_allowlist.txt'

# 框架按名字调用的方法/属性，不能按「没人引用」判断
CONTRACT = {
    'run', 'reset_to_start', 'verify_start_state', 'load_config', 'on_destroy',
    'start', 'stop', 'pause', 'unpause', 'enable', 'disable', 'save', 'load',
    'capture', 'click', 'swipe', 'scroll', 'send_key', 'send_key_down', 'send_key_up',
    'mouse_down', 'mouse_up', 'move', 'input_text', 'connected', 'close', 'refresh',
    'eventFilter', 'paintEvent', 'resizeEvent', 'showEvent', 'enterEvent', 'leaveEvent',
    'mousePressEvent', 'mouseMoveEvent', 'mouseReleaseEvent', 'wheelEvent',
    'minimumSizeHint', 'sizeHint', 'name', 'description', 'icon', 'default_config',
    'config_type', 'config_description', 'add_after_default_tabs',
    'set_window_size', 'tr', 'dispose',
    # 被框架/标准库按名字调用的基类钩子
    'get_abs_cords', 'get_top_window_cords', 'connected', 'do_GET', 'do_POST',
    'log_message', 'setUp', 'tearDown', 'setUpClass', 'tearDownClass',
}

# 这些前缀的方法由框架/测试运行器按名字调用，不参与死函数扫描
CONTRACT_PREFIX = ('test_', 'do_', 'handle_', 'visit_', 'on_', 'notify_')


def _is_contract(name: str) -> bool:
    return name in CONTRACT or name.startswith('__') or any(
        name.startswith(p) for p in CONTRACT_PREFIX)


def _allow(path: str, kind: str, name: str) -> bool:
    return (kind, path, name) in _ALLOW or (kind, '*', name) in _ALLOW


def _load_allow() -> set:
    out = set()
    if not ALLOWLIST.exists():
        return out
    for raw in ALLOWLIST.read_text(encoding='utf-8').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) == 3:
            out.add((parts[0], parts[1].replace('\\', '/'), parts[2]))
        elif len(parts) == 2:
            out.add((parts[0], '*', parts[1]))
    return out


_ALLOW = _load_allow()


def _rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT).as_posix()


def _py_files():
    for d in SRC_DIRS:
        for p in d.rglob('*.py'):
            if '__pycache__' not in str(p) and p.name != 'check_dead_code.py':
                yield p


def check_unused_imports(files):
    problems = []
    for p in files:
        src = p.read_text(encoding='utf-8', errors='replace')
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()
        bound = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    bound[(a.asname or a.name).split('.')[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                if node.module == '__future__':
                    continue
                for a in node.names:
                    if a.name == '*':
                        continue
                    bound[a.asname or a.name] = node.lineno
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for name in list(bound):
                    if re.search(rf'\b{re.escape(name)}\b', node.value):
                        used.add(name)
        for name, ln in bound.items():
            if name in used:
                continue
            line = lines[ln - 1] if ln - 1 < len(lines) else ''
            if 'noqa' in line.lower():
                continue
            if _allow(_rel(p), 'import', name):
                continue
            problems.append(f'{_rel(p)}:{ln} 未使用的导入 {name}')
    return problems


def check_dead_functions(files):
    problems = []
    texts = {p: p.read_text(encoding='utf-8', errors='replace') for p in files}
    whole = '\n'.join(texts.values())
    for p, src in texts.items():
        # 测试文件里的方法由 unittest 按名字调用，不参与死函数扫描
        if p.resolve().is_relative_to(ROOT / 'tests'):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        defs = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defs.append(m)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.append(node)
        for m in defs:
            name = m.name
            if _is_contract(name):
                continue
            body = whole.replace(f'def {name}(', '', 1)
            if re.search(rf'\.{re.escape(name)}\b', body) or \
               re.search(rf'\b{re.escape(name)}\b', body):
                continue
            if _allow(_rel(p), 'func', name):
                continue
            problems.append(f'{_rel(p)}:{m.lineno} 没人引用的函数 {name}')
    return problems


def check_qss(files):
    """QSS 里的 #objectName 必须能在源码里找到 setObjectName(...) / object_name=...。"""
    problems = []
    code = '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in files)
    names = set(re.findall(r'setObjectName\(\s*["\']([^"\']+)["\']', code))
    names |= set(re.findall(r'object_name\s*=\s*["\']([^"\']+)["\']', code))
    for qss in QSS_FILES:
        if not qss.exists():
            continue
        for raw in qss.read_text(encoding='utf-8', errors='replace').splitlines():
            for name in re.findall(r'#([A-Za-z_][\w-]*)', raw.split('{', 1)[0]):
                if re.fullmatch(r'[0-9a-fA-F]{3,8}', name):   # 十六进制颜色不是 objectName
                    continue
                if name in names or _allow('assets/ui', 'qss', name):
                    continue
                problems.append(f'{qss.name} 选择器 #{name} 在源码里没有对应控件')
    return problems


def check_layout_keys(files):
    """monitor_layout.json 里的键，渲染层必须有 spec.get("key") 之类的读取。

    只检查 content 键（type/height 这类元信息除外）。
    """
    if not LAYOUT_FILE.exists():
        return []
    problems = []
    code = '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in files)
    read_keys = set(re.findall(r'\.get\(\s*["\']([^"\']+)["\']', code))
    read_keys |= set(re.findall(r'\[\s*["\']([^"\']+)["\']\s*\]', code))
    meta = {'type', 'height', 'version', 'sections', 'items', 'stats', 'rows',
            'bar', 'bar_key', 'bar_width', 'bar', '_hidden', 'id'}
    data = json.loads(LAYOUT_FILE.read_text(encoding='utf-8'))

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k not in meta and k not in read_keys and not _allow('layout', 'layout', k):
                    problems.append(f'monitor_layout.json 键 {k!r} 渲染层没有读取')
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return problems


def check_all():
    files = list(_py_files())
    problems = []
    problems += check_unused_imports(files)
    problems += check_dead_functions(files)
    problems += check_qss(files)
    problems += check_layout_keys(files)
    return problems


def main():
    problems = check_all()
    if problems:
        print(f'死代码门禁：{len(problems)} 处')
        for p in problems:
            print('  - ' + p)
        print('\n如果是故意保留，写进 tools/dead_code_allowlist.txt（import/func/qss/layout <文件|*> <名字>）')
        return 1
    print('死代码门禁：干净（未用导入 / 死函数 / QSS 选择器 / 布局键）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
