# MCP 薄 harness

这些文件不是任务，不会被 GUI 注册。它们是给 ok-script 的 MCP `run_task` 提交用的
**薄入口**：只做「回到基线 → 校验 → 借已注册任务实例跑一段」，业务逻辑一行都不复制。

## 为什么需要

ok-script 2.0.16+ 的 MCP runner 要求提交的文件里定义：

```python
class Task(BaseTask):
    def reset_to_start(self): ...     # 返回 False = 本次运行失败
    def verify_start_state(self): ... # 必须 assert 或返回 True
    def run(self): ...                # 返回 False = 本次运行失败
```

已注册任务（TrainingTask / QuickMatchTask …）本身不需要这些钩子，它们由 GUI 直接调
`run()`。所以这里写薄 harness，通过 `get_task_by_class` 借真实实例，
上下文（`_enabled` / `running` / `current_task` / `artifact_directory`）
由 `_harness.borrow()` 设置并在结束时还原。

## 文件

| 文件 | 用途 | 会改游戏状态吗 |
|---|---|---|
| `probe.py` | 回大厅 + 报告顶栏/OCR，改代码后的第一只探针 | 只点返回箭头 |
| `training_smoke.py` | 借 TrainingTask 跑 30 拍，验证识别→决策→点击链 | 会（正常训练流程） |
| `quickmatch_tick.py` | 借 QuickMatchTask 跑 10 拍，不回大厅 | 会（比赛内操作） |

## 跑法

```powershell
# 管理员终端；游戏窗口必须可见（Unity 遮挡时 WGC 零帧）
.venv\Scripts\python.exe -m ok mcp --config src.config:config
```

连接信息读 `logs/mcp/connections/<pid>-<port>.json`（URL + bearer token），
然后 `run_task("D:\\workspace\\ok-hacking\\tools\\mcp\\probe.py")`，
轮询 `get_run_status(run_id)` 到终态，产物在 `logs/mcp/<run_id>/`。

步数可用环境变量调：`OK_MCP_SMOKE_STEPS`、`OK_MCP_QUICK_TICKS`。
