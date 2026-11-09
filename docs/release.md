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
                 ├─ [待恢复] Sync Repositories：按 deploy.txt 灌进 ok-hacking-update 并打同名 tag
                 ├─ pyappify-action（钉 commit + v1.2.4）→ 产出 pyappify_dist/*-setup.exe
                 └─ softprops/action-gh-release 把这些 exe 挂到 Release
```

玩家只下 Release 里的 `*-setup.exe`；装好之后的更新由 launcher 去 `pyappify.yml` 的 `git_url` 拉增量。
**更新仓库还没建**（见下），现在 `git_url` 指向源仓库，玩家会把整个仓库（含 `docs/shots`）拉下去。

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

## 更新仓库（还没建，建之前玩家会下全仓）

`pyappify.yml` 的 `git_url` 应该指向**只含运行时文件**的 `ok-hacking-update`（由 `deploy.txt` 决定内容），
而不是源仓库。参考项目（ok-ww / ok-ef / ok-nte / ok-duet）都是这个结构。

恢复步骤：

1. 建 public 空仓库 `TieLaa/ok-hacking-update`（launcher 匿名 clone，必须公开）；
2. 配 secret `UPDATE_REPO_TOKEN`（fine-grained PAT，只授权该仓库）；
3. `build.yml` 加回 `Sync Repositories` 步骤，指向该仓库，`sync_list: 'deploy.txt'`；
4. `pyappify.yml` 两个 profile 的 `git_url` 改成该仓库。

第三方 Action 一律钉 commit SHA（`pyappify-action` 已钉），不要用 `@master`。

## 发版

```powershell
git tag v0.1.2
git push origin v0.1.2      # 只推 tag，CI 自动出包并建 Release
```

`v0.1.0` / `v0.1.1` 是发布链路修好之前的旧 tag，**不要推它们** —— 那时的 `build.yml` 还指向模板作者的仓库。

## 依赖变更后重编锁定文件

```powershell
python -m piptools compile --extra qt --strip-extras --no-header --output-file requirements.txt pyproject.toml
python -m piptools compile --extra docs --strip-extras --no-header --output-file requirements-docs.txt pyproject.toml
```

Qt 锁定文件用 `--no-deps` 安装。编译完成后删掉生成的 `pyside6` / `pyside6-addons` 条目，
但保留 `pyside6-essentials`，避免 Fluent Widgets 把整个 PySide6 metapackage 拖回来。
