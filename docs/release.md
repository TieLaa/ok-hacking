# 打包与发布

## 相关文件

| 文件 | 作用 |
| --- | --- |
| `.github/workflows/build.yml` | 监听 `v*` tag：跑测试 → （同步更新仓库）→ 用 PyAppify 打包 → 建 GitHub Release |
| `pyappify.yml` | 安装器配置：应用名、图标、`uac`、每个 profile 的 `git_url` / `main_script` / `requirements` |
| `deploy.txt` | 同步到「更新仓库」的文件清单（玩家实际会下到的内容） |
| `requirements.txt` / `requirements-docs.txt` | pip-compile 生成的锁定文件（运行时 / 文档） |
| `pyproject.toml` | 定义 `qt` / `docs` 两个依赖 profile，是锁定文件的来源 |

## 当前发布链路（2026-11-09 状态）

```
推 tag v*  ──► GitHub Actions (windows-latest)
                 ├─ Configure git / checkout / Python 3.12
                 ├─ pip install --no-deps -r requirements.txt
                 ├─ 跑 tests/*.py（任一文件失败即 fail）
                 ├─ Stamp version from tag   （把 tag 写进 src/config.py 的 version）
                 ├─ pyappify-action（钉 commit + v1.2.4）→ 产出 pyappify_dist/*-setup.exe
                 └─ softprops/action-gh-release 把这些 exe 挂到 Release

更新仓库（ok-hacking-update）由它自己的 workflow 反向拉取，不在本链路里 —— 见下一节。
```

玩家只下 Release 里的 `*-setup.exe`；装好之后的更新由 launcher 去 `pyappify.yml` 的 `git_url` 拉增量。
`pyappify.yml` 两个 profile 已指向 `TieLaa/ok-hacking-update`（2026-11-10），该仓库已建。

## 本地先打一次包（不用等 CI）

`pyappify` 的 pip 包只是运行时更新器，**打包要用 GitHub Releases 里的那个 Rust 启动器**（最新 v1.2.4）：

```powershell
# 1) 下载 pyappify.exe（https://github.com/ok-oldking/pyappify/releases/latest）
# 2) 把 pyappify.yml 的 git_url 临时指向本地仓库，先验证链路
#      git_url: "D:/workspace/ok-hacking"
# 3) 无头构建指定 tag 的完整离线包
.\pyappify.exe -c setup -p China --setup-version v0.1.1
```

产物：`pyappify.exe` + `pyappify.yml` + `data/`（Python / venv / 依赖 / 该 tag 的代码）+ `logs/` + `cache/`。
也可以直接双击 `pyappify.exe` 走 GUI 装一遍 —— 这是**唯一**能验证提权弹窗、安装路径中文检测、
Defender 提示的地方，CI 无桌面会话，验不了这些。

发布前最低限度要过一遍：本地 `-c setup` 能出包 → 装上 → 起 GUI → 跑一轮训练。

## 更新仓库（`ok-hacking-update`，由它自己拉源仓库）

`pyappify.yml` 的 `git_url` 指向**只含运行时文件**的 `ok-hacking-update`（内容由 `deploy.txt` 决定），
而不是源仓库。参考项目（ok-ww / ok-ef / ok-nte / ok-duet）都是这个结构。

**同步方向是反的**（2026-11-10 改）：不是源仓库 CI 往外推，而是更新仓库自己的
`workflow_dispatch` / 每日定时任务去拉公开的源仓库。这样**不需要 PAT、不需要 GitHub App**——
`GITHUB_TOKEN` 推自己所在的仓库是合法的，跨仓库推才需要令牌。

```
源仓库推 v* tag
   └─► 更新仓库的 .github/workflows/sync.yml（手动 Run workflow 或每天 03:17 UTC）
          ├─ 按 tag clone 源仓库（公开，匿名可读）
          ├─ 逐行读 deploy.txt 拷文件；源仓库不存在的条目（如 ok/）跳过
          └─ commit + 打同名 tag + push 到 main
```

前提与状态（2026-11-10）：

| 前提 | 状态 |
|---|---|
| `TieLaa/ok-hacking-update` 建为 public | 已完成 |
| `sync.yml` 与 README 入库 | 已完成 |
| `pyappify.yml` 的 `git_url` 指向该仓库 | 已完成 |
| **源仓库 `TieLaa/ok-hacking` 改为 public** | **待做**（否则更新仓库读不到源；玩家也下不了 Release 附件） |
| 源仓库的 `build.yml` 不再需要 Sync 步骤 | 已删除（避免依赖 PAT） |

发版顺序：推 `v*` tag → CI 出包并建 Release → 到更新仓库 Actions 点一次
**Sync from source**（填同一个 tag）→ 玩家 launcher 才能按新 tag 拉到增量。
漏了最后一步，已安装的用户更新时会停在旧版本。

第三方 Action 一律钉 commit SHA（`pyappify-action` 已钉），不要用 `@master`。

## 发版

```powershell
git tag -a v0.1.8 -m "v0.1.8"
git push origin v0.1.8      # 只推 tag，CI 自动出包并建 Release
```

推完 tag 还要做**第二步**：到 https://github.com/TieLaa/ok-hacking-update/actions
点 **Sync from source**，填同一个 tag。漏了这步，已安装用户的更新源会停在旧版本。

`v0.1.0` / `v0.1.1` 是发布链路修好之前的旧 tag，**不要推它们** —— 那时的 `build.yml` 还指向模板作者的仓库。

## 依赖变更后重编锁定文件

```powershell
python -m piptools compile --extra qt --strip-extras --no-header --output-file requirements.txt pyproject.toml
python -m piptools compile --extra docs --strip-extras --no-header --output-file requirements-docs.txt pyproject.toml
```

Qt 锁定文件用 `--no-deps` 安装。编译完成后删掉生成的 `pyside6` / `pyside6-addons` 条目，
但保留 `pyside6-essentials`，避免 Fluent Widgets 把整个 PySide6 metapackage 拖回来。
