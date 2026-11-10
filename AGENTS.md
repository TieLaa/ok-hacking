# ok-hacking 项目规则

## 读图（硬规则）

- **先读文本，再考虑看图。** 存证按局分目录（2026-11-10 起）：`logs/<局ID>/debug/<时间>_<tag>/`、`logs/<局ID>/unknown/`（MCP 运行时在 `logs/mcp/<run_id>/<局ID>/` 下）里的调试包都带 `ocr.txt`（文字+坐标）和 `actions.txt`，判断卡在哪一步够用。只有 OCR 漏判（按钮没识别出来）时才读图。
<!-- 变更 2026-11-10 · opencode · 存证改为按局分目录，同步本条路径 · 按局清理才能控住体积；规则细节见 docs/ENGINEERING.md 6.1 -->
- **一个会话最多读 5 张图。** 历史里的图每轮都会重新发给模型：1080p PNG 每张约 3 MB，读图首 token 等待 ≈ 3.5 秒/MB（实测 19 张图让每轮从 5 秒涨到 90 秒）。超过 5 张就开新会话。

## 截图格式

- 调试/存证帧一律存 **JPEG q95 + 4:4:4**：`TrainingTask.dump_debug`（`frame.jpg`，用 `src/tasks/training/constants.py` 的 `JPEG_PARAMS`）、`dump_unknown`（`<name>.jpg`）、`tools/wgc_capture.py`、`tools/win_shot.py` 都已改。
- 为什么不用 PNG：同一帧 PNG 约 2.4 MB、q95 约 0.48 MB，读图耗时与体积成正比（每 MB 约 3.5 秒）。
- 为什么是 4:4:4：项目有按像素/区域取色复核品质、互动的需求，色度抽样会把颜色糊到相邻像素。实测单像素最大色差 4:2:0 是 94、4:4:4 是 20；区域平均色差都在 1 以内。旧版 OpenCV 没有 `IMWRITE_JPEG_SAMPLING_FACTOR_444` 时会自动降级。
- 运行时取色读的是内存帧 `self.frame`，与存盘格式无关。
- 调用截图工具时直接给 `.jpg` 路径：

  ```powershell
  .venv\Scripts\python.exe tools\wgc_capture.py out.jpg
  .venv\Scripts\python.exe tools\win_shot.py <pid> out.jpg
  ```

- `docs/shots/` 保持 PNG：`tools/check_handlers.py`、`check_shop_quality.py` 等按 `*.png` 扫描，改成 JPEG 会漏测。

## 工程（硬规则）

- 业务代码只放 `src/tasks/training/`（flow / parsers / scoring / handlers），`tools/` 只放运维脚本，禁止写游戏业务判据。细则见 `docs/ENGINEERING.md`。
- 改 handler 判据或顺序后必须跑 `python tools/check_handlers.py --cache`，exit 0 才能提交；新截图先 `--update-expected` 再人工确认。
- **跨边界不猜编码。** 字节在进程 / 管道 / 文件 / 终端之间流动时，要么原样传字节，要么在那一处显式写明 encoding；任何一层替你「字节↔文本」的隐式转换，都会拿本机默认（CP936）去猜，`✓`、假名、带音符字母被改写成 `�`/`?`，且不可逆。判断只有一个问句：**这一步谁在解码、用什么码？** 答不上来，就只传字节。

  当前实例（供对照，不是判据）：Python 侧用 `$env:PYTHONUTF8="1"`（或 `-X utf8`，不继承子进程）固定自己的 stdio；PowerShell 侧 `|`、`2>`、`*>`、`2>&1` 都会替你转一次文本，不转的只有「不 pipe」和 stdout 的 `>`。踩坑细节见 `docs/PROMPT_PLAYBOOK.md`「跨边界不猜编码」。
  <!-- 变更 2026-11-10 · opencode · 用通用规则替换「跑 Python 前固定 UTF-8」特例 · 特例越加越满、换个语法就失效；通用规则给判断问句，pipe/重定向/subprocess/文件/终端由同一句覆盖 -->
- **MCP 开发循环**（ok-script 2.0.16+）：改已注册任务用 `list_tasks` → 原地改源码 → `reload_tasks(task_ids)` → 用 `tools/mcp/probe.py` / `training.py` 验证，不要重启 GUI。任务类必须保持 `reset_to_start` / `verify_start_state` 可用（`src/tasks/attempt.py`），业务逻辑不复制进 harness。流程见 `docs/MCP_DEV.md`。
- **提交信息写给用户看。** subject 是一句话的「用户能感知的变化」——发版时 pyappify 直接拿两个 tag 之间的提交生成更新提示，`handler`、`TaskExecutor`、`ci/test/chore` 这些内部词会原样出现在用户面前。技术细节放正文；纯内部改动（测试 / CI / 死代码）发版前用 `git rebase -i` 合并或压掉。
  <!-- 变更 2026-11-10 · opencode · 新增提交信息口径 · 安装器更新提示由 tag 之间的 commit 生成，内部词会直接暴露给用户；v0.1.9 前已按此口径重写过一批提交 -->
- 踩到新坑时，把「下次该怎么说」补进 `docs/PROMPT_PLAYBOOK.md`，不要只改代码。

## 开跑指令（用户 2026-11-07 口径）
<!-- 变更 2026-11-07 · opencode · 新增「开跑指令」一节 · 用户不再口头复述队长/目标分/吃药条件，改由会话读 configs/TrainingTask.json 自取 -->
<!-- 变更 2026-11-07 · opencode · 「队长队列」配置键已废弃（队列降级为 MCP 注入参数），改口径描述 · 防止会话按已删键找配置 -->

用户说「开始跑了 / 持续跟踪一下」时，**不再复述业务条件**（队长、目标分、吃不吃药）——这些以 `configs/TrainingTask.json` 为唯一真相（GUI 改的就是它），会话里先读它确认当前配置，再动手：

1. 读 `configs/TrainingTask.json`：确认「指定队长」（界面唯一真相；目标分由 agent 说或走 MCP 注入）、「自动恢复精力 / 恢复精力下限」（吃药）。
2. 启动或接上任务，用 `tools/watch_run.py` 跟踪推进，结束时用 `tools/mcp/summarize_run.py` 收账。
3. 卡住/异常时先读 `logs/<局ID>/debug/` 里的 `ocr.txt`（先文本后图，遵守「读图」硬规则），再修复。
4. 多角色队列只走 `logs/mcp/next_captain.json` 的 `{"queue": "..."}` 注入（一次性参数）；**用户在 GUI 指定队长会清掉它**。禁止把队列写进配置或写死在代码里。
<!-- 变更 2026-11-10 · opencode · 开跑指令第 3 条的调试包路径改到 logs/<局ID>/debug/ · 与按局分目录同步，避免新会话按老路径找不到存证 -->

