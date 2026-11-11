# 打包与发布

## 相关文件

| 文件 | 作用 |
| --- | --- |
| `.github/workflows/build.yml` | 监听 `v*` tag：跑测试 → （同步更新仓库）→ 用 PyAppify 打包 → 建 GitHub Release |
| `pyappify.yml` | 安装器配置：应用名、图标、`uac`、每个 profile 的 `git_url` / `main_script` / `requirements` |
| `deploy.txt` | 同步到「更新仓库」的文件清单（玩家实际会下到的内容） |
| `requirements.txt` / `requirements-docs.txt` | pip-compile 生成的锁定文件（运行时 / 文档） |
| `pyproject.toml` | 定义 `qt` / `docs` 两个依赖 profile，是锁定文件的来源 |

## 应用图标（`icons/`）

`pyappify.yml` 的 `icon:` 指向 `icons/icon.png`，安装包、快捷方式、窗口图标都吃这一个源；
`icons/icon.ico` 是同一张图的多尺寸版（16/32/48/64/128/256），给 Windows 壳用。

**换图流程**（不要在画图软件里手工裁，圆形遮罩和 ico 尺寸会不一致）：

```powershell
# 1) 抓帧：游戏停在有立绘的页面（时装选择 / 卡面 / 图鉴），存成 jpg
.venv\Scripts\python.exe tools\wgc_capture.py "$env:TEMP\opencode\icon_shots\icon_src_06.jpg"

# 2) 拖框：左边整帧、右边实时预览 256/64/32px 成品
.venv\Scripts\python.exe tools\pick_icon_box.py
#    默认读 %TEMP%\opencode\icon_shots\*.jpg|png，也可以把文件夹路径当第一个参数传进去
#    左键拖动圆心 / 滚轮或 +/- 改大小 / WASD 大调 / 方向键微调 / n·p 换帧 / Enter 保存 / Esc 放弃
#    无头自检：加 --check，只验证「读帧 → 裁剪 → 遮罩 → 预览」链路，不写文件

# 3) 存盘后 tools/pick_icon_box.py 会打印取景参数，抄进提交信息
#    BOX frame=icon_src_01.jpg x=388 y=23 side=320
```

存盘规则：框内裁正方形 → 缩到 320px → 圆形遮罩（外圈透明）→ 落 256px PNG + 六尺寸 ICO。
之所以是 256 而不是更大：launcher 和资源管理器最大只用到 256，再大只是白占体积。

要点：

- **右侧 32px 预览必须能认出是谁**。换成半身/全身构图时尤其要看这一格——细节在 32px 会糊成一团。
- **改完必须重新打包才生效**（`deploy` 出安装包）；已经装好的 `ok-hacking` 进程还是旧图标。
- 取景参数只是**记录**用的，不参与生成：下次想复现同一版，照着 `frame / x / y / side` 重新拖即可。
- 拖不动、鼠标没反应，先怀疑窗口工具链而不是坐标：v1（拖边）和 v2（HighGUI 整块跟鼠标）都栽在
  OpenCV 的鼠标回调收不到事件上，v3 换成 PySide6 才稳。

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
`pyappify.yml` 两个 profile 已按 region 分开指向（2026-11-10）：China → cnb.cool，Global → GitHub，见「国内更新源」一节。

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

## 国内更新源（cnb.cool，2026-11-10 建）

China profile 的 `git_url` 不指向 GitHub，而是 cnb.cool 上的同名镜像仓库：

| 项 | 值 |
|---|---|
| 仓库 | https://cnb.cool/TieLaa/ok-hacking-update （公开，匿名可 clone） |
| 组织 | `TieLaa`（腾讯 CNB，注册需实名认证） |
| 同步方向 | **GitHub Actions 事件驱动推送**，没有定时抓取 |
| 凭据 | GitHub 更新仓库的 Actions secret `CNB_TOKEN`（cnb 访问令牌，用户名固定 `cnb`） |

推送逻辑在**更新仓库**的 `sync.yml` 末尾：commit + 打 tag + push 到 GitHub 之后，顺手把
`main` 和所有 tag 强推到 cnb。发版那一刻两边就一致，不存在「等下一次抓取」的空窗。

```
源仓库推 tag → sync.yml（手动 Run 或 03:17 定时）→ push GitHub 更新仓库 → push cnb 镜像
```

cnb 仓库里的 `.cnb.yml` 已清空（2026-11-10）：早先那版是 cnb 定时 pull，已废弃 ——
不要在那里再放 crontab，两套机制同时写同一个 main 会互相覆盖。

免费额度（`https://docs.cnb.cool/zh/pricing.md`）：仓库存储 100 GiB、构建 160 核时/月。
只当镜像仓库基本用不到。更新仓库里只放 `deploy.txt` 列的运行时文件，**不要放安装包**。

### 安装包镜像（`TieLaa/ok-hacking-dist`，2026-11-10 建）

安装包不放更新仓库，单独放 cnb 仓库 `https://cnb.cool/TieLaa/ok-hacking-dist` 的
`dist` 分支，由 `export-to-cnb.yml` 推送。

为什么必须分开（v0.1.10 事故）：pyappify 打包时会**整仓克隆更新仓库**，dist 分支里的
安装包会被一起塞进下一版安装器 —— v0.1.9 的 211 MB 让 v0.1.10 的 setup.exe 涨到 425 MB，
超过 cnb 单文件 256 MiB 的 blob 上限，`export-to-cnb.yml` 直接被 pre-receive hook 拒掉。
搬走后安装包回到 ~211 MB。更新仓库里遗留的 dist 分支用 `Cleanup cnb update-repo dist`
工作流删除。

::: tip 同步链路的前提
`sync.yml` 靠匿名 `curl` 读源仓库的 tag 列表，所以**源仓库必须是 public**（2026-11-10 已转）。
另外 `Check out this repo` 那步必须 `fetch-depth: 0` —— 浅克隆推 cnb 会被
`shallow update not allowed` 拒掉。
:::


## 发版

顺序不能反（v0.1.10 第一次就是这么挂的）：**pyappify 打包时要从更新仓库 checkout 同一个
tag，更新仓库里没有这个 tag，构建就失败**。

```powershell
git tag -a v0.1.11 -m "v0.1.11"
git push origin v0.1.11     # 触发 Build
```

推完 tag **立刻**去 https://github.com/TieLaa/ok-hacking-update/actions 跑
**Sync from source**（填同一个 tag）。它 20 秒就完事，而 Build 走到打包步骤要 10 分钟，
赶得上。

如果 Build 已经失败（日志里是 `Tag 'vX.Y.Z' not found locally after fetch`），
**不用重新打 tag**：`gh run rerun <run-id> --failed` 重跑失败的 job 就行。

出包成功后，再跑 `Export release to cnb`（填同一个 tag），把 China 包装到
cnb 的 dist 仓库；`Sync from source` 同一次 Run 里已经把 main + tags 推给 cnb 镜像，
国内更新源不会停在旧版本。

`v0.1.0` / `v0.1.1` 是发布链路修好之前的旧 tag，**不要推它们** —— 那时的 `build.yml` 还指向模板作者的仓库。

## 依赖变更后重编锁定文件

```powershell
python -m piptools compile --extra qt --strip-extras --no-header --output-file requirements.txt pyproject.toml
python -m piptools compile --extra docs --strip-extras --no-header --output-file requirements-docs.txt pyproject.toml
```

Qt 锁定文件用 `--no-deps` 安装。编译完成后删掉生成的 `pyside6` / `pyside6-addons` 条目，
但保留 `pyside6-essentials`，避免 Fluent Widgets 把整个 PySide6 metapackage 拖回来。
