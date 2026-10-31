# 监控页（布局可调）

挂机时看的第二页：打开就知道第几周、体力多少、现在在做什么、最近点了什么。
**结构和样式都能直接改**：在页面里点「编辑布局」，或者手改 JSON / QSS，存盘即生效。

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
| `connection` | 连接状态条 | `height` `title` `meta` |
| `captain` | 队长选择（勾选框 + 分组下拉） | `height` `label` `hint` `combo_width` |
| `hero` | 周目 / 体力 / 文案 / 按钮 | `stats` 数组、`now`、按钮文案 |
| `cards` | 一行摘要卡 | `items[].title` + `rows`（每行 `["左","右"]`） |
| `timeline` | 最近动作 | `max_rows` `time_width` `event_width` |

## 队长选择

- 勾选框 = 有没有队长；下拉按流派分组（训练流 / 商店流 / 扭蛋流 / 小游戏流），
  角色表来自 `src/tasks/training/constants.py` 的 `STYLE_BY_NAME`。
- 下拉里的 `── 训练流 ──` 这类标题只是分组，点不中（会自动退回上一个角色）。
- 勾掉勾选框 → `指定队长` 清空 → **无队长模式**：只跑已有局，遇到开局页面就停
  （口径见 `docs/RULES.md` 第 0.5 节）。
- 改动直接写进 `TrainingTask.config`，即 `configs/TrainingTask.json`，立刻生效，不用重启。

## 第一栏 / 队长栏上的快捷按钮

| 位置 | 控件 | 作用 |
|---|---|---|
| 连接条右侧 | 开始 / 暂停 | 等同顶部开始按钮（`executor.pause()` / `start_controller.start()`） |
| 连接条右侧 | 重新检测 | 重扫窗口并刷新页面数据 |
| 队长栏右侧 | 快速比赛 开关 | 启停 `QuickMatchTask`（实时同步任务状态） |
| 队长栏右侧 | 自动训练 开关 | 启停 `TrainingTask` |
| 队长栏最右 | ✎ | 进出编辑布局模式 |

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
