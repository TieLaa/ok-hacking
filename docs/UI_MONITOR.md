# 监控页（布局可调）

启动后默认落在这一页（`MonitorTab._select_on_startup` 等主窗口 showEvent 后切过来）：
打开就知道第几周、体力多少、现在在做什么、最近点了什么。
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
| `connection` | 连接状态条 | `height` `title` `meta` `run_text` `stop_text` |
| `captain` | 队长选择（勾选框 + 分组下拉）+ 自动训练开关 | `height` `label` `hint` `combo_width` `start_text` `pause_text` |
| `hero` | 周目 / 体力 / 文案 | `stats` 数组、`title`、`fallback` |
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

- 勾选框 = 有没有队长；下拉按流派分组（训练流 / 商店流 / 扭蛋流 / 小游戏流），
  角色表来自 `src/tasks/training/constants.py` 的 `STYLE_BY_NAME`。
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
| 连接条右侧 | **启动 / 停止** | **总开关**：启动 = 接入游戏（连设备、必要时拉起游戏）；停止 = 断开游戏（停任务 + 暂停执行器 + 释放截图会话），**并把第二栏复位成「开始」** |
| 连接条右侧 | 重新检测 | 重扫窗口并刷新页面数据 |
| 队长栏 | **开始 / 暂停** | **只管自动训练脚本自己**：开始 = `task.enable()`；暂停 = `task.disable()`。不碰「接入游戏」 |
| 队长栏右侧 | 快速比赛 开关 | 启停 `QuickMatchTask`（实时同步任务状态） |
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
| 第一栏【启动/停止】 | `connected`：**游戏接上了没有**（`capture_method` 在且 connected） | 旧判据 `not executor.paused` 是**执行器**的运行态 → 第二栏点【暂停】里的 `task.unpause()` 会调 `executor.start()`，把第一栏从「启动」顶回「停止」 |
| 第二栏【开始/暂停】 | `training_active` = `enabled and running` | 旧判据 `running or enabled`（`enabled` 含「在队列里等」）→ 点完【开始】立刻变【暂停】，其实一帧没跑 |
| 第二栏【开始】前置条件 | `connected` 必须为真 | 旧版不管连接状态就 `enable()` → 任务在队列里躺着，执行器还停着，永远不动 |

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
  数值由 `parsers.read_attribute_values` 读，handler 写进 `info["attrs"]`
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
| `{stamina}` `{stamina_max}` `{stamina_pct}` | 体力 / 上限 / 百分比 | 同上 |
| `{coin}` | 零用钱 | 同上 |
| `{style}` | 当前流派 | 每拍命中 handler 时同步 |
| `{action}` | 当前动作（域 · handler） | 同上 |
| `{uptime}` | 本次运行时长 | `task.start_time` |
| `{timeline}` | 最近 12 条动作（时间线专用） | 同上 |
| `{fail_rate}` | 最近一次科目页失败率 | `info["当前失败率"]` |
| `{buy_strategy}` `{clear_balance}` `{fail_limit}` `{picnic_threshold}` `{stuck}` | 配置回显 | `TrainingTask.config` |
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
