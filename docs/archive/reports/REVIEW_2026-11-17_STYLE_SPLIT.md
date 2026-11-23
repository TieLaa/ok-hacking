# 审查报告：流派策略拆分与谓词表（2026-11-17）

- 审查范围：`d5ae8cc^..add0fbb` 共 5 个提交，16 个文件（+619 / −402）
  - `d5ae8cc` 流派规则收进一张表：周末去哪、公园选什么不再各写一遍
  - `a4b46d1` 公园选什么搬进流派策略：判断和读画面分开了
  - `cb13850` 地图和餐厅的流派判断也搬进策略表了
  - `d3a1b9a` 周四休息规则也进策略表：参数表只留一个阈值
  - `add0fbb` 删掉最后一个策略空方法：步行街挑卡不是流派差异
- 审查方式：逐行读 diff，新旧逻辑逐条对照，实际跑测试与门禁
- 未纳入：工作区未提交的 `docs/decisions.md`（python-guidelines 安装记录，与本次话题无关）

## 结论

**通过，无阻塞问题，可发布。** 这是一次忠实的搬迁：8 条旧判据逐条核对，决策结果一致；
新增的公园基线用例把最容易改坏的「两个日期来源」钉住了。

需要补的不是行为，而是「文档自述」和「表内冗余」：2 个中等、5 个低。
按 `docs/decisions.md` 的口径，这类问题属于「改一处只改一处」的反面，应在下一次会话前修掉。

## 一、验证结果

| 项目 | 结果 |
|---|---|
| 单元测试（`test_park_decision` + `test_params_access` + `test_training_rules`） | 680 条全过（skipped=3） |
| `python tools/check_handlers.py --cache` | exit 0；1781 帧认对 1721（96.6%）；`docs/shots` 50 帧认对 49 |
| 配置单一来源门禁 | 违规 0 处（声明 37 项 / 被读 35 项） |
| `git diff --check` | 无空白错误 |

## 二、行为等价性核对

| 旧位置 | 新位置 | 核对结论 |
|---|---|---|
| `WEEKEND_MAIN_PLACE` + `WEEKEND_PLAN` + `training_weekend_places` | `Style.weekend_places` / `TrainingStyle.weekend_places` | 一致：主地点+餐厅、买到即收工、第 7 周周六分档、缺口 <30/≥30 周六/周日、读不到回退「餐厅→公园」 |
| `MapPlace.prefer_venue` 主地点分支 | `Style.prefer_venue` | 一致：钱门槛 360、第 7 周周六不看钱、读不到钱去主地点、候选不足原样返回 |
| `ParkPlace.park_pick` 三段规则 | `Style.pick_park` → `pick_park_normal` | 一致：海边前 → 最后一周 → 平时（训练流缺口 / 队友 2 人 / 划船比赛 / 配置默认），顺序也没变 |
| `restaurant_preference` 的「分散退出」 | `TrainingStyle.accept_work_card` | 一致；唯一差异：`week=None` 时旧代码比较 `None < 7` 会 TypeError，新代码放行（更稳健） |
| `training_day_action` 周四阈值 | `Style.should_rest` | 一致：白名单从参数表字典换成 `thursday_rest`，阈值仍是 `max(stamina_threshold, 50)` |
| `shop.save_money_for_weekend` | `saves_money` | 一致；未知流派仍保守返回 True |
| `street.money_first` | `cash_first` | 一致；未知流派仍按「非训练流」处理 |
| `map_context` 的 `discount_worth` / `_detour_hits` 的 gacha 收工 | `discount_bonus` / `ends_day_after_venue` | 一致；谓词表没有新增分支，流派差异被收进两个布尔值 |

细节差异（都不改变决策）：

- 旧 `weekend_gap()` 返回 `max(上限−当前, 0)`；新 `TrainingStyle` 直接用 `stamina_max - stamina`，精力超上限时日志会出现负数，但阈值比较结果相同。
- `today_places` 现在对所有流派都读一次 `top_bar()`；旧代码只有训练流读。`top_bar` 解析的是已经拿到的 texts，不影响结果。

## 三、做得好的地方

- **搬迁忠实**：不是「重写一遍」，而是把原有日志、注释和边界条件一起搬过去；连「第 7 周周六不插急招」「周末周日精力不再兑现」这些反直觉口径都保住了。
- **谓词表没有变胖**：`MAP_DETOURS` 仍是「急招 + 步行街打折」两条。旧代码里两处 `style == "扭蛋流"` 改成 `discount_bonus` / `ends_day_after_venue` 两个表值，`_detour_hits` 的守卫顺序和语义未变；`map_context` 仍只读一次钱/精力。没有发现新问题（唯一的 nit 是 `_detour_hits` 里第二次调 `style_spec`，开销可忽略）。
- **新增测试钉住行为**：`tests/test_park_decision.py`（11 个用例）专门覆盖 `current_day()` 与 `top_bar().week` 来源不同、只读到周不读到星期的情况。
- **ctx 设计正确**：`NamedTuple` 而不是 dict，字段拼错立刻 AttributeError；`ctx` 只读、每事件一个，没有把任务对象塞进方法。
- **参数表真正减负**：`thursday_threshold_by_style` 整个字典删除，`params.json` 的 `_doc` 写清了新来源（`Style.thursday_rest` + 单值）。
- **删掉误导性空方法**：`wants_shop` / `pick_shop_item` / `event_detour` / `pick_street_card` 这些 `NotImplementedError` 桩会让后来人以为还有迁移工作。
- **谓词表没有膨胀**：没有为「扭蛋流收工」「扭蛋流打折」各加一条 predicate，而是把流派差异压成 `MapContext` 上的布尔值，`MAP_DETOURS` 仍是两条。

## 四、问题清单

### 中-1 `styles.py` 自述仍停在「尚未接入」，与本次改动相反

位置：`src/tasks/training/strategy/styles.py:1`、`:20`、`:131`、`:150`

- 文件头：`流派策略：最终形状（接口先行，**尚未接入**）`
- 第 20 行：`判断（方法，未接）`
- 类文档：`方法 = 判断（接入 ctx 前一律抛错）`
- 分节标题：`判断（尚未接入，调用即报错）`

但同一文件第 14 行已经写「5 个事件判断已接」，第七节也写「剩下：无」。这是本项目最重要的「业务地图」，下一会话会先读它，看到「调用即报错」会不敢用、甚至重做迁移。

建议：4 处改成「已接入」并删掉「调用会报错」；`三部分` 改为「值 + 判断」；核对日期从 `d350d5e / 3cf2e0c` 更新到 `add0fbb`。

### 中-2 规则书/界面图仍指向已删除的常量和函数

| 位置 | 内容 | 现状 |
|---|---|---|
| `docs/RULES.md:92` | `constants.WEEKEND_PLAN / WEEKEND_MAIN_PLACE` | 两个常量已在 `constants.py` 删除 |
| `docs/RULES.md:105` | 「主地点…（`constants.WEEKEND_MAIN_PLACE`）」 | 同上 |
| `docs/RULES.md:174` | 「已收进 `training_weekend_places`：缺口 <30 → 餐厅打工，缺口 ≥30 → 公园」 | 方法已删除；行为描述也缺了 2026-11-16 的「周六缺口 ≥30 先去餐厅看一眼」 |
| `docs/RULES.md:482` | 「实现：`places/map.py: training_weekend_places`」 | 方法已删除 |
| `docs/UI_MAP.md:64` | 「训练流周六：只有一个地点在打折就先进它」 | 该分支本次已删（训练流本就不去步行街/游戏厅） |
| `docs/UI_MAP.md:67` | `constants.WEEKEND_PLAN / WEEKEND_MAIN_PLACE` | 两个常量已删除 |

影响：`RULES.md` 是用户口径的唯一落点，路径失效会让下一次搜代码搜空；`RULES.md:98` 的规则行也和当前代码不一致（见上表 174 行）。

建议：统一改成 `strategy/styles.py: Style.weekend_places / prefer_venue`，顺手把训练流的行文改成「缺口 <30 → 餐厅→公园；≥30 周六 → 餐厅→公园；≥30 周日 → 公园」。

### 低-1 `weekend_plan` 有两行永远不会生效的值

位置：`src/tasks/training/strategy/styles.py:386`、`:405`；`tests/test_style_table.py:35`

`Style.weekend_places` 只在 `weekend_main is None` 时读 `weekend_plan`（`styles.py:164-169`）。
`GachaStyle` / `MiniGameStyle` 都设了 `weekend_main`，所以它们的 `weekend_plan` 是死数据；
`TrainingStyle` 继承的 `("餐厅",)` 也永远不会被当兜底（读不到缺口时 `map.today_places` 直接返回硬编码的 `["餐厅","公园"]`）。

`test_style_table.py:35` 还把这个死值钉住了，会让人误以为改它有用。

建议：删掉两行 `weekend_plan`，测试改成断言 `weekend_places(ctx)` 的结果（构造最小 `WeekendContext`），或只保留商店流那一行。同步修 `styles.py:89`「只是兜底值，正常不会用到」→「不会被读取」。

### 低-2 `GachaStyle` 文档里的周四阈值还是 60

位置：`src/tasks/training/strategy/styles.py:375`

`周四精力 < 60 直接休息` 与 `params.json` 的 `rest.thursday_threshold = 50`、`RULES.md`、`flow.py` 的 60→50 记录都冲突。策略表既然是唯一真相，注释里的数字就不能单独存在。

建议：改成「低于 `rest.thursday_threshold`（默认 50）」，不写死数字。

### 低-3 `MATE_MARGIN` 仍有两个家

位置：`src/tasks/training/strategy/styles.py:148`、`src/tasks/training/places/restaurant.py:46`

两处都是 `2`，`styles.py` 的注释还写着「与 `RestaurantPlace.MATE_MARGIN` 同一口径」。这与 `AGENTS.md`「同一件事实第二次写进代码时，先找它的家」冲突；`docs/PROMPT_PLAYBOOK.md:1103` 又明确记录过「两边各一个常量」是有意为之。现在等于三个文件在描述同一件事。

建议二选一，并在 `PROMPT_PLAYBOOK` 同步改口径：
- 让 `restaurant.py` 读 `styles.Style.MATE_MARGIN`（推荐，事实只剩一个家）；
- 或者保留两份，但在 `styles.py` 明确写「这是第二份，改这里必须同步 `RestaurantPlace.MATE_MARGIN`」。

### 低-4 `ParkContext` 的两个日期字段没有任何规则读取

位置：`src/tasks/training/strategy/context.py:16-25`；`styles.py:203-221`

文档说「`week/day` 来自 `current_day()`，给『海边前』用；`top_week` 给『最后一周』用，合并会改变行为」。
实际 `Style.pick_park` 只读 `ctx.vacation_target` 和 `ctx.top_week`，`ctx.week` / `ctx.day` 在公园规则里没有引用；
`vacation_target` 是调用方 `vacation_eve_target()` 用 `st.date` 算好传进来的，不是方法里读 `ctx.week`。
`test_park_decision.py::test_week_readable_but_day_missing` 名字暗示在验证这个区分，实际验证的是「`st.date` 不完整 → 海边前不触发、`top_week` 仍生效」。

另外 `ParkContext.style`、`VenueContext.style`、`WorkContext.style`、`WeekendContext.style` 都未被读取（`RestContext.style` 只用于日志）。

建议：删掉未用字段（公园只留 `vacation_target` + `top_week`），或把注释改成「保留 / 仅日志」；测试注释同步写清它实际证明的东西。

### 低-5 「缺口」公式有两份写法

位置：`src/tasks/training/places/map.py:87-93`（`max(top−cur, 0)`）与 `styles.py:314`、`:332`（裸 `stamina_max − stamina`）

当前阈值比较结果一致（负数同样小于 30/70），但这是同一个业务量「缺口」的两种定义；日志里会出现负缺口，将来有规则写 `gap == 0` 时会分叉。

建议：在 `map.today_places` / `park.park_pick` 构造 ctx 时传入 `weekend_gap()` 的结果，或在 `styles.py` 统一 `max(..., 0)`。

### 低-6 `today_places` 的「三件所有流派相同的事」对训练流不成立

位置：`src/tasks/training/places/map.py:107-108`、`:134-141`

训练流 `weekend_places` 返回 `final=True`，直接 `return places`，跳过：
- 「商店」过滤；
- 两个场所开关过滤；
- 开关删空后的体力二选一。

其中第 7 周周六的 `["步行街","餐厅"]` 会绕过「允许进步行街」开关。这是**本次之前就有的行为**（旧代码同样早返回），不是回归；但 docstring 说「三件事所有流派相同」，与 `final` 的实际语义不符。

建议：docstring 加一句「训练流 `final=True`，下面三件不执行」；如果「关了步行街仍去」不是期望行为，另开需求，不要夹在这次搬迁里悄悄改。

## 五、未验证与风险

- 只做了单元测试与 handler 门禁，没有实机跑。第 7 周周六、周四低精力、公园海边前这三条真实分支只靠测试钉住，实跑仍需盯一次。
- `tests/test_park_decision.py` 与重构在同一个提交（`a4b46d1`），从 git 历史看不出「先写基线、再搬迁、基线始终绿」。行为正确性由当前测试保证，但过程证据缺失；下次建议基线单独一个提交（仓库里 `test_shop_decision.py` 已有先例）。
- `docs/shots` 仍有一帧已知误判（`2026-10-26-day1.png`，期望 `story_arrow`、实得 `unknown`），门禁判定为通过；与本批改动无关。
- 工作区 `docs/decisions.md` 有未提交改动（python-guidelines 技能安装），不在本次审查范围。

## 六、建议的最小修复顺序

1. `styles.py` 4 处「尚未接入」措辞 + 核对日期（10 分钟，影响最大）。
2. `RULES.md` / `UI_MAP.md` 6 处失效路径与过期行为描述（10 分钟）。
3. 删 `GachaStyle` / `MiniGameStyle` 的 `weekend_plan`，同步 `test_style_table`（5 分钟）。
4. `GachaStyle` 周四数字改成参数引用（1 分钟）。
5. （可选）统一 `MATE_MARGIN`、清理 ctx 未用字段、统一缺口 clamp。

前 4 项都不改代码行为，可以合成一个 `docs: ...` 提交；第 5 项需要单独确认，尤其 `MATE_MARGIN` 在 `PROMPT_PLAYBOOK` 里有明确的历史口径。
