# 监控页（布局可调）

启动后默认落在这一页（`MonitorTab._select_on_startup` 等主窗口 showEvent 后切过来）：
打开就知道第几周、专注多少、现在在做什么、最近点了什么。
**结构和样式都能直接改**：在页面里点「编辑布局」，或者手改 JSON / QSS，存盘即生效。

想改回启动落在「截图方式」页：把 `src/ui/MonitorTab.py` 顶部 `START_ON_MONITOR` 改成 `False`。

- 代码：`src/ui/MonitorTab.py`（取数据、定时刷新、热重载、编辑落盘）
- 渲染：`src/ui/monitor_view.py`（只认 layout + data，不碰业务）
- 布局：`configs/monitor_layout.json`
- 样式：`assets/ui/monitor.dark.qss` / `assets/ui/monitor.light.qss`

## 在界面里改（推荐）

1. 进「监控」页，点连接卡右边的 **编辑布局**。
2. 编辑态下每个区块右上角出现三个按钮：
   - `↑` / `↓`：和相邻区块换位置
   - `✕`：隐藏这一块（摘要卡是逐张隐藏的）
3. **双击任意文字**直接改：卡片标题、每行的左右文字、周目标题、四项数值的标签和值。
   改行值时可以用 `{字段}` 占位，例如 `{coin}`。
4. 隐藏的区块会收进页面底部的「已隐藏：…」，点 `＋ 名称` 恢复。
5. **拖动主状态卡中间那条竖线**：编辑态下它会变宽（手感区 7px，视觉还是细线），左右拖动可以调整右侧按钮栏的宽度，松手自动保存。范围 160–520px。
6. 点 **完成** 退出编辑态。每次操作都会立刻写回 `configs/monitor_layout.json`。

连接卡不能隐藏（它是编辑入口，也显示设备状态），但可以上下移动。

## 手改 JSON（同样的文件）

四个 section 类型，`sections` 数组的顺序就是页面从上到下的顺序：

| type | 作用 | 关键字段 |
|---|---|---|
| `connection` | 连接状态条 | `height` `title` `meta` `run_text` `stop_text`（图标随状态走：`▶ 启动` / `■ 停止`） |
| `captain` | 队长选择（勾选框 + 分组下拉）+ 自动训练开关 | `height` `label` `hint` `combo_width` `start_text` `pause_text` |
| `hero` | 周目 / 专注 / 文案 | `stats` 数组、`title`、`fallback` |
| `cards` | 一行摘要卡 | `items[].title` + `rows`（每行 `["左","右"]`） |
| `timeline` | 最近动作 | `max_rows` `time_width` `event_width` |

> 2026-11-02 清理：hero 段原来的 `now` / `now_label` / `start_text` /
> `pause_text` / `stop_text` 是**死配置** —— `_build_hero` 一个都没读，
> 改了 JSON 也没反应。已从 JSON 和 `DEFAULT_LAYOUT` 里删掉。
> 队长栏的 `stop_text` 同理（只剩 `start_text` / `pause_text` 两个文案）。

「最近动作」每行 4 列：**时间 / 域（入口·比赛·训练…）/ 本拍识别到的文字 / 结果**。

- 详情列是这一拍 OCR 原文的去重摘要（`src/tasks/training/flow.py` 的 `summary_texts`），
  最多 8 条、总长 50 字符，超出截断加 `…`；看得到「当时屏幕上有什么」，比只写 handler 名好复盘。
- 步数和 handler 全名不再占列，作为 `step` / `handler` 键留在事件里，需要时加一列即可。
- `result` 目前恒为「成功」：handler 抛异常时不会写事件，这列暂时没有区分度。

## 队长选择

- 勾选框 = 有没有队长；下拉按流派分组，角色表来自
  `src/tasks/training/constants.py` 的 `STYLE_BY_NAME`。
- **下拉先只列「训练流 / 扭蛋流」**（2026-11-08 用户口径：其它的先隐藏）。
  白名单是 `constants.CAPTAIN_DROPDOWN_STYLES`，加回来只需往元组里加流派名；
  **业务规则没变**，商店流 / 小游戏流的行程、阈值照旧生效。
  例外：配置里正跑着的队长如果属于被隐藏的流派，`MonitorTab._captains` 会把
  它那一组补回下拉（否则回填不到，一勾勾选框就被换成第一个训练流角色）。
- 下拉里的 `── 训练流 ──` 这类标题只是分组，点不中（会自动退回上一个角色）。
- 勾掉勾选框 → `指定队长` 清空 **并且清掉运行期的流派**（`task._style`）
  → **无队长模式**：只跑已有局，遇到开局页面就停
  （口径见 `docs/RULES.md` 第 0.5 节）。
- 改动直接写进 `TrainingTask.config`，即 `configs/TrainingTask.json`，立刻生效，不用重启。
- 勾选框**默认不勾**（2026-11-01 修正）：旧版默认勾上，而下拉第 0 项是
  「── 训练流 ──」分组标题 —— 用户一进页面就等于「指定了第一个训练流角色」，
  扭蛋流被写成训练流。现在默认不勾，等 `_sync_captain` 按配置里的实际值回填。
- 勾选时用下拉**当前**选中的角色，不是「列表第一个」。

## 第一栏 / 队长栏上的按钮

| 位置 | 控件 | 作用 |
|---|---|---|
| 连接条右侧 | **▶ 启动 / ■ 停止** | **总开关（执行器归它管）**：启动 = 接入游戏（连设备、必要时拉起游戏）+ `executor.start()`；停止 = 断开（**两个模块开关都复位** + 暂停执行器 + 释放截图会话） |
| 连接条右侧 | 重新检测 | 重扫窗口并刷新页面数据 |
| 队长栏 | **▶ 开始 / ‖ 暂停** | **只管自动训练脚本自己**：开始 = `task.enable()`（执行器停着就**顺手**唤醒）；暂停 = `task.disable()`，**不碰执行器**。**没接上游戏时按【开始】会自动接入**（等同第一栏【启动】），接入期间显示【启动中…】 |
| 队长栏右侧 | 快速比赛 开关 | **只管快速比赛模块**（养成之外的比赛：全国大赛 / 圣山战 / 竞技场）：开 = enable `QuickMatchTask` + **顺手**唤醒执行器（`executor.start()`）；关 = 只 disable 它自己，**不碰执行器**。第一栏【停止】会把它复位成「关」（实时同步任务状态） |
| 队长栏最右 | ✎ | 进出编辑布局模式 |

### 为什么要拆成两个（2026-11-01，2026-11-02 补完）

旧版第一栏是「开始 / 暂停」，按的是 `executor.pause()`，第二栏是「▶ 开始 / ■ 停止」：

- 「暂停」按住的是**整个执行器**，第二栏的自动训练跟着一起被按住 ——
  表现成「第二栏的开关带动第一栏的开关」，逻辑正好反了；
- 「停止」只停了任务，**没有断开游戏**，脚本还挂着游戏窗口不放。

现在：第一栏 = 总开关（接入 / 断开游戏），第二栏 = 自动训练脚本的开关。
两者互不带动；第一栏点「停止」时会把第二栏复位成「开始」（未启动状态）。

### 三个判据不能混（2026-11-02 修正，这是上次没修干净的地方）

88dd7da 那次只把**文案**拆开了，**判据**还串着。这次改的是判据：

| 按钮 | 判据 | 曾经错在哪 |
|---|---|---|
| 第一栏【启动/停止】 | `connected`：**会话在跑没有** = `capture_method` 在且 `connected()` 且 **`executor.paused` 为假** | ① 更早用 `not executor.paused`，第二栏点【暂停】里的 `task.unpause()` 会调 `executor.start()`，把第一栏从「启动」顶回「停止」；② 后来只看 `capture_method`，**app 一启动框架就自己建好了 WGC 截图会话** → 一进页面就显示【停止】，看着像「默认已经开始了」 |
| 第二栏【开始/暂停】 | `training_active` = `enabled and running` | 旧判据 `running or enabled`（`enabled` 含「在队列里等」）→ 点完【开始】立刻变【暂停】，其实一帧没跑 |
| 第二栏【开始】前置条件 | `connected` 为假 → **主动接入**（2026-11-02 改）；为真 → 直接 `enable()` | ① 更早不管连接状态就 `enable()` → 任务在队列里躺着，执行器还停着，永远不动；② 中间版本「没连上就只打一条 `logger.warning` 然后 return」→ 界面上一点变化都没有，用户看到的是「点了毫无反应」 |

### 第二栏【开始】没连上就主动接入（2026-11-02 口径）

用户口径：「第二栏按下开始的时候，如果没有连上游戏，就主动启动」。

旧版那条分支是死胡同：

```python
if not self._connected():
    logger.warning("监控页：还没接上游戏，先按第一栏【启动】再开始自动训练")
    self._update_data()
    return      # ← 点下去界面一动不动，日志里才看得到原因
```

现在 `MonitorTab._auto_start()` 走第一栏 `_connection_start()` 的同一条链路
（`start_controller.start(task)`：刷新设备 → 必要时拉起游戏 exe → 等窗口稳定 →
`_mark_task_enabled(task)` → `executor.start()`），用户不用记住按钮顺序。

两个坑：

1. **这里不能自己再 `task.enable()`**。`start_controller` 自己会 enable 这个 task
   （`_mark_task_enabled`）；提前 enable 只会让任务躺在队列里，界面也不动
   —— 又变回「点了没反应」。
2. **接入是异步的**（`start()` 只是 post 到 handler 线程，几秒到几十秒）。
   期间再点一次要用 `start_controller.starting` 挡掉，否则 handler 队列里会堆
   好几个 `do_start`，重复拉游戏窗口。同时第二栏显示【启动中…】（`connecting`
   数据键，`FluentIcon.SYNC`），否则那几十秒里按钮没有任何变化，还是像没反应。

**`task.unpause()` 是第二栏的毒药**（2026-11-02 删掉）：

```python
# ok/task/task.py:1224
def unpause(self):
    self._paused = False
    self.executor.start()   # ← 把整个执行器从暂停里放出来
```

第二栏从来没调过 `task.pause()`，所以根本不需要 `unpause()`。
留着它就等于「停自动训练」顺手撤销了「断开游戏」：
第一栏刚【停止】（执行器已 pause、截图会话已释放），第二栏再点【暂停】
就把执行器唤醒了 → 第一栏自己跳回【停止】，而且执行器去取帧时
`capture_method` 已经是 `None` → 拿不到帧。

> `task.disable(); task.unpause()` 这个写法是抄框架 `TaskCard.stop_clicked()` 的。
> 框架里成立是因为框架的 stop 语义是「停掉这张任务卡」；我们的第二栏是
> 「暂停自动训练」，语义不同，副作用也不同。

### 启动后第一栏不能显示成「已经在跑」（2026-11-02 第二次修正）

用户口径：「进去的时候默认就开始了，按钮应该是 ■停止，而不是 ▶停止」。

日志证据（`logs/ok-script.log`，app 刚打开、用户还没点任何按钮）：

```
RefreshAdb DeviceManager:update_pc_device ... connected: True
RefreshAdb windows_graphics:start_or_stop start WGC capture
```

框架启动时会自动刷新设备并**建好截图会话**，所以 `capture_method` 一开始就有对象
—— 旧判据只看它，第一栏进来就是【停止】。执行器启动时是 `paused=True`，
把「执行器没暂停」加进判据，才能把「截着图」和「真在跑」分开。

**图标必须跟文案走**（同一次修正）：第一栏 `▶ 启动` / `■ 停止`，
第二栏 `▶ 开始` / `‖ 暂停` / `↻ 启动中…`。只在状态翻转时 `setIcon`，不拿 `icon()`
和枚举比（PySide 每次返回新 QIcon，比出来永远「变了」）。

### 断开游戏时第二栏要复位成【开始】

用户 2026-11-02 口径：「断开连接」的链路要收尾干净 —— 断开后第二栏必须回到
**未启动状态**（显示【开始】），否则用户会看到一个「暂停」按钮，
但游戏已经断开、点了也没用。

实现上有个坑：`task.disable()` 是**同步**生效的（`_enabled` 立刻变 False），
但 `task.running` 要等任务线程跑完当前拍才置 False —— 中间有几秒窗口。
所以判据要用 `enabled and running`（不是光看 `running`），
断开后按钮立刻变【开始】，不用等任务线程收尾。

还有个竞态：`_close_capture_async` 旧版把 `capture_method = None`（属性赋值）
也放在后台线程里，于是断开后的几秒内 `_connected()` 仍为真 —— 第一栏还显示
【停止】、第二栏也以为还能开始。现在**先同步置 None、再后台 close**
（close 要等正在取的那一帧，最多 4 秒，不能放 UI 线程）。

## 执行器 = 唯一运行基础：谁能动它、谁只能「顺手」（2026-11-02）

用户口径（两条，合起来就是这个页面的总规则）：

> 「executor 可以管【开始/暂停】【快速比赛】两个开关，执行器是被【启动】拉起来的，
> 而不是被【开始】拉起来的。【开始/暂停】只负责管自己自动训练，但是点了【开始】
> 如果执行器没起来就顺手拉起来一下，只是顺手而不是必须。」
>
> 「快速比赛也是依靠执行器，所以打开的时候也顺手打开执行器，但是**关闭执行器
> 都不是它们该管的事**。反过来，执行器关闭的时候，【开始/暂停】【快速比赛】
> 都要关掉，因为运行的基础关掉了。」

| 控件 | 开 | 关 |
|---|---|---|
| 第一栏【启动/停止】 | **接入**（刷设备 / 必要时拉游戏 exe）+ `executor.start()` + enable 自动训练 | **两个模块开关复位** + `executor.pause()` + 释放截图会话 |
| 第二栏【开始/暂停】 | enable 训练任务 + **顺手** `executor.start()`（没接上则走接入链路） | 只 `task.disable()` —— **不碰执行器** |
| 快速比赛 开关 | enable `QuickMatchTask` + **顺手** `executor.start()` | 只 `task.disable()` —— **不碰执行器** |

要点：

- **「顺手」的实体是 `_wake_executor()`**：只做 `executor.start()`（把 `paused`
  置 False）。刷新设备、拉游戏 exe 这些**接入**动作只归第一栏【启动】；
  游戏没开着时唤醒也拿不到帧，用户还是得按【启动】。
- **没人能动 `executor.pause()`，只有第一栏【停止】能**。第二栏【暂停】和快速比赛
  拨「关」只 disable 自己的任务 —— 这正是「两个模块无关」在代码里的落点。
- **运行基础关了，挂在上面的开关必须跟着关**：`_connection_stop()` 先
  `_stop_auto_training(reset_button=True)` 再 `_stop_quick_match()`，然后才
  `executor.pause()`。顺序不能反（任务线程下一拍才退出，先 pause 会多跑一拍）。

### 为什么旧版「快速比赛」开关像跟第二栏绑在一起

旧版只是 `task.enable()`，而 **`TriggerTask` 只有执行器没被暂停时才会被调度**：

```python
# ok/task/TaskExecutor.py:execute()
if self.paused or self._exclusive_owner is not None:
    ...          # 只跑 job，不调度任何任务（trigger 也不跑）
```

于是开关拨到「开」什么都不动，非得有人先按第二栏【开始】把执行器拉起来 ——
用户看到的就是「快速比赛跟第二栏的开始绑在一起」。现在开关自己会顺手唤醒执行器，
不必再看第二栏的脸色。

### 两个模块的分工（别混）

- **自动训练**（第二栏）自己带比赛逻辑：养成流程里的比赛由 `TrainingTask`
  自己跳过 / 加速；
- **快速比赛**（`QuickMatchTask`）管**养成之外**的比赛：全国大赛、圣山战、
  竞技场……用户手动进的那些局。常驻 `TriggerTask`，`next_task()` 里 onetime
  任务优先，所以养成跑着时它自动让位，不会抢点击。

## 流派从哪来

**只由队长决定**（`docs/RULES.md` 第 0 节）。监控页勾了队长 → 配置里的
`指定队长` 有值 → `flow.resolve_style()` 直接查表定流派，**不管是不是大厅开局**
（2026-11-01 修正）。

旧逻辑只在 `is_lobby()` 时才套用配置队长，理由是「接手别人的局时实际队长可能不是
配置里那个」。但**日历只在训练日弹**，接手的局如果当前是周末（非训练日）就永远读不到
实际队长 → `_style` 一直空着 → `style` 兜底成「训练流」→ 扭蛋流跑成训练流
（2026-11-01 19:19 实跑日志）。

## 分数预测（主状态卡右半边）

```
分数预测                              2613
技能  彩 [1]  金 [3]  白 [4]
投射  999        弹跳  850
运控  700        体能  900
防守  620        意识  1100
```

- 公式来自 `docs/score-model/README.md`：`六维总和 + 75×彩 + 50×金 + 25×白`
- 六维顺序固定为游戏底部面板的顺序（**列优先**：左列 投射/弹跳/运控，右列 体能/防守/意识），
  数值由 `parsers.read_attribute_values` 读，`parsers.refresh_attr_info` **合并**写进 `info["attrs"]`
- **刷新点：进入休息室时拍一次快照（2026-11-03 用户口径）**
  - `handle_training_day` 的**决策拍**（页面淡入后的第一拍）调 `parsers.snapshot_lounge()`：
      **六维 + 精力 + 零用钱** 三项一起拍；`_lounge_snapshot_done` 保证同一页只拍一次，
    离开这一页由 `handlers/__init__.py:LEAVE_RESETS` 清掉，第二天进来重新拍
  - 其它页面不读六维：校门口 / 地图根本没有这个面板；周末加的是零碎属性
    （属性+10、精力+…），回头累加既对不上面板也没意义
  - 科目页只在选科目时用 `info["attrs"]`（当前值低的科目优先），**不再**承担刷新
  - 周 / 星期 / 专注 / 零用钱 另外还由 `BaseGirlsTask.top_bar()` 每拍解析
    （顶栏一直在屏幕上）；零用钱取法复用 `parsers.read_money()`（周末商店/游戏厅本来就在用）
  - **任务停着的时候一个字段都不会动**：这些都是 OCR 读游戏画面得来的，没拍就没数据
- 读不到六维时显示「—」，分数也显示「—」；`refresh_attr_info` 是合并语义，
  所以「面板被说明气泡挡住 / 这一帧 OCR 漏一项」时那一项保留上一次的值，不会掉成「—」
- 彩/金/白是**手动填**的技能数量，存在 `TrainingTask.config` 的
  `技能-彩色 / 技能-金色 / 技能-白色`，改完即时重算分数
- 读不到六维时显示「—」，分数也显示「—」
- 属性图标暂用文字占位；要游戏里那套 logo，把 6 张小图放到 `assets/ui/attrs/<属性名>.png`，
  我再接 `_attr_icon`（现在还没接，文件不存在就直接显示文字）

按钮不走 `clicked` 信号，统一由 `MonitorView.eventFilter` 派发（`_button()` 工厂里挂 `_click_handler`）。
原因：这套环境下 `clicked` 连接绑定方法会静默失效，按钮点了没反应。

常见改法：

```jsonc
// 1. 换顺序：把 cards 挪到 hero 前面，直接调数组顺序
"sections": [{"type": "cards", ...}, {"type": "hero", ...}]

// 2. 隐藏一张卡：从 items 里删掉这一项即可
"items": [{"id": "today", ...}, {"id": "risk", ...}]

// 3. 加一行：往 rows 里追加
["今日已逛", "{shop_today}"]

// 4. 改文案 / 换字段：占位符写成 {字段名}，缺失显示「—」
"title": "{week} 周 · {day}"

// 5. 整段兜底：模板里有任何字段没值时，用 fallback 顶替整句
"title": "第 {week} 周 {day}", "fallback": "等待开始"
```

## 可用字段

已接真实数据：

| 字段 | 含义 | 来源 |
|---|---|---|
| `{week}` `{day}` | 第几周 / 星期几 | 顶栏 OCR（`top_bar()`） |
| `{stamina}` `{stamina_max}` `{stamina_pct}` | 专注 / 上限 / 百分比 | 同上 |
| `{coin}` | 零用钱 | 同上 |
| `{style}` | 当前流派 | 每拍命中 handler 时同步 |
| `{action}` | 当前动作（域 · handler） | 同上 |
| `{uptime}` | 本次运行时长 | `task.start_time` |
| `{timeline}` | 最近 12 条动作（时间线专用） | 同上 |
| `{fail_rate}` | 最近一次科目页失败率 | `info["当前失败率"]` |
| `{buy_strategy}` `{fail_limit}` `{picnic_threshold}` `{stuck}` | 配置回显 | `TrainingTask.config` |
| `{device_title}` `{device_meta}` | 设备信息 | `og.device_manager` |

按钮专用（不给卡片用，只给两个按钮的状态）：

| 键 | 含义 | 判据 |
|---|---|---|
| `connected` | 游戏接上了没有 → 第一栏【启动/停止】 | `capture_method` 在且 `connected()` 为真 |
| `training_active` | 训练任务**真在跑** → 第二栏【开始/暂停】 | `task.enabled and task.running` |
| `training_running` | 任务在跑或在队列里（只作信息，不控按钮） | `task.running or task.enabled` |

**尚未接入**（显示「—」，接了才有值）：`{match_countdown}` `{weekend_plan}` `{shop_today}`。
不想要这几行，直接从 `rows` 里删掉，别留着占位。

## 热重载规则

- 数据：每 500 ms 读一次 `task.info`，**不在 UI 线程做 OCR**。
- 布局：每 500 ms 比对 `configs/monitor_layout.json` 的 mtime，变了就整页重建。
- 样式：比对当前主题对应 QSS 的 mtime，变了就重新 `setStyleSheet`。
- JSON 写坏：报错日志 + 回退内置默认布局，不会白屏。

## 改样式

`assets/ui/monitor.dark.qss` 和 `monitor.light.qss` 是两份独立配色，随框架主题切换自动选。
所有选择器都挂在 `#monitor*` / `[mRole="..."]` 上，不会污染框架自带的按钮和卡片。

想加一种角色（比如给某行加红色），在 QSS 里加属性选择器，再在 JSON 里没法指定——
角色是渲染层写死的，这种情况改 `monitor_view.py` 的 `_label(..., role)` 调用。

## 已知边界

- 左侧导航不能改、不能删，只能靠 `custom_tabs` 往里面**加**页；这一页就是加进去的。
- 页面内的一切（卡片、按钮、间距）都是自己的 Qt 代码，不受框架限制。
- 深浅主题必须两套 QSS，写死颜色的规则在另一套里会看不清。
