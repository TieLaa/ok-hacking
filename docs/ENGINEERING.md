# 工程规则（ok-hacking）

> 目的：让「加一个界面」变成可复现的固定流程，而不是每次临场发挥。
> 本文件是规则，`AGENTS.md` 是硬约束（每次会话必加载）。两者冲突时以 `AGENTS.md` 为准。

## 1. 目录职责

| 路径 | 放什么 | 不放什么 |
|---|---|---|
| `src/tasks/TrainingTask.py` | 任务壳：元数据、`default_config`、`__init__` 状态 | 任何 handler / 解析 / 打分逻辑 |
| `src/tasks/training/flow.py` | 单拍状态机、卡死检测、存证 | 业务判据 |
| `src/tasks/training/parsers.py` | 只读解析：OCR 文本 → 结构化数据、帧取色 | 点击、等待 |
| `src/tasks/training/scoring.py` | 纯打分（输入 dict / 文本，输出数字） | 任何 `self.click` |
| `src/tasks/training/handlers/<域>.py` | 按界面域分的 handler：popup / training / weekend / match / entry | 跨域判据、通用工具 |
| `src/tasks/training/handlers/__init__.py` | **唯一**的 `HANDLERS` 优先级表 | 方法实现 |
| `src/tasks/training/constants.py` | 流派地图、JPEG 参数等模块级常量 | 运行期可变状态 |
| `src/tasks/BaseGirlsTask.py` | 通用文字匹配 / 点击 / 截图 / 区域工具 | 养成业务 |
| `src/tasks/ui/Defs.py` | 区域坐标、文案表、正则 | 业务分支 |
| `tools/` | 运维脚本：抓图、OCR 转储、回归、日志 | 任何游戏业务逻辑 |
| `docs/shots/` | 回归截图（PNG）+ `expected.json` + `.ocr_cache/` | 人工截图素材 |
| `docs/` | 手册、运行报告、已知问题 | 临时草稿 |

## 2. 新增一个界面：固定七步

1. **取证**：把界面截图存 `docs/shots/<日期>-<界面>.png`（必须是 PNG，回归脚本按 `*.png` 扫）。
2. **看文字**：先跑 `python tools/check_handlers.py 这张图.png --cache --verbose`，确认没被现有 handler 认走。
3. **写 handler**：放进对应的域文件，判据写在 docstring 里（为什么用这个词，不用那个词）。
4. **定位置**：在 `training/handlers/__init__.py` 插进元组。判据越独特越靠前；共用「下一步 / 确定」的必须排专属 handler 之后。
5. **登记期望**：`python tools/check_handlers.py 这张图.png --update-expected`，然后**人工确认**命中结果和点击位置合理。
6. **全量回归**：`python tools/check_handlers.py --cache` 必须 exit 0。
7. **提交**：一个界面一个 commit，提交信息写「界面 + 判据关键点」。

## 3. handler 写作规则

- 一个 handler 只处理**一个界面状态**，返回 `True` = 消费本拍。
- 判据优先用**独有文案**；只能用通用词（下一步 / 确定 / 关闭）时，必须显式排除已知的邻居界面。
- 不要按卡片数量拆 handler：同一界面的多张卡在 handler 内部循环（见 `handle_skill_select` / `handle_event_choice`）。
- 点击只出现在 handler / flow；parsers 和 scoring 必须能脱离游戏跑。
- 配置读取一律 `self.config.get("键", 默认值)`，默认值写代码；`configs/*.json` 是用户覆盖，不要当默认值改。

## 4. tools/ 脚本规则

- 一个用途一个文件；文件名动词开头：`check_`（只读校验）、`dump_`（导出）、`capture` / `shot`（抓图）、`start_`（启动）。
- 脚本不写业务判据。需要「怎么点」的逻辑，说明它该进 handler。
- 每个脚本必须有一段 docstring：为什么存在、怎么用、依赖哪些环境（管理员 / 游戏进程 / 缓存）。
- 回归类脚本必须有退出码语义：通过 `0`，失败 `1`。

## 5. 回归门禁

```powershell
.venv\Scripts\python.exe tools\check_handlers.py --cache            # 必须 exit 0
.venv\Scripts\python.exe tools\check_handlers.py --cache --all-hits # 查抢占（诊断，不强制）
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_training_rules.py"  # 决策逻辑
```

- `docs/shots/expected.json` 是断言表：截图 → 应该命中的 handler。
- `tests/test_training_rules.py` 是决策断言：选哪张卡、点哪个位置、体力怎么解析。回归图只能验证「命中谁」，验证不了「决策对不对」。
- 改 handler 顺序 = 改行为。顺序调整后必须重跑全量。
- `--all-hits` 列出「同一张图有多个 handler 返回 True」。这些不一定是 bug（顺序正确就安全），但**新增**的多命中要确认不是新 handler 判据过宽。

## 6. 截图与日志

规则见 `AGENTS.md`（q95 + 4:4:4、先读 `ocr.txt`、单会话最多 5 张图）。

- 运行时取色读内存帧，不受存盘格式影响。
- 事后复核颜色必须用 q95+4:4:4 的 jpg，不要用历史 PNG 之外的压缩图。

## 7. 重构规则

- 重构前先 commit，并保存一份回归基线（`check_handlers.py --cache` 的输出）。
- 重构后逐张比对基线；命中 handler 有任何差异都必须解释清楚。
- 只搬代码不改行为。要改行为就单独一个 commit。
