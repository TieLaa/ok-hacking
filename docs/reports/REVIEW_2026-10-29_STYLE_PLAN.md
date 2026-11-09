# 审核清单：流派由队长决定 + 周五/周六/周日行程（2026-10-29 最终版）

> 用户口径（第三版，覆盖前两版）：
> 1. 流派不是配置项，是**队长的属性**；本任务只管「快速接手」：流派为空 → 点一次日历解析；
> 2. 周六：训练流原设定，其他流派都去餐厅打工；
> 3. 周日：扭蛋流→步行街→餐厅、小游戏流→游戏厅→餐厅（表里两条，不看有没有花钱）；
> 4. 打工：**能选的优先** → 队友多（卡片左上角头像）→ 右边（耗精力）；
> 5. 日程完全由表决定，不再保留「周末第一天优先资源点」开关。
> 规则全文在 `docs/RULES.md` 第 0 / 1 / 1.1 / 6 节。

## 一、流派：一条路径

`_style` 是运行期变量，唯一来源是**日历解析**：

| 时机 | 行为 |
|---|---|
| `run()` / 点「开始训练」 | `reset_style()` 清空（新一局，队长可能换了） |
| 训练日主界面 + 流派为空 | `handle_style_probe` 点左上角开日历（≤3 次，3 秒冷却） |
| 日历页 | `read_plan_captain` 读「XX的训练计划」→ `style_for_name` 查表 → 写入 → 关日历 |
| 查不到 | 按训练流跑 + 告警；不再反复开日历 |

- 全自动流程（另一条流程，未实装）自己把流派写进 `_style`；本任务看到有值就不点日历。
- 删掉：`流派` 配置项、`STYLE_AUTO`、`declared_style()`、`style_from_config_captain()`。

## 二、必须改的互斥点

### 1. 周六：商店流不再进店

旧：商店流周六/周日各进一次店。新：周六所有人不逛店（其他流派去餐厅打工），
只有**周日 + 训练流/商店流**才在首页先点「商店」（`handle_go_out`）。

### 2. 行程表补全（不再有特判）

`WEEKEND_PLAN` 覆盖 4 个流派 × 周六/周日 共 8 条，删掉 `if day=="六" and style!="训练流"`。

### 3. 周日扭蛋流/小游戏流：表里就是两站

`("日","扭蛋流"): ["步行街","餐厅"]`、`("日","小游戏流"): ["游戏厅","餐厅"]`。
不再有 `spent` 记账、不再有 `SUNDAY_WORK_FALLBACK` 例外。

### 4. 删掉「周末第一天优先资源点」

地点完全由表决定：训练流周日就是公园。配置项、特判、描述全部删除。

### 5. 打工选卡：判断「能不能选」

`min(found, ...)` 那套「左边/右边」特判已删，改成：

```python
usable = [b for b in ranked if self.option_selectable(b, texts, stamina, cost)]
return usable or ranked        # 都不可选时按右边优先试
```

- `option_selectable`：界面提示「精力不足/不可/无法」→ 不可选；卡片标的消耗 > 当前精力 → 不可选。
- 右边默认消耗 `WORK_STAMINA_COST = 25`，左边 0；卡片写了消耗以卡片为准。
- `打工最低精力` 配置项已删。

### 6. 周五休息阈值按流派分档

训练流不变（`休息体力阈值`，默认 40）；其他流派 55；商店流 80。

### 7. 日历页专属 handler（修旧 bug）

日历页带「转动扭蛋/野餐/划船」，旧版被 `handle_street` / `handle_park` 抢走
（2026-10-27 00:54 误当扭蛋页退出并误记「步行街已去」；15:17 那局被点了「野餐」）。
现在 `handle_training_plan` 排在所有地点 handler 之前，读完队长就关掉。

## 三、行为等价性（训练流）

- 周六：步行街 → 游戏厅 → 公园；周日：公园 + 划船/野餐；周五阈值仍取配置值。
- 不进店、科目/比赛/商店/扭蛋/游戏厅判据全部未动。
- 差别：日历页出现时不再误记「步行街已去」、不再误点「野餐」；接管首日多两次点击。

## 四、删掉的代码（自检：无调用方）

| 删除 | 原因 |
|---|---|
| `constants.STYLE_PLACES` | 与 `WEEKEND_PLAN` 重复 |
| `constants.STYLE_AUTO` / `SUNDAY_WORK_FALLBACK` | 概念取消 |
| `flow.declared_style()` / `style_from_config_captain()` | 同上 |
| `flow.low_stamina()` | 从来没有调用方 |
| `weekend.mark_spent()` + `spent` 状态 | 周日改由表决定 |
| `parsers.mates_near()` → `mates_on_card()` | 口径改成卡片左上角 |
| 配置 `流派` / `餐厅选项` / `打工最低精力` / `周末第一天优先资源点` / `周末先看商店` | 被新规则取代 |

## 五、待实测

1. 餐厅内页（无实测帧）：日志 `餐厅：当服务员=x人…、桌游GM=y人…｜精力 …` 要看得出头像数与可选性。
2. 左上角按钮：确认 `handle_style_probe` 点的是日历而不是返回。
3. 队长名 OCR：日志 `训练计划：队长「XX」→ 流派「XX」`。

## 六、回归

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_training_rules.py"   # 151 条
.venv\Scripts\python.exe -m unittest tests.TestMain                                   # 12 条
.venv\Scripts\python.exe tools\check_handlers.py --cache                              # 32 张，exit 0
```
