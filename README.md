# 篮球少女 OK（ok-hackingGirls）

基于 [ok-script](https://github.com/ok-oldking/ok-script) 的《篮球少女：飞跃巅峰》Windows 客户端自动化项目。

## 当前状态

- 框架已搭建，GUI 可启动
- 已识别游戏窗口：进程 `k3_windows_ob.exe`，窗口类 `UnityWndClass`，客户区 1920x1080（16:9）
- `src/tasks/AutoBattleTask.py` 是后台自动战斗骨架，识别逻辑待补充
- `src/tasks/InputTestTask.py` 是输入链路自检任务（点击 + 前后画面差异）
- 模板库里还没有 `battle_mark`，任务启用后会跳过并提示

## 三个硬约束（实测得出）

1. **必须管理员启动**。游戏进程是管理员权限，非管理员进程给它发消息会被 UIPI 拦掉（`PostMessage` 返回「拒绝访问」）。
2. **路径必须纯英文**，ok-script 会拒绝中文路径。
3. **游戏窗口必须可见**。窗口被完全遮挡或最小化时，WGC 拿不到新帧（日志 `no frame for 10 sec`），任务无从判断。

## 环境

- Python 3.12
- 项目路径：`D:\workspace\ok-hackingGirls`

```powershell
cd D:\workspace\ok-hackingGirls
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple/ --no-deps -r requirements.txt
```

## 运行

```powershell
# 普通权限（只能截图，不能操作游戏）
.\.venv\Scripts\python.exe main_debug.py

# 管理员（能操作游戏）
Start-Process -FilePath "D:\workspace\ok-hackingGirls\.venv\Scripts\python.exe" `
  -ArgumentList "main_debug.py" -WorkingDirectory "D:\workspace\ok-hackingGirls" -Verb RunAs
```

首次启动会初始化 OCR 模型，需要等待一会儿。

## 目录

| 路径 | 作用 |
| --- | --- |
| `src/config.py` | 应用配置：窗口、截图/输入方式、注册任务 |
| `src/tasks/AutoBattleTask.py` | 后台自动战斗任务（TriggerTask） |
| `src/tasks/InputTestTask.py` | 输入链路自检（一次性任务） |
| `src/tasks/MyOneTimeTask.py` | 模板自带的配置项演示任务，可删除 |
| `assets/coco_annotations.json` | 模板匹配素材的 COCO 标注 |
| `tools/wgc_capture.py` | 脱离框架抓游戏画面（被遮挡也可） |
| `configs/` | 运行时生成的配置 |
| `logs/` | 运行日志 |
| `screenshots/` | Debug 截图（每次启动清空） |

## 下一步

1. 确认输入链路：运行「输入测试」任务，看点击是否生效。
2. 启动游戏，进入一场比赛，截图并框选 `battle_mark` 模板。
3. 在 `AutoBattleTask.run()` 里补上技能检测与按键逻辑。
4. 用 `python -m unittest tests.TestMain` 验证。

## 注意

- ok-script 使用 AGPL-3.0，公开分发修改版需要开源。
- 游戏自动化可能违反用户协议，账号风险自行评估。
