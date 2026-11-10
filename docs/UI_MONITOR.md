# 监控页

一屏看完当前这一局的进度，并且只放**两颗按钮**：一颗管执行器（＝起始页的【开始(F9)】），
一颗管自动训练任务（＝任务页任务卡的【开始】）。其余都是只读展示和落盘设置。

| 文件 | 作用 |
| --- | --- |
| `src/ui/MonitorTab.py` | 取数据（`_collect`）、动作（`toggle_connection` / `toggle_auto_training`）、热重载 |
| `src/ui/monitor_view.py` | 纯渲染层：只认 layout 字典 + data 字典，不碰业务对象 |
| `configs/monitor_layout.json` | 布局（区块顺序、高度、文案、宽度比例）；改完存盘 0.5 秒内生效 |
| `assets/ui/monitor.{dark,light}.qss` | 样式；改完存盘即生效，跟随应用主题切换 |

## 界面结构

```
┌ 第一栏（connection）  ● PC端 已连接：xxx 1548x871      [开始/暂停] [截图] [重新检测]
├ 第二栏（captain）      ☐ 指定队长 [▼ 真崎辉世]  ☐ 刷到目标分 [目标分] ☐ 允许吃专注药  快速比赛●
├ 主状态卡（hero）      当前进度 第N周 星期X | 精力 120/130 | 六维 | 分数预测 | 技能
├ 卡片（cards）         比赛倒计时 / 周末计划 / 今日商店 / 失败率 …
└ 最近动作（timeline）  时间 | 事件 | 详情 | 结果
```

## 改布局

只有一条路：**改 `configs/monitor_layout.json`**（2026-11-10 删掉了界面里的编辑模式）。

- 文件 `mtime` 一变 → 下次轮询（0.5 秒）重建整个页面；
- 布局里的 `_hidden: true` 仍然被尊重（可以手工藏掉一个区块），但界面里没有对应开关；
- 区块顺序＝`sections` 数组顺序，`height` 是固定像素高，hero 的 `right_ratio` 是左右比例。

各区块支持的键：

| type | 键 |
| --- | --- |
| `connection` | `height` `title` `meta` `show_run` `run_text` `stop_text`（`starting_text` 已是遗留键，不再使用） |
| `captain` | `height` `label` `hint` `show_quick_match` `show_auto_training` `start_text` `pause_text` |
| `hero` | `height` `kicker` `title` `fallback` `right_ratio` `stats[]`（`label`/`value`/`suffix`/`bar`/`bar_key`/`bar_width`） |
| `cards` | `height` `items[]`（`id`/`title`/`rows[]`，每行 `[标签, 模板]`） |
| `timeline` | `title` `max_rows` `time_width` `event_width` |

文案里的 `{key}` 是数据占位符（见下方「可用字段」）；`fallback` 是整句兜底，
数据不全时优先显示它。

## 第一栏【开始 / 暂停】＝ 起始页的【开始(F9)】

```python
def toggle_connection(self):
    if not og.executor.paused:
        og.executor.pause()                       # 在跑 → 暂停
    else:
        og.app.start_controller.start()           # 没跑 → 连接 + 启动执行器
```

与框架 `ok/ui/qt/start/StartCard.py:67` 的 `clicked()` **逐字一致**：

| 项 | 口径 |
| --- | --- |
| 判据 | 只有 `og.executor.paused`（`⇄ 开始 ▶ / 暂停 ‖`） |
| 任务 | **不 enable 任何任务** —— 执行器跑起来，任务由第二栏负责 |
| 连接/截图会话 | 归 `start_controller` 自己管，这里不判断、不抢前台 |
| 切页 | 不切。框架的「跳到任务页」只在 `stackedWidget.currentIndex()==0`（起始页）时触发，监控页索引必然 >0 |
| 防连点 | 无（与 F9 一致）。接入期间框架会弹 `StartLoadingDialog`：**铺满主窗口的 30% 灰遮罩 + 倒计时卡片**，按钮既看不到也点不到 |

> **接入那 5~30 秒监控页是被盖住的**，这是框架行为、属预期。想看数据要等接入结束。

2026-11-10 删掉了这条链路上所有自建逻辑：判据 `_connected()`（截图通道 + paused 混判）、
「停止＝断开游戏」（停任务 + 暂停执行器 + 关截图会话）、抢前台、`_connected()` 相关的
测试。现在第一栏只剩上面三行。

## 第二栏【开始 / 继续 / 暂停】＝ 任务卡的【开始】

```python
if enabled and paused:   task.unpause()                        # 框架此时显示 Resume
elif running:            task.pause()
else:                    og.app.start_controller.start(task)   # 刷设备/拉游戏，连上后 enable 本任务
```

与框架 `ok/ui/qt/tasks/TaskCard.py:118`（`start_clicked`）+ `:144`（`pause_clicked`）同源。

| 项 | 口径 |
| --- | --- |
| 判据 | `task.enabled / paused / running` 三态（`training_active` 驱动按钮文案） |
| 未连接 | **不判断**。`start_controller.start(task)` 自己刷设备、必要时拉起游戏 exe，连上后 enable |
| 防连点 | 无（与任务卡一致） |
| 抢前台 | 不做（删掉了；真抢前台在执行器线程里由 `GirlsInteraction` 每次点击前做） |
| 停止 | 没有单独的【■】按钮（2026-11-10 删除）。要停就是再点一次【暂停】 |

**副作用（框架设计，不是 bug）**：点第二栏【开始】→ `start_controller.start(task)`
→ `executor.start()` → `communicate.executor_paused.emit(False)` → **第一栏跟着显示【暂停】**。
启动任何任务都必然启动执行器，所以第一栏跟着变是事实。反方向不成立：第一栏【开始】
不 enable 任务，第二栏保持【开始】。

## 与框架按钮的对应关系

| 监控页 | 框架里的原型 | 语义 |
| --- | --- | --- |
| 第一栏【开始/暂停】 | 起始页【开始(F9)】`StartCard.clicked` | 执行器总开关 |
| 第二栏【开始/继续/暂停】 | 任务卡【开始】`TaskCard.start_clicked` | 这一个任务的开关 |

## 队长选择

- 勾选框**默认不勾**：不勾＝不接开局，只跑你手动开好的局；
- 勾上时用下拉**当前选中**的角色（不是列表第一个）；
- 下拉按流派分组；`队长下拉选择` 会落盘，没勾选时显示的是它；
- **改队长会清掉 CLI/MCP 注入的 `_launch_queue`**（"本次注入的安排作废"），
  配置派生的队列不受影响；
- 流派来源：配置里填了队长 → 直接查表；留空 → 进训练后点一次日历，按「XX的训练计划」
  里队长的流派跑。

## 快速比赛 / 刷到目标分 / 允许吃专注药

| 控件 | 作用 |
| --- | --- |
| 快速比赛 | 只管 `QuickMatchTask`（全国大赛/圣山战/竞技场）。开＝只 enable（**执行器停着只提示「按第一栏【启动】后生效」，不替你启动**，2026-11-10 改）；关＝只 disable，绝不动执行器。开关状态落盘 |
| 刷到目标分 | 勾上＝把队列配成「指定队长:目标分」，反复练到某局结束评分 ≥ 目标分。没队长时勾选会提示并自动关掉 |
| 允许吃专注药 | 映射到配置项 `自动恢复精力`：勾上＝专注低于「恢复精力下限」时进恢复页喝 +30/+60 饮料（**绝不用钻石**）；取消＝不进恢复页，宁停不耗 |

`QuickMatchTask` 是 `TriggerTask`，**只有执行器没被暂停时才会被调度**
（`TaskExecutor.execute()` 里 `if self.paused: 只跑 job`）。所以第一栏【暂停】时它也不跑，
不需要额外开关。

## 可用字段

`_collect()` 产出的 `data` 字典（layout 里的 `{key}` 直接取这些键）：

| 键 | 含义 |
| --- | --- |
| `state` | `idle` / `queued` / `paused` / `running` |
| `week` `day` `stamina` `stamina_max` `stamina_pct` `coin` | 顶栏读数 |
| `captain` `captain_memory` `style` | 勾选/下拉记忆/当前流派 |
| `paused` | **执行器**是否暂停（第一栏判据） |
| `training_running` `training_active` `training_paused` | 任务状态（第二栏判据） |
| `attrs` `score` | 六维 / 分数预测（六维不齐时 `score` 为 `None`） |
| `skill_color` `skill_gold` `skill_white` `skill_gained` `skill_last` | 技能数（配置里是开局自带，`gained` 是本局三选一累加） |
| `quick_enabled` `score_stop` `score_target` `stamina_allowed` | 第二栏设置的回填值 |
| `action` `uptime` `task_hint` `timeline` | 最近动作 / 运行时长 / 任务名 / 时间线 |
| `match_countdown` `weekend_plan` `picnic_threshold` `buy_strategy` `shop_today` `fail_rate` `stuck` | 卡片里的派生信息 |
| `device_title` `device_meta` | 第一栏的窗口与分辨率 |

**分数预测**：`六维总和 + 75×彩 + 50×金 + 25×白`（每角色开局恒 1 彩 + 1 白，见
`docs/score-model`）。六维缺任何一项都不算分，显示 `—` —— 拿 4 项求和会给出偏低的假分。

## 防闪：`_sticky` 缓存

`info` 是任务每拍覆盖写的 UI 通道，OCR 漏读一拍就会短暂变 `None`，直接渲染就是
「好值 → — → 好值」。`_sticky(key, value)` 只对**展示**生效：这一拍为 `None` 就沿用上一拍。
新一局 `info` 被清空时，这份缓存也一起清掉，免得新角色先显示上一个人的数。
业务判据读的是 `top_bar()` 的返回值，`None` 语义不变。

## 热重载

轮询间隔 500ms（`QTimer`）：

| 变化 | 动作 |
| --- | --- |
| `configs/monitor_layout.json` 的 mtime 变了 | 重读布局 → 重建整页 → 重新套样式 |
| 当前主题对应的 QSS mtime 变了 | 只重新 `setStyleSheet` |
| 应用主题切换（`qconfig.themeChanged`） | 换成另一套 QSS |

轮询的每一拍同时刷新数据。第二栏点完按钮会**立刻**再刷一次（`_update_data()`）；
第一栏不主动刷 —— 它只改执行器状态，界面靠下一拍（0.5 秒内）跟上。

## 已知边界

1. **窗口必须可见**：完全遮挡/最小化时 WGC 零帧，页面上什么都不会变。
2. **接入期间监控页被框架遮罩盖住**（见第一栏一节），这是预期行为。
3. 数据来自 `task.info` 与顶栏 OCR：漏读会靠 `_sticky` 顶住，连续漏读就可能停在旧值。
4. 改布局/样式只影响监控页；业务判据全部在 `src/tasks/training/`，与这里无关。
