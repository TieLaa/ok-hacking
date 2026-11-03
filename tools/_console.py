"""tools/ 共用：**控制台输出策略**（Windows 码页 936 的坑）。

实测定因（2026-11-03）：
    Python 写 UTF-8 → **不经过 cmdlet** 时字节原样透传，中文正确；
    一旦接进 PowerShell 的 cmdlet（`Select-Object` / `Select-String` /
    `Where-Object` / `Out-String`），pwsh 按 `CP936` 去解那段 UTF-8 → 中文被截坏，
    极端情况变成希伯来字母。反过来写 GBK 字节，两条路都坏。
    **没有任何一种编码能同时满足两条路** —— 所以不在编码上赌，改结构：

    1. stdout 只保证「不崩」：UTF-8 + `errors="replace"`
       （本机默认 cp936，`✓/✗` 会直接 UnicodeEncodeError → 脚本 exit 1，
       看起来像「判据回归失败」，其实没问题）；
    2. **需要被读的中文**（OCR 结果、回归报告）一律另外写一份 UTF-8 文件，
       stdout 只打 ASCII 的件数/路径 —— 父进程按哪个码页解码都不影响内容；
    3. 交互终端（`isatty()`）照旧打中文，人看着舒服。

用法：

    from tools._console import utf8_stdout, write_report
    utf8_stdout()
    path = write_report("ocr.txt", text)
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(ROOT, "logs", "reports")


def utf8_stdout():
    """stdout/stderr 固定 UTF-8 且不因码页崩（`✓/✗` 在 cp936 下会抛异常）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                     # noqa: BLE001  （被换成非文本流时）
            pass


def write_report(name, text):
    """把给人看的中文报告写进 `logs/reports/<name>`（UTF-8），返回绝对路径。"""
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, name)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path
