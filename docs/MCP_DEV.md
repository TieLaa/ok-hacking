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

每个**已注册任务**都有一个 MCP 入口文件，业务逻辑一律不复制：

| 文件 | 对应任务 | 说明 |
|---|---|---|
| `tools/mcp/training.py` | TrainingTask | 跑完整一轮；`OK_MCP_STEPS` 可限步；**不支持删档**（见下方红线） |
| `tools/mcp/quickmatch.py` | QuickMatchTask | 跑 N 拍（`OK_MCP_TICKS`，默认 10），不回大厅 |
| `tools/mcp/autobattle.py` | AutoBattleTask | 骨架任务跑 N 拍，只读画面 |
| `tools/mcp/input_test.py` | InputTestTask | 点一次并断言界面变化 |
| `tools/mcp/my_onetime.py` | MyOneTimeTask | 配置演示，冒烟用 |
| `tools/mcp/probe.py` | — | 回大厅 + 报顶栏/OCR，改代码后的第一只探针 |
| `tools/mcp/summarize_run.py` | — | 把一次 run 的 `run.log` 数成表（写运行记录用，只读；`-o` 写 UTF-8） |

`_harness.borrow()` 借出已注册实例并临时设置 `_enabled / running / current_task /
artifact_directory`；`run_registered()` 额外支持本次运行的 config 覆盖（跑完还原）。

🚫 **红线：脚本永不删档**（2026-11-02 用户口径）。开训练消耗的专注在删档时
**不返还**，所以「删档」是纯亏损操作。「训练确认」弹窗只会点「继续训练」，
「新一局删档」开关已从 `config_spec` 删除 —— 旧配置里残留 `true` 也不会被读。
`tools/mcp/run_rounds.py` 连跑多轮时需要人工开新局，脚本本身不提供删档入口。

## 适配后要遵守的约束

1. **管理员权限**：MCP server 必须以管理员启动，否则点击全被 UIPI 拦截。
2. **窗口必须可见**：Unity 全遮挡时 WGC 零帧，MCP 不改变这条。
3. **只有一个执行者**：一次只能有一个 run 持有 executor，`stopping` 不是终态。
4. **helpers 不会被热重载**：`reload_tasks` 只刷新已注册任务模块；`src/tasks/training/*`
   这类被任务 import 的模块如果改了，需要重启 runtime（`exit_process` 有官方出口）。
5. **看图仍是稀缺资源**：AGENTS.md 的「一个会话最多 5 张图」不变。优先让 harness
   assert 并以文字返回结论，只在核对坐标时读一张图。
6. **识别层回归仍走 `check_handlers.py --cache`**：它不用起 runtime，秒级，MCP 不替代它。

## PostMessage 能不能用（2026-10-30 结论：仍然不行）

2.0.20 修了 PostMessage 的鼠标坐标（`update_mouse_pos` 按子窗口选 target、按钮消息复用
同一套 client 坐标），但**对本游戏无效**。同一次运行里的对照实验：

| 方式 | 结果 |
|---|---|
| `PostMessageInteraction.click(赛事)` @(1180,630) | 无反应，OCR 没有出现「玩法目录」 |
| 同一坐标用真实鼠标（GirlsInteraction）点一次 | 打开「玩法目录」，OCR 命中 |

两次坐标完全相同、`pm_target_hwnd` 就是顶层窗口 1182186、`pm_mouse_pos` 也正确，
所以**不是坐标问题**，是 Unity 侧不消费投递的鼠标消息。结论保持不变：
`src/config.py` 的 `windows.interaction` 只留 `GirlsInteraction`。

当时的对照脚本 `tools/mcp/postmessage_probe.py` 已在 2026-11-09 随一次性实验脚本一起删除；
要复现这组对照，按上表两个坐标手动各点一次即可，判据只看「玩法目录」有没有出现。

## 改了 helper 怎么换新 runtime（2026-11-02 实测）

`reload_tasks` 只重编**任务自己那个文件**（`task_manager._load_source_module` 用
`spec_from_file_location` 加载任务路径），`src/tasks/training/handlers/*`、`parsers.py`
这类 helper **不会**跟着重载。所以改 handler 判据后必须换进程，别指望 `reload_tasks`。

换进程的三条路，按「能不能用」排序：

| 手段 | 什么时候能用 | 说明 |
|---|---|---|
| `client.py --exit` | runtime 空闲时 | 有时只是排队，进程不真退（2026-11-02 连发两次都没退） |
| 管理员 shell 里 `restart_mcp_runtime.ps1` | 你的 shell 是管理员 | 最干净 |

**先确认游戏是不是管理员进程**（今天就不一定是）：

```powershell
try { (Get-Process -Id <游戏pid>).MainModule.FileName } catch { '非管理员读不到' }
```

读得到 → 游戏不是管理员 → **非管理员 runtime 也能点进去**（UIPI 只拦「低权限进程→高权限窗口」），
直接在自己 shell 起即可，不需要 UAC：

```powershell
Start-Process -FilePath .venv\Scripts\python.exe `
  -ArgumentList '-m','ok','mcp','--config','src.config:config' `
  -WorkingDirectory 'D:\workspace\ok-hacking' -WindowStyle Hidden `
  -RedirectStandardOutput logs\mcp\runtime_stdout.log `
  -RedirectStandardError  logs\mcp\runtime_stderr.log
```

⚠️ 派生子进程那条路走不通：从 runtime 里 `subprocess.Popen(powershell, DETACHED_PROCESS)`
起的进程**根本不执行**（2026-11-02 用标记文件验过），所以别写成「自己排程重启自己」。

## 常用命令

```powershell
# 识别层回归（不需要游戏/MCP）
$env:PYTHONIOENCODING='utf-8'; .venv\Scripts\python.exe tools\check_handlers.py --cache

# 起 MCP 开发服务器
.venv\Scripts\python.exe -m ok mcp --config src.config:config

# 正常 GUI
.venv\Scripts\python.exe main_debug.py
```
