"""杂项检查：找出源码里「裸名字用了但没导入/没定义」的 bug（同类 PROJECT_ROOT 那种）。

只查 Name 载入（`foo`），不查属性（`self.foo`）；混入类靠 self 访问，所以裸名字
基本只可能是模块级常量/函数，漏导入一眼就能报出来。

用法：.venv\\Scripts\\python.exe tools/check_undefined_names.py [路径...]
"""

import ast
import builtins
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def module_defined(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[0])
    return names


def check(path):
    with open(path, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=path)
    defined = module_defined(tree) | set(dir(builtins))
    defined |= {"__file__", "__name__", "__doc__"}
    missing = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in defined:
                missing.setdefault(node.id, node.lineno)
    return missing


def main(argv):
    targets = argv[1:] or [os.path.join(ROOT, "src"), os.path.join(ROOT, "tools")]
    bad = 0
    for target in targets:
        if os.path.isfile(target):
            files = [target]
        else:
            files = [os.path.join(base, name)
                     for base, _dirs, names in os.walk(target)
                     for name in names if name.endswith(".py")]
        for path in files:
            if "__pycache__" in path:
                continue
            try:
                missing = check(path)
            except SyntaxError as e:
                print(f"SYNTAX  {path}: {e}")
                bad += 1
                continue
            for name, line in sorted(missing.items(), key=lambda kv: kv[1]):
                print(f"UNDEF   {os.path.relpath(path, ROOT)}:{line}  {name}")
                bad += 1
    print(f"\n共 {bad} 处未定义名字")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
