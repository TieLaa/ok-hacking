"""把 TrainingTask 里过大的点击延迟压下来。

背景：这些 after_sleep 全是早期拍的保守值（1.0~3.0s），实测界面切换 0.3~0.5s 就完成。
一局 5 天 x 每天 3~5 次点击 = 白白等好几分钟。

保留原则：
- 普通导航（下一步 / 科目 / 选项 / 关闭弹窗）→ 0.35~0.45
- 会触发加载或状态切换的（开始训练 / 比赛 / 结算 / 确认编队）→ 0.6~0.8
"""

import io
import re

PATH = "src/tasks/TrainingTask.py"

# 先整体降档，个别关键的再手动抬回去
MAP = {
    "3.0": "0.8",
    "2.5": "0.6",
    "2.0": "0.6",
    "1.5": "0.5",
    "1.2": "0.45",
    "1.0": "0.4",
    "0.9": "0.4",
    "0.8": "0.35",
}

src = io.open(PATH, encoding="utf-8").read()
before = len(re.findall(r"after_sleep=([\d.]+)", src))
src = re.sub(r"after_sleep=([\d.]+)", lambda m: f"after_sleep={MAP.get(m.group(1), m.group(1))}", src)

# 每拍间隔 0.8 -> 0.3
src = src.replace('"操作间隔(秒)": 0.8', '"操作间隔(秒)": 0.3')
# 加载等待 1 秒 -> 0.3 秒（有 loading 字样时多等没必要那么久）
src = src.replace("                self.sleep(1)\n", "                self.sleep(0.3)\n")

io.open(PATH, "w", encoding="utf-8", newline="\n").write(src)
after = len(re.findall(r"after_sleep=([\d.]+)", src))
print(f"改了 {after} 处 after_sleep（共 {before} 处）")
print("操作间隔 0.8 -> 0.3")
