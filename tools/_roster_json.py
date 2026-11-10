"""卡池采集脚本共用的「按名字合并 + 写盘」。

`collect_roster.py` 和 `collect_ssr.py` 各抄过一份 12 行的合并逻辑
（2026-11-10 重复扫描抓到），改一处就得记得改另一处。
"""

import json
import os


def merge_and_save(rows, out_path):
    """把本次采到的 rows 按 `name` 合并进 out_path，返回累计条数。

    规则：同名以**本次**为准（新数据覆盖旧数据），顺序按首次出现。
    旧文件读不出来时直接覆盖（打印原因，不静默）。
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    merged, order = {}, []
    if os.path.exists(out_path):
        try:
            for r in json.load(open(out_path, encoding="utf-8")):
                if r.get("name") and r["name"] not in merged:
                    merged[r["name"]] = r
                    order.append(r["name"])
        except Exception as e:                       # noqa: BLE001
            print("旧文件读不出来，直接覆盖：", e)
    for r in rows:
        if r.get("name") and r["name"] not in merged:
            order.append(r["name"])
        merged[r.get("name")] = r
    out = [merged[n] for n in order if n]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"已写入 {out_path}（本次 {len(rows)} 个，累计 {len(out)} 个）")
    return len(out)
