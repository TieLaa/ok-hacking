# MCP 开发循环（ok-script 2.0.16+）

本项目 2026-10-30 从 ok-script 2.0.0b7 升到 2.0.20，MCP 开发服务器随之可用。
这份文档说明「改任务 → 验证」的新流程，以及它替代掉的旧流程。

## 一条命令起服务

```powershell
# 必须是管理员终端：游戏进程是管理员权限，非管理员发不进消息
.venv\Scripts\python.exe -m ok mcp --config src.config:config
```

- 默认 `http://127.0.0.1:8765/mcp`，端口被占自动 +1。
- 连接凭据在 `logs/mcp/connections/<pid>-<port>.json`（URL + bearer token + PID）。
- `logs/mcp/connection.json` 只是「最近一次」的快照，可能是僵尸记录；以 `connections/` 为准。
- 服务是 headless 的：没有 Qt 界面，常驻 TriggerTask 默认 `enabled=false` 不会自己跑。

GUI 里也能开：设置 → 基本选项 → Enable MCP Server（默认关），或
`python main_debug.py --mcp`。**GUI 启动会顶掉同项目的 console 实例并关掉它的 MCP**，
所以二者不要同时开。

## 新流程

```
list_tasks                         # 拿 task_id + 源文件绝对路径
  ↓ 用本地编辑工具原地改源码
reload_tasks(task_ids=[...])       # 事务式热重载，失败保留旧实例
  ↓ 轮询 get_run_status 到终态
run_task("D:\workspace\ok-hacking\tools\mcp\probe.py")   # 薄 harness 验证
  ↓ 轮询 get_run_status
读 logs/mcp/<run_id>/status.json + run.log + 产物图
```

对比旧流程：改完必须重启 GUI（`main_debug.py`）、人工点开始、出问题再贴日志。
现在重载几秒完成，验证由 harness 执行，产物落在本次运行目录里。

## 产物在哪

| 入口 | 产物目录 |
|---|---|
| MCP `run_task` | `logs/mcp/<run_id>/`（runner 注入，含 `status.json` / `run.log`） |
| GUI / CLI | `logs/mcp/attempts/<时间>_<任务名>/`（`AttemptSupport.attempt_dir`） |

`AttemptSupport.attempt_dump(tag, texts)` 把 `JPEG q95 + 4:4:4` 的帧和 OCR 文本写进
当次目录，最多 20 张；`dump_debug` / `dump_unknown` 的老路径不变，那是给人翻的。

## 任务契约（代码已适配）

MCP 提交的**文件**必须有：

```python
class Task(BaseTask):
    def reset_to_start(self): ...      # 返回 False = 运行失败
    def verify_start_state(self): ...  # 必须 assert 或返回 True
    def run(self): ...                 # 返回 False = 运行失败
```

已注册任务由 GUI 直接调 `run()`，不经过这些钩子，所以两条路径都要照顾：

- `src/tasks/attempt.py` 的 `AttemptSupport`：识别入口（`mcp_driven`）、产物目录、
  存证、默认钩子（不改状态的任务用 no-op）。
- `BaseGirlsTask`：`reset_to_start` = 回大厅（`recover_to_lobby`）、
  `verify_start_state` = 断言「赛事 + 训练」都在屏幕上。
- `TrainingTask` 继承上面的严格基线：MCP 里必须从大厅开始。
- `QuickMatchTask` / `AutoBattleTask` **不回大厅**：比赛打到一半点返回等于毁局，
  它们只断言「画面是可识别场景」。
- GUI/CLI 跑 `TrainingTask` 时会在 `run()` 开头自动回大厅，失败只告警不中止
  （用户手动起任务时不该被卡住）。

### 回大厅为什么不无条件点返回

`recover_to_lobby` 的退出阶梯是：**顶部 20% 的 ×/关闭/返回/箭头字符** → 右上角返回箭头 →
ESC → 放弃并存证 `recover_failed`。遇到比赛/小游戏标记（`VS`/`暂停`/`比分`/`战术板`）会
直接放弃并存证 `recover_blocked`，因为盲点返回会打开暂停菜单甚至误触技能。

2026-10-30 实测踩到的三个坑：

1. **退出控件不都在右上角**。「选队长 / 选择队友」页的退出按钮在左上角，而且 OCR 会把它
   认成 `《`（有时是 `×`）。只点右上角箭头会连点 6 次画面纹丝不动，`reset_to_start`
   直接判失败。所以 `EXIT_TEXTS` 里同时收了 `×`/`《`/`<`/`〈` 这些箭头误认字符。
2. **用户动键鼠会让重置失败**。`GirlsInteraction` 的用户优先策略会暂停所有点击，等用户
   静默 `user_idle_wait` 秒。MCP 跑的时候你如果还在点窗口，`reset_to_start` 会因为
   「点了没变化」而超时失败——这不是 bug。MCP 跑之前先停手。
3. **连点没变化就要换手段**。同一个界面连续两次点击后 OCR 指纹不变，说明这个控件对本页
   无效，继续点只是浪费轮次；现在会切到 ESC，再不行就带着存证放弃。

## Harness

| 文件 | 用途 | 会改游戏状态 |
|---|---|---|
| `tools/mcp/probe.py` | 回大厅 + 报顶栏/OCR，改完代码的第一只探针 | 只点返回箭头 |
| `tools/mcp/training_smoke.py` | 借 TrainingTask 跑 30 拍 | 会 |
| `tools/mcp/quickmatch_tick.py` | 借 QuickMatchTask 跑 10 拍 | 会 |

`tools/mcp/_harness.py` 的 `borrow()` 负责借出已注册实例并临时设置
`_enabled / running / current_task / artifact_directory`，退出时还原——不复制业务代码。
步数可用 `OK_MCP_SMOKE_STEPS` / `OK_MCP_QUICK_TICKS` 调。

## 适配后要遵守的约束

1. **管理员权限**：MCP server 必须以管理员启动，否则点击全被 UIPI 拦截。
2. **窗口必须可见**：Unity 全遮挡时 WGC 零帧，MCP 不改变这条。
3. **只有一个执行者**：一次只能有一个 run 持有 executor，`stopping` 不是终态。
4. **helpers 不会被热重载**：`reload_tasks` 只刷新已注册任务模块；`src/tasks/training/*`
   这类被任务 import 的模块如果改了，需要重启 runtime（`exit_process` 有官方出口）。
5. **看图仍是稀缺资源**：AGENTS.md 的「一个会话最多 5 张图」不变。优先让 harness
   assert 并以文字返回结论，只在核对坐标时读一张图。
6. **识别层回归仍走 `check_handlers.py --cache`**：它不用起 runtime，秒级，MCP 不替代它。

## 常用命令

```powershell
# 识别层回归（不需要游戏/MCP）
$env:PYTHONIOENCODING='utf-8'; .venv\Scripts\python.exe tools\check_handlers.py --cache

# 起 MCP 开发服务器
.venv\Scripts\python.exe -m ok mcp --config src.config:config

# 正常 GUI
.venv\Scripts\python.exe main_debug.py
```
