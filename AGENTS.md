# ok-hacking 项目规则

## 读图（硬规则）

- **先读文本，再考虑看图。** `logs/debug/<时间>_<tag>/` 和 `logs/unknown/` 里的调试包都带 `ocr.txt`（文字+坐标）和 `actions.txt`，判断卡在哪一步够用。只有 OCR 漏判（按钮没识别出来）时才读图。
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
- **跑 Python 命令前固定 UTF-8 环境。** 本机系统码页是 936（GBK），且 `pwsh -NoProfile`（OpenCode 等工具以此启动）不读 `$PROFILE`。Python 的 stdout 一旦是管道就按 CP936 编码，`check_handlers.py` 里的 `✓` 会抛 `UnicodeEncodeError` 让脚本 exit 1——**判据其实没坏，是打印字符崩的**，别误判成回归失败。非交互调用前先设：

  ```powershell
  $env:PYTHONUTF8 = "1"
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
  ```

  本机交互式终端的同等设置已在 `$PROFILE` 里，这一条只覆盖非交互和跨机场景。
- **MCP 开发循环**（ok-script 2.0.16+）：改已注册任务用 `list_tasks` → 原地改源码 → `reload_tasks(task_ids)` → 用 `tools/mcp/probe.py` / `training.py` 验证，不要重启 GUI。任务类必须保持 `reset_to_start` / `verify_start_state` 可用（`src/tasks/attempt.py`），业务逻辑不复制进 harness。流程见 `docs/MCP_DEV.md`。
- 踩到新坑时，把「下次该怎么说」补进 `docs/PROMPT_PLAYBOOK.md`，不要只改代码。

