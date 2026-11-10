"""存证目录定位：**老平铺结构和新「按局分目录」结构都认**（2026-11-10）。

老（2026-11-10 之前）：

    logs/events/  logs/unknown/  logs/unmark/  logs/debug/

新（按局分目录，见 `src/tasks/evidence.py`）：

    logs/<局ID>/{events,unknown,unmark,debug}/
    logs/mcp/<run_id>/<局ID>/{events,unknown,unmark,debug}/
    logs/pre/{events,unknown,unmark,debug}/          ← 局还没开始时的存证

所以运维/校验脚本不要写死 `logs/unknown`，用这里的函数递归找。
"""

import glob
import os


def evidence_dirs(root, names=("unknown", "debug")):
    """递归找出所有名为 `names` 里的存证子目录（去重、排序）。"""
    out = []
    for dirpath, dirnames, _filenames in os.walk(root):
        for name in names:
            if name in dirnames:
                out.append(os.path.join(dirpath, name))
    return sorted(set(out))


def evidence_files(root, name, pattern="*.json"):
    """某个存证类别下的全部文件（跨局合并，按路径排序）。"""
    out = []
    for base in evidence_dirs(root, (name,)):
        out.extend(glob.glob(os.path.join(base, pattern)))
    return sorted(out)
