# 研究委托：Unity 客户端「鼠标输入」还有没有更好的方案

> 委托人：Windows 上的 OpenCode（ok-hacking 项目）
> 受托：NAS Hermes
> 日期：2026-10-28
> 用途：给当前「伪后台 + 抢前台 + 抢光标」的输入方案找替代路径；只做调研，不改代码。

---

## 0. 一句话背景

`ok-hacking` 是《篮球少女：飞跃巅峰》Windows 客户端（Unity，`UnityWndClass`）的自动化脚本，基于 ok-script 2.0.0b7（Python 3.12）。
现在**唯一可用**的输入方式是「真实鼠标事件 + 每次点击前把游戏抢到前台 + 移动物理光标」，一局 13~16 分钟、700~900 次点击，期间用户基本无法用电脑。
需要你大范围检索：**有没有办法在不抢前台、不抢光标的前提下，让这个 Unity 客户端接收点击**；如果没有，退而求其次的工程折衷是什么。

---

## 1. 环境事实（先读，别推翻）

| 项 | 值 |
| --- | --- |
| 系统 | Windows（`win32`，双显示器，虚拟桌面 5120×1440） |
| 游戏进程 | `k3_windows_ob.exe`，窗口类 `UnityWndClass`，客户区 1920×1080 |
| 引擎 | Unity（旧 Input Manager / 新 Input System 未知，这是待查项） |
| 框架 | ok-script 2.0.0b7，Python 3.12 |
| 截图 | WGC（Windows Graphics Capture），客户区=捕获帧，坐标 1:1 |
| 当前输入实现 | `src/interaction/GirlsInteraction.py`（自定义 `BaseInteraction`） |
| 命令行工具 | `tools/abs_mouse.py`（`mouse_event(MOVE\|ABSOLUTE\|VIRTUALDESK)` + 点击） |

---

## 2. 已经实测确认的事实（**不要重新论证，直接当约束**）

### 2.1 消息注入全部失败

1. `PostMessage(WM_LBUTTONDOWN/UP)` **完全无效**：返回成功，游戏毫无反应。试过四种组合全废：
   - 裸消息；
   - 先发 `WM_ACTIVATE(WA_ACTIVE)` 伪激活再发点击；
   - 先 `SetCursorPos` 把光标移到目标再发点击；
   - 游戏已经在前台时发消息。
2. 游戏进程若以**管理员**启动，非管理员脚本 `PostMessage` 直接「拒绝访问」（UIPI）；但**同一场景下真实 `mouse_event` + WGC 正常**，无需同权限。
3. 键盘：`keybd_event` 有效；后台 `PostMessage` 按键**会延迟生效**（ESC 在下次窗口置顶时突然触发）。

### 2.2 光标与点击

4. `SetCursorPos` 瞬移**游戏感知不到**（不产生 raw input），点击位置会错。
5. `mouse_event(MOVE, dx, dy)` 相对小步移动**有效**，但要几十上百步，慢且全程霸占鼠标。
6. `mouse_event(MOVE|ABSOLUTE|VIRTUALDESK)` 一步到位**有效**，坐标按虚拟桌面归一化到 0~65535。**当前统一用这个。**
7. 点击按住时长 **<100ms 会被丢弃**（70ms 点「启程」无反应），现在统一 `max(down_time, 0.10)`。
8. 点击后用 `SetCursorPos` **还原**光标是安全的、不影响游戏内部光标（因为它不产生 raw input）；下一次点击会重新绝对定位。这是为了缓解「脚本抢鼠标」。

### 2.3 前台

9. `SetForegroundWindow` 会被 Windows **静默拒绝**（不抛异常，窗口没上来）→ 真实点击落到别的窗口上（实测点到 ok-script 自己的 GUI）。
10. 可靠的绕法是：`keybd_event(VK_MENU, 0)` → `SetForegroundWindow` → `KEYEVENTF_KEYUP` 抬起 Alt；失败再叠加 `AttachThreadInput`、`SetWindowPos(HWND_TOPMOST)` 再撤回。
11. **Alt 必须 `finally` 抬起**，否则后续所有按键都变成 Alt 组合键；并且注入的 Alt 会与注册的全局热键凑成组合键（曾凑出 Ctrl+Alt+F12 触发别的程序，热键已改 Ctrl+Alt+Shift+F12）。
12. 抢不到前台就**放弃点击并抛异常**，不能硬点。
13. 「最小化再恢复窗口」不能用来抢前台：前台是管理员进程时恢复失败，窗口反复 `visible=False`，WGC 无帧、点击全落空。

### 2.4 渲染与窗口

14. 窗口**完全被遮挡或最小化**时 WGC 收不到新帧（日志 `no frame for 10 sec`）；**部分可见**（哪怕不在前台）就正常出帧。

### 2.5 行为观测

15. 一局 703 步 / 814 次点击，用时 11 分 57 秒（另一局 13~16 分钟、700~900 次）。
16. 转场动画期间点击会被吞，注册球员页的「下一步」稳定需要连点 2~4 次。
17. 有的按钮必须点在**文字本身**：全屏「点击屏幕继续」点屏幕正中无效，点那行字才有效。
18. 跨项目对照（本机装过）：**ok-ww（鸣潮，UE）能完全后台运行、不抢鼠标**；**ok-endfield（终末地，Unity）是伪后台，会抢鼠标**。网上的解释是「UE 读 lParam 坐标，Unity 读 `GetCursorPos()`」——但本项目实测 **`SetCursorPos` 单独用无效**，这条解释与本项目现象不符，需要你查清 Unity 到底读什么。

---

## 3. 现在仍然没解决的问题（按痛感排序）

| # | 问题 | 影响 |
| --- | --- | --- |
| P1 | 每次点击都抢前台 + 移动物理光标 | 跑脚本时电脑基本不能用；这是「真后台」的核心诉求 |
| P2 | 抢前台靠 Alt 注入，成功率不稳（GUI 弹到前台时更差），失败要重试 4 次 | 偶发整段流程卡住/空点 |
| P3 | 无法区分「点击被吞（动画中）」和「坐标点错」 | 用连点兜底，浪费时间、加剧抢鼠标 |
| P4 | 窗口必须保持可见（渲染限制） | 与输入方案独立，但一起卡住「无人值守」 |
| P5 | 双屏 + 150% DPI 缩放下绝对坐标归一化未系统验证 | 换机器/改缩放可能整体偏 |
| P6 | Alt 注入的副作用（全局热键、Alt 卡住） | 偶发按键异常 |

---

## 4. 请你研究的问题（深度检索 + 给可落地结论）

> 要求：**先给结论表，再给依据**。每条结论标 `已证实 / 未证实 / 推测`，附来源链接（优先 Microsoft Learn、Unity 官方文档/issues、GitHub 仓库与 issue、可复现的实测帖）。不要照抄博客结论，冲突时说明冲突点。

### Q1 输入注入层：Windows 上让 Unity 客户端在**非前台**接收鼠标点击，到底有哪些可行手段？

逐项给「原理 / 是否需要改游戏 / 是否需要驱动或签名 / 是否抢光标 / 兼容性与风险 / 有无公开实测」：

1. `PostMessage` / `SendMessage` 家族：`WM_LBUTTONDOWN/UP`、`WM_MOUSEMOVE`、`WM_ACTIVATE`、`WM_MOUSEACTIVATE`、`WM_NCHITTEST`；Unity（IMGUI / UGUI / 新 Input System）分别怎么处理这些消息，为什么可能"返回成功但无反应"。
2. `SendInput` / `mouse_event` / `pydirectinput` / `interception` 驱动 / `ViGEmBus` / HID 注入 / 内核级注入（`winio`、`MouClass` filter）——逐个说明「是否仍要求目标窗口在前台」「是否产生 raw input」「反作弊/驱动签名要求」。
3. **UIAccess 清单（`uiAccess=true` + 代码签名 + Program Files 安装）**：能否绕过前台锁、能否向更高完整性进程（管理员游戏）`PostMessage`？现实中的限制（必须是受信任路径、需要 EV/普通签名、Win11 的变化）。
4. `SetForegroundWindow` 前台锁的官方手段：`AllowSetForegroundWindow`、`SPI_SETFOREGROUNDLOCKTIMEOUT`、`AttachThreadInput` 正确调用顺序、`SwitchToThisWindow`、`SetWindowPos` 置顶组合；哪个在「目标进程管理员 + 脚本非管理员」下仍有效。
5. 为什么 `SetCursorPos` 无效而 `mouse_event` 有效：从 Unity 输入栈（旧 Input Manager / Input System / Raw Input / Windows.Gaming.Input）解释，并给出**可判定的验证实验**（不写代码也行，给判据和工具）。

### Q2 会话/桌面隔离：真正"不抢用户鼠标"的工程解

1. Windows 第二会话 / RDP loopback（同一台机器开一个 RDP 会话跑游戏）：Home 版限制、GPU 渲染是否正常、WGC 能否捕获、是否需要额外授权、开源实现。
2. 独立窗口站/桌面（`CreateDesktop` + `SwitchDesktop`）：能否让游戏在自己的桌面收输入而用户桌面不受影响？真实可行性。
3. 虚拟机 / 双机（Hyper-V GPU-P、GPU 直通、物理第二台机 + 串流）：延迟、显卡要求、成本、是否适合 700+ 次点击的 UI 自动化。
4. 虚拟显示器 + 隐藏会话（如 `IddSampleDriver`）是否能让 Unity 在"看不见"的情况下继续渲染并接受输入。

### Q3 绕过输入层：直接操作 Unity

1. BepInEx / MelonLoader 注入后调用 `EventSystem.ExecuteEvents` / 构造 `PointerEventData` 模拟点击，或直接调用业务方法：可行性、版本更新成本、封号/反作弊风险。
2. Unity 的 `-batchmode` / 远程调试 / `PlayerPrefs` / IL2CPP 反射等官方通道有没有输入注入入口。
3. 有没有「不改游戏二进制」的中间层（例如注入 `user32` 的 shim、API hook）。

### Q4 如果都不行：工程折衷怎么做才最不打扰

1. 只在需要时抢前台、点完立刻还原光标（我们已做）之外，还有哪些降损手段（合并点击、降低频率、批量操作、点击队列）。
2. ok-endfield（Unity，伪后台）具体怎么做的，有没有可复用的更优实现（它的 `Mouse.py`）。
3. 有没有「半后台」：不抢前台但允许抢光标？或抢前台但不动光标？

### Q5 可判定的实验设计

给 3~5 个「今天就能做、10 分钟内出结论」的实验，用来区分：
- 游戏读的是 raw input 还是 `GetCursorPos`；
- 失败原因是「消息没送到」还是「送到了但 UI 不认」；
- 前台锁是否是唯一障碍（如用 `AllowSetForegroundWindow` 后是否还失败）。
工具建议（ETW / Raw Input 日志 / API Monitor / Spy++ / Windows 消息监视）。

---

## 5. 交付要求

1. **输出文件**：写到容器内 `/opt/data/outbox/mouse-research-2026-10-28.md`（宿主机 `/share/Container/hermes-agent/outbox/`），同时在 kanban 任务下留一条评论指向该文件。
2. **结构**：
   - 结论表（方案 / 能否真后台 / 是否抢光标/前台 / 实现难度 / 风险 / 证据）；
   - 每条的详细依据与链接；
   - 「建议下一步」按投入产出排序，最多 3 条；
   - 明确列出「查不到 / 不确定」的项，不要编。
3. **中文**，链接用完整 URL（用户会直接点开）。
4. **不要修改** `D:\workspace\ok-hacking` 任何文件；不要用 `oc` 派发会让 Windows 端改代码的任务（Windows 机器要关机）。纯调研。
5. 若需要更多实现细节，可在 `/opt/data/inbox/mouse-2026-10-28/` 读我放的两份源码；**不要**去 clone 或遍历整个仓库。

---

## 6. 附：当前实现要点（摘自源码，供你判断）

- `ensure_foreground()`：Alt 注入 → `SetForegroundWindow` → `AttachThreadInput` → `HWND_TOPMOST` 再撤回，共 4 轮；失败抛异常。
- `_move_to()`：`mouse_event(MOVE|ABSOLUTE|VIRTUALDESK, nx, ny)`，按虚拟桌面归一到 0~65535，settle 40ms。
- `click()`：抢前台 → 保存光标 → 绝对移动 → `LEFTDOWN` → `sleep(>=0.10)` → `LEFTUP` → `SetCursorPos` 还原。
- `sweep/swipe`：绝对移动分 12 步 + 按住左键。
