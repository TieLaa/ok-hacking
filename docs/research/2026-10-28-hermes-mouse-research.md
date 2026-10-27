# Unity 客户端（k3_windows_ob.exe）鼠标输入方案调研

> 委托：ok-hacking / Windows OpenCode · 受托：NAS Hermes · 日期：2026-10-28
> 范围：纯调研，未改任何代码。结论按 `已证实 / 未证实 / 推测` 标注，依据全部附链接。

---

## 0. 结论先行

### 0.1 一句话机制（本报告所有推论的根）

**Windows 的鼠标输入只投递给"当前输入桌面上的前台窗口"；Unity 的一次点击 = 输入流里的一个按钮事件 + 该事件发生瞬间的系统光标位置。**

推论三条：
1. 消息注入（`PostMessage`）只往目标线程消息队列塞消息，**不产生输入流事件** → Unity 输入层看不到 → "返回成功但毫无反应"。**必失效**，不是参数没调对。
2. 只改光标（`SetCursorPos`）**不产生事件** → 游戏不认。但"改光标 + 发事件"（`SetCursorPos` + `mouse_event(LEFTDOWN)`）有效，因为事件里的坐标取的是当时的光标位置。
3. 真事件只能进前台窗口。所以**不改游戏的前提下，"非前台 + 收点击"不存在第三条路**；能动的只有"谁当前台"（会话/桌面隔离）或"绕过 OS 输入路由"（in-process 注入）。

### 0.2 结论表

| # | 方案 | 非前台可点 | 抢前台 | 抢光标 | 难度 | 风险 | 证据状态 |
|---|---|---|---|---|---|---|---|
| 1 | `PostMessage/SendMessage` WM_LBUTTONDOWN / WM_MOUSEMOVE / WM_ACTIVATE / WM_NCHITTEST | ❌ | 否 | 否 | 低 | 无 | **已证实无效**（实测 4 种组合 + 文档机制） |
| 2 | `SendInput` / `mouse_event` / `pydirectinput`（现状） | ❌ | 是 | 是 | 低 | 无 | **已证实有效**（唯一可行） |
| 3 | Interception 内核驱动 | ❌ | 是 | 是 | 中 | 需签名/测试模式 | 推测（同 #2 的输入路由，只是注入点更底层） |
| 4 | ViGEmBus | 不适用 | — | — | 中 | 项目已退役 | **已证实**：它模拟的是 XInput **手柄**，不是鼠标 |
| 5 | 硬件 HID / USB 注入（Arduino、usbip、虚拟 USB） | ❌ | 是 | 是 | 高 | 需硬件 | 推测（真设备事件同样只进前台窗口） |
| 6 | UIAccess 清单（`uiAccess=true`） | ❌（对 P1 零收益） | 是 | 是 | 中 | 需签名 + 装 Program Files | **已证实部分**：只解决 obs#2 的 UIPI 拒绝，不解决 Unity 不认消息 |
| 7 | `CreateDesktop` + `SwitchDesktop` 独立桌面 | ❌（用户桌面被切走） | 是（但永不失败） | 是 | 中 | 低 | **已证实**（`SwitchDesktop` 语义），对 P1 无效、**对 P2 有效** |
| 8 | 第二 RDP 会话（RDPWrap / Server SKU） | ✅ | 否 | 否 | 高 | 换 `termsrv.dll`；Win11 支持未证实；会话内 GPU 渲染隐患 | **部分证实** |
| 9 | VM + GPU-P（Easy-GPU-PV / Hyper-V） | ✅ | 否 | 否 | 高 | 需 Pro/Enterprise + 支持的 GPU | 未证实（本机条件未知） |
| 10 | 物理第二台机 + 串流 | ✅ | 否 | 否 | 高 | 成本 / 延迟 | 已证实可行（工程常识级） |
| 11 | `IddSampleDriver` 虚拟显示器 | 只解决"看不见"，不解决"收输入" | 否 | 否 | 中 | 需签名驱动 | **已证实机制** |
| 12 | BepInEx / MelonLoader 进程内注入，直接喂 UGUI | ✅（唯一真后台） | 否 | 否 | 高 | 版本维护 / 反作弊 / 封号 | **已证实机制**，本游戏可行性未证实 |
| 13 | 进程外 hook `user32`（shim / Detours / AppInit） | ❌ | — | — | 高 | — | 推测（跨进程 hook 用户态 API 不成立） |

---

## 1. 机制：Unity 到底读什么（回答 obs#18 的冲突）

### 1.1 三条实测的统一解释

你们的实测放一起看，只剩一个自洽模型：

| 实测 | 模型给出的解释 |
|---|---|
| obs#1 `PostMessage` 四种组合全废（含游戏已在前台） | 消息进了队列，但输入流里没有事件，Unity 输入层不产生 `GetMouseButtonDown` |
| obs#4 `SetCursorPos` 单用无效 | 光标动了，但没有事件 → 无点击 |
| obs#6 `mouse_event(ABSOLUTE)` 一步到位有效 | 光标移动 + 按钮事件一起进输入流 → 有效 |
| obs#8 `SetCursorPos` 还原安全、不影响游戏内光标 | 点击已完成，游戏不持续轮询光标位置 → **反证"轮询光标"模型** |
| obs#42 后台 `PostMessage` 的 ESC 会在下次置顶时生效 | 消息确实躺在窗口消息队列里，被 `DispatchMessage` 后才进引擎 → **消息路由是通的，是"翻译层"断了** |

**结论：博客那句"UE 读 lParam 坐标，Unity 读 `GetCursorPos()`"是错的（至少对本项目）。**
如果是 `GetCursorPos` 轮询，obs#4 的 `SetCursorPos` 就该生效，obs#8 的还原就该让 hover 错位。两条都与实测相反。
正确的说法是：**Unity 消费的是输入事件流（Windows 上来自 raw input / 光标锁定时由引擎自己注册的 `RIDEV_INPUTSINK`），事件里的位置取当时的光标坐标；它不读 `lParam`，也不轮询光标。**
状态：**推测**（引擎源码不可见），但被上表 5 条实测约束到只剩这一种解释。第 6 节 E1/E2 给 10 分钟内可判定的实验。

### 1.2 为什么原生 Win32 程序用 `PostMessage` 就能点，Unity 不行

原生程序的 `WndProc` 收到 `WM_LBUTTONDOWN` 会**自己**把它翻译成业务点击——消息就是它唯一的输入源。
Unity Player 不走这条路：`WM_LBUTTONDOWN` 不是它的输入源，它的输入源是 raw input 流；UGUI 的点击由 `StandaloneInputModule` 每帧读 `Input.mousePosition` / `Input.GetMouseButtonDown` 生成，`Input` 由输入层填，不是由窗口消息填。
所以网上大量"`PostMessage` 后台点击 XX 游戏"的帖子对**自绘 WndProc 的游戏**有效，对 Unity/UE 这类引擎必失效。
状态：**推测**（Unity 输入栈实现不可见），机制与 obs#1/#42 完全一致。

**顺带排掉一条常见误诊**：Unity 桌面端的鼠标不可能来自 `Windows.Gaming.Input`——那个命名空间只有 Xbox 手柄 / 赛车方向盘 / 街机摇杆，Windows 没有给鼠标键盘提供对应的托管 API（鼠标在 UWP 侧走 `Windows.UI.Input`/`CoreWindow` 指针事件，桌面 Player 不适用）。所以"Unity 读什么"的候选只剩两个：raw input，或窗口消息翻译。E1/E2 就是用来在这两个里二选一。**已证实**（`Windows.Gaming.Input` 覆盖范围）。

---

## 2. Q1 输入注入层

### 2.1 `PostMessage` / `SendMessage` 家族 — 已证实无效，别再试

- **文档**：`PostMessage` 在 Vista+ 受 UIPI 限制，"发送方进程的完整性级别必须不低于接收方"。
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-postmessagew
  这解释了 obs#2（游戏管理员 → `拒绝访问`）。
- **发送方可以调 `ChangeWindowMessageFilterEx` 放行特定消息——但必须由*目标窗口*所在进程调用**，第三方脚本无权修改别人的过滤器。所以对"管理员启动的游戏"没有合法绕法（除非脚本自己也管理员，或 UIAccess，见 2.3）。
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changewindowmessagefilterex
- **`WM_ACTIVATE`/`WM_MOUSEACTIVATE` 伪激活也没用**：`GetForegroundWindow` 的答案由窗口管理器维护，不是由谁发了什么消息决定；`WM_ACTIVATE` 只是通知，不改系统状态。
- **`WM_NCHITTEST`** 是给非客户区命中的，对 UGUI 零作用。
- **IMGUI / UGUI / 新 Input System 的差异对本问题无意义**：三者都以输入层为源，都不读 `WM_LBUTTONDOWN`。差别只体现在"会不会因为失焦而重置设备"（见 2.5）。
- 唯一"能看见"的现象是 obs#42：键盘消息进队列，窗口被真正激活、消息循环跑到时才被翻译 → **这就是"消息送到了但翻译层不认"的直接证据**。

### 2.2 `SendInput` / `mouse_event` / 驱动级 / HID — 都受"前台"约束

| 手段 | 是否仍要前台 | 是否产生 raw input | 签名/反作弊 |
|---|---|---|---|
| `mouse_event` / `SendInput` | **是** | **是**（注入进系统输入流，与真设备合并） | 无 |
| `pydirectinput` | 是 | 是（就是对 `SendInput` 的封装，额外处理 DirectInput 扫描码） | 无 |
| Interception（`oblitum/Interception`） | 是 | 是（内核 filter driver，在设备栈里插入事件，之后仍走系统输入路由） | **需驱动签名**（或测试签名模式，重启/水印） |
| ViGEmBus（`nefarius/ViGEmBus`） | — | — | **不适用**：ViGEm 是虚拟 **XInput/DualShock 手柄**，仓库 README 已标 retired；游戏要鼠标，给它一个手柄没用 |
| 硬件 HID（Arduino/USB 注入/usbip 转发真鼠标） | 是 | 是（而且是最"真"的 raw input） | 需硬件；不破解任何东西 |
| `MouClass` filter / `winio` 类内核注入 | 是 | 是 | 驱动签名 + 蓝屏风险 |

依据：
- `SendInput`：把事件**插入系统输入流**；受 UIPI 限制——"只允许注入到同级或更低完整性级别的应用"，且**失败时返回值和 `GetLastError` 都不指示失败**。
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
- Raw Input：应用只有在 `RIDEV_INPUTSINK` 下才能在**后台**收到数据；否则只有前台窗口收到。
  https://learn.microsoft.com/en-us/windows/win32/inputdev/about-raw-input
- **冲突点（重要）**：文档说 `SendInput` 受 UIPI 限制，但 obs#2 观测到"游戏以管理员启动时，非管理员脚本的 `mouse_event` 正常"。两者矛盾。可能原因：(a) UIPI 对注入事件的拦截实际发生在"投递给更高 IL 的前台窗口"这一步，而你们的点击落在游戏窗口时游戏本就是前台，路径不同；(b) 你们的脚本本身也在管理员上下文（未确认）。**这条我不下结论**，第 6 节 E3 给判定方法。
- **`Interception` 为什么帮不上**：它能做到"事件看起来来自某个具体物理设备"，但事件进流之后仍然按"前台窗口"路由。它解决的是"游戏区分真实设备/注入设备"这类反作弊问题，不解决前台问题。
- **驱动签名要求（现实门槛）**：用户态注入（`SendInput`）零签名要求。内核级方案（Interception、MouClass filter、任何自定义输入驱动）在 64 位 Windows 上必须**有受信任链的签名**，否则只能开测试签名模式（桌面水印 + 每次启动要绕过驱动签名强制）或禁用强制签名——这不是"难不难"的问题，是"能不能装到干净机器上"的问题。而**开了签名/加了驱动恰恰最容易触发反作弊**。综合：内核路线对本项目是"成本最高、收益为零（前台约束不变）、风险最大"的三重亏，直接排除。

**Q1.2 结论**：所有注入手段在"事件进系统输入流"这一步之后走同一条路由 → **没有任何一种能让非前台窗口收到点击**。差别只在"注入点更低级、更难被反作弊识别"（Interception/硬件 HID），与"后台"无关。所以 Q1 的答案是：**注入层无解，必须在"谁当前台"或"绕过输入层"上解。**

### 2.3 UIAccess 清单 — 对 P1 零收益，不要做

- **能做什么**（已证实，两份官方文档）：
  - `uiAccess="true"` 的用途是"bypass user interface protection levels and drive input to higher-permission windows on the desktop" —— 即：能向**更高完整性级别**的窗口发消息/注入输入。
    https://learn.microsoft.com/en-us/windows/win32/sbscs/application-manifests
  - 前置条件：**必须代码签名**，且**必须安装在受信任位置**（如 `Program Files`），否则 UAC 不会授予 UIAccess 令牌。
    https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-securityoverview
- **不做什么**（关键）：它**不改变 Unity 不认 `WM_LBUTTONDOWN` 这件事**。obs#1 的四组失败里包含"游戏已在前台、非管理员"的情形，那条路径上没有任何 UIPI 拦截，仍然无效。**UIAccess 只可能救 obs#2，而 obs#2 你们已经用 `mouse_event` 绕开了 → 收益为零，成本是证书 + 安装位置约束。**
- **未证实**：流传的"UIAccess 进程可以无视前台锁直接 `SetForegroundWindow`"我没有在本次抓到的一手文档里找到明确句子（`SetForegroundWindow` 页和 `LockSetForegroundWindow` 页都没提 UIAccess）。按未证实处理。
- **未证实**：是否需要 EV 证书（实践帖多说普通受信任链签名即可）。**Win11 是否有额外收紧**：没查到，标"查不到"。

### 2.4 `SetForegroundWindow` 前台锁 — 现有 Alt 技巧是官方依据，可以保留

已证实（`LockSetForegroundWindow` 页原文）：

> "The system automatically enables calls to `SetForegroundWindow` if the user presses the ALT key or takes some action that causes the system itself to change the foreground window (for example, clicking a background window)."

https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-locksetforegroundwindow

**这就是你们 obs#10 那个 Alt 技巧的官方依据**——不是野路子，是文档描述的机制。副作用（obs#11 的 Alt 卡住、凑热键）是注入方式的代价，不是方法错。

其余手段的定位（`SetForegroundWindow` 限制清单见 https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow ）：

| 手段 | 是否有效 | 说明 |
|---|---|---|
| `AllowSetForegroundWindow(ASFW_ANY)` | 有限 | 它只能由**当前持有前台权**的进程调用，把置前权让给别人。**如果脚本自己就没有置前权，调它没有任何作用** → 对 obs#9（被静默拒绝）通常无效。未证实其对"目标进程管理员"的额外效果。 |
| `SPI_SETFOREGROUNDLOCKTIMEOUT = 0` | 未证实 | 对应 `HKCU\Control Panel\Desktop\ForegroundLockTimeout`，改完需广播/重登。实践上能减少失败，但没有官方保证"永不被拒"。建议作为**降低重试次数的辅助**，不要当主方案。 |
| `AttachThreadInput` | 有效 | 把脚本线程的输入队列挂到前台窗口线程上，置前后再解挂。文档只描述"attaches the input processing mechanism of one thread to that of another"，**组合顺序没有官方保证**，属于社区经验。https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-attachthreadinput |
| `SwitchToThisWindow` | 未证实 | 未文档化/半文档化 API，行为随版本漂移，不建议新增依赖。 |
| `SetWindowPos(HWND_TOPMOST)` | 只改 Z 序 | 不改前台，不能替代。 |
| **Alt 注入** | **有效**（现状） | 官方机制（见上引文）。你们的 4 轮组合已经比 ok-endfield 的实现更稳。 |

**在"游戏管理员 + 脚本非管理员"下仍然有效的**：Alt 注入 + `SetForegroundWindow`（置前本身不受 IL 限制，IL 限制的是消息/注入/hook）。所以 obs#13 描述的"前台是管理员进程时恢复失败"更可能与窗口状态/前台锁有关，而非 IL——状态：推测。

### 2.5 新 Input System 的坑（如果游戏用的是它）

`InputSettings.BackgroundBehavior` 有三个值，失焦时的行为完全不同：

- `IgnoreFocus`：失焦后设备继续工作；
- `ResetAndDisableNonBackgroundDevices`（默认）：失焦时重置并禁用"不支持后台运行"的设备（键盘/鼠标就是）；
- `ResetAndDisableAllDevices`：全部重置禁用。

且文档明确：这个选项**只在 Player Settings 勾了 Run In Background 时才有意义**。
https://docs.unity3d.com/Packages/com.unity.inputsystem@1.7/api/UnityEngine.InputSystem.InputSettings.BackgroundBehavior.html
https://docs.unity3d.com/Packages/com.unity.inputsystem@1.7/manual/KnownLimitations.html
https://docs.unity3d.com/ScriptReference/Application-runInBackground.html

意义：**如果游戏用新 Input System 且没勾 Run In Background，那么即使你把事件送进它的输入流，失焦状态下设备已被禁用，点击照样不认。**这决定了 in-process 路线（Q3）能不能靠"喂输入层"实现——那种情况下只能直接调 UGUI 事件（`ExecuteEvents`），不能靠 `Input`。

---

## 3. Q2 会话 / 桌面隔离

### 3.1 第二 RDP 会话（同机 loopback）— 唯一"便宜的真后台"候选，但坑多

- **Windows 家庭版不能作 RDP 主机**（无远程桌面宿主）→ 若本机是 Home，只有 RDPWrap 这条路。
- **RDPWrap**（`stascorp/rdpwrap`）：README 自述"enable Remote Desktop Host support and **concurrent RDP sessions** on reduced functionality systems for home usage"；徽章只标到 Windows 10（Vista~10）。
  https://github.com/stascorp/rdpwrap
  **未证实：Win11 可用性**（README 未列 Win11；社区 issue 里 Win11 更新后常失效）。原理是替换/挂钩 `termsrv.dll`，**每次 Windows 更新可能失效，且会被杀软拦**。
- **GPU 渲染**：RDP 会话默认走软件/远程渲染，3D 客户端的表现不可预期；RemoteFX vGPU 已被微软移除。**未证实**本机能拿到硬件加速（没在本次抓到一手文档，标"查不到"）。
- **WGC 能否捕获**：WGC 是桌面合成器侧 API，在 RDP 会话内工作，但帧率/编码行为未证实。
- **成本**：中高（改系统 DLL + 双会话授权问题 + 更新脆弱）。**不建议作为第一选择**。

### 3.2 `CreateDesktop` + `SwitchDesktop` — 不能"用户不受影响"，但能消灭 P2

文档（已证实）：

> `SwitchDesktop`: "Makes the specified desktop visible and **activates it. This enables the desktop to receive input from the user.**"
> https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-switchdesktop

> `CreateDesktopW`: 在当前窗口站上建桌面并**分配给调用线程**。
> https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-createdesktopw

判读：
- 输入只进**活动桌面**。所以"游戏在自己的桌面收输入、用户在自己桌面正常用机"**不可能同时成立**——`SwitchDesktop` 就是把屏幕切过去，用户看不见自己的窗口。
- 但它能给你两个**实打实**的东西：
  1. **前台竞争消失**：那个桌面上只有你的脚本和游戏，`SetForegroundWindow` 不会因为用户点了别处而失败 → **P2 直接归零，Alt 注入都可以不要**；
  2. **出帧稳定**：没有别的窗口遮挡 → obs#14 的"被遮挡就无帧"不再发生。
- 代价：脚本进程必须活在那个桌面上（`SetThreadDesktop` / 在该桌面创建进程），跨桌面无法投递输入；用户想看/接管必须切回来。
- **适用场景**：无人值守跑图（夜里/挂机）非常合适；"边用电脑边跑"仍然不行。状态：**推测**（由上述两条文档语义推出，未实测）。

### 3.3 VM / 双机（Hyper-V GPU-P、直通、串流）

- Hyper-V 需 Win10/11 **Pro/Enterprise**（Home 无 Hyper-V）；
- GPU-P（GPU 分区）需宿主 GPU + 驱动支持，社区工具 `jamesstringerparsec/Easy-GPU-PV`；
- 双机 + 串流（Moonlight/Sunshine、Parsec）延迟对 700~900 次 UI 点击**完全够用**（点击不敏感于 <50ms），成本是一台机器。
- **结论**：真后台且不折腾系统 DLL 的唯一"稳"路径，但成本最高。状态：**未证实**（未在有 GPU 的 Windows 上验证过本游戏）。

### 3.4 `IddSampleDriver` 虚拟显示器 — 解决的是另一个问题

- IDD（Indirect Display Driver）是软件显示器驱动，`roshkins/IddSampleDriver` 是微软示例的衍生，可给无物理显示器的机器造一块虚拟屏。
- 它**不改变**"窗口被遮挡/最小化时 WGC 无帧"（你们 obs#14 的实测），因为那不是显示器问题，是窗口可见性问题。
- 有用场景：无头机器 + `CreateDesktop` 方案下"总得有个显示器"的补丁。状态：**已证实机制**（IDD 是微软公开的驱动模型）。

---

## 4. Q3 绕过输入层：直接操作 Unity

### 4.1 BepInEx / MelonLoader 注入后直接触发 UGUI —— 唯一"真后台"

- **机制（已证实）**：注入托管 DLL 后，在游戏进程内拿到 `EventSystem.current`，构造 `PointerEventData`（`position` 填屏幕坐标），先 `RaycastAll` 拿目标，再
  `ExecuteEvents.Execute(target, data, ExecuteEvents.pointerClickHandler)`。
  https://docs.unity3d.com/ScriptReference/EventSystems.ExecuteEvents.Execute.html
  https://docs.unity3d.com/ScriptReference/EventSystems.PointerEventData.html
  这条路**完全不经过 OS 输入路由**，非前台、无光标、无焦点都能点。
- **前提判定（必须先做）**：游戏是 Mono 还是 IL2CPP。
  - Mono：`<Game>_Data/Managed/Assembly-CSharp.dll` 存在 → BepInEx 5 即可；
  - IL2CPP：存在 `GameAssembly.dll` → 需要 BepInEx 6（IL2CPP）或 MelonLoader（`Il2CppInterop`），成本和不确定性都上一个台阶。
- **风险（必须说清）**：
  - 每次游戏更新要重新适配（IL2CPP 尤其）；
  - **反作弊/封号**：这是自动化脚本，注入 DLL 会让"脚本"变成"外挂"性质。若游戏有服务端风控，风险显著高于"假装成真人鼠标"的现状；
  - `ExecuteEvents` 只覆盖"走 UGUI 事件系统的按钮"。你们 obs#17（"必须点在文字上"）说明有些命中区非常规，可能不是标准 `Image` raycast target，需要针对性处理（`GraphicRaycaster` 的 target 列表、`raycastTarget` 关闭的文本等）。
- **状态**：机制**已证实**；本游戏可行性**未证实**（不知道 Mono/IL2CPP、不知道是否有反作弊）。

### 4.2 官方通道：`-batchmode` / 远程调试 / PlayerPrefs / IL2CPP 反射

- `-batchmode`：Unity 的**无头运行**开关（主要用于服务器/CI），对已发行客户端不提供输入注入入口。**已证实语义 / 对本游戏无实用价值**。
- 远程调试：需要 Development Build 或调试符号服务器，发行版通常没有。**推测**。
- `PlayerPrefs`：只影响配置，与输入无关。**已证实无关**。
- IL2CPP 反射：**进程外不可能**（没有 CLR 可反射）；必须 in-process（`Il2CppInterop`）。**推测（架构确定）**。

### 4.3 中间层（`user32` shim / API hook）

- 想 hook `GetCursorPos` / `RegisterRawInputDevices` / `GetRawInputData`，必须在**游戏进程内**（Detours/MinHook 都是进程内 hook）。
- 进程外"hook 别人的 API 调用"在用户态没有合法手段（`AppInit_DLLs` 已废弃且需高权限签名；剩下的是内核 minifilter）。
- 所以"中间层"实际上等于"in-process 注入"，和 4.1 同一个门槛。**推测（架构确定）**。

---

## 5. Q4 工程折衷

### 5.1 降损清单（在"必须抢前台"前提下）

1. **批量合并**：把"一次 UI 操作需要 N 次点击"合并成一次前台占用（抢一次前台，点完 N 次再还），而不是每次点击抢一次。你们 `click()` 里每点都 `ensure_foreground()`，这是 P1 痛感的主要来源。
2. **前台占用窗口压到最短**：`ensure_foreground` 已成功的场景下，后续点击**不重复置前**（用 `GetForegroundWindow()==hwnd` 快速判定，且判定失败才走完整流程）。obs#10 的 4 轮重试只在首次使用。
3. **光标还原延迟到批次结束**：obs#8 说还原安全；但每点还原一次会额外产生光标跳动，用户观感差。可以只在"批次结束/用户接管"时还原一次。
4. **`AttachThreadInput` 常驻替代 Alt 注入**（降低 P6）：把前台窗口线程与脚本线程 attach 保持一次，之后 `SetForegroundWindow` 不再需要 Alt → 消除 Alt 卡住/凑热键的整类问题。**未证实**长期 attach 的副作用（输入队列共享，可能导致脚本也收到游戏的消息），需要实测。
5. **`ForegroundLockTimeout=0`**：辅助降低失败率（见 2.4），减少重试次数。
6. **点击时长**：obs#7 的 100ms 下限保留；连点兜底（obs#16 的 2~4 次）可以改成"检测到画面变化就停止连点"，减少无谓点击。
7. **P3 的判据**：连点前先比对 WGC 帧的局部像素/哈希（同一按钮区域在动画中会变），能区分"被吞"和"坐标错"，比盲目连点省时间。

### 5.2 ok-endfield 的实现审计（`ok-oldking/ok-end-field`，master）

代码：https://github.com/ok-oldking/ok-end-field/blob/master/src/interaction/Mouse.py

看过 `Mouse.py` 后的结论：**它不是"更优实现"，它就是同一套东西，而且更糙。**

- `click_down(hwnd,x,y)`：`win32gui.ClientToScreen(hwnd,(x,y))` → `user32.SetCursorPos(sx,sy)` → `mouse_event(LEFTDOWN)`；`click_up()` 只发 `LEFTUP`。
- `active_and_send_mouse_delta(...)`：`SW_RESTORE`/`SW_SHOW` → `SetForegroundWindow`，**`except` 里才补 Alt 注入**（一次，不重试，不校验结果）；然后用 `mouse_event(MOVE, dx/steps, dy/steps)` 分步相对移动。
- 也就是说：**抢前台 + 抢光标 + `mouse_event`，与你们完全同源**。网上把它叫"伪后台"是准确的。
- 它比你们**差的**：置前只试一次、无 `AttachThreadInput`、无失败重试、无结果校验。
- 它比你们**好的，只有一处**，而且很实用：**用 `ClientToScreen + SetCursorPos` 定位，而不是 `MOVE|ABSOLUTE|VIRTUALDESK` 归一化**。
  - 好处：直接给屏幕像素坐标，**彻底绕开 0~65535 归一化的 DPI/多屏换算** → 这正是你们的 **P5**；
  - 代价：定位和点击分成两次调用（`SetCursorPos` + `mouse_event(LEFTDOWN)`），中间有极短窗口期光标可能被抢——你们已经有 settle 40ms，可接受。
  - 与 obs#4 不冲突：`SetCursorPos` **单独**无效，但"`SetCursorPos` 定位 + `mouse_event` 触发"有效，因为触发事件时读的就是当前光标位置。

### 5.3 "半后台"到底存不存在

| 诉求 | 可行性 | 原因 |
|---|---|---|
| 不抢前台，但抢光标 | ❌ | 事件只进前台窗口；光标在别人窗口上时事件进的是别人 |
| 抢前台，但光标不动 | ❌ | 事件坐标 = 当前光标位置，不动光标就点不到目标；只能"瞬移→点→还原"（=现状） |
| 抢前台，光标闪一下就还 | ✅ | 现状。把"闪"压到 <100ms 且只在一批操作首尾各一次 |

---

## 6. Q5 可判定的实验（每个 ≤10 分钟）

**E1｜判定 Unity 读事件流还是轮询光标（最便宜，先做这个）**
游戏置于前台，用 `SetCursorPos` 把光标瞬移到一个**有 hover 效果**的按钮上（例如主菜单的某个图标），停 2 秒，观察该按钮是否出现高亮/放大。
判据：出现 hover → 引擎在轮询光标位置（则 obs#4 的解释另有原因，模型要改）；无 hover → 事件驱动（本报告模型成立）。
顺手对比：脚本点一下该按钮（`mouse_event`），hover 是否出现 → 确认"事件驱动 + 光标位置"模型。

**E2｜判定游戏是否用 raw input（静态证据，30 秒）**
在游戏目录对 `UnityPlayer.dll` 搜 API 名字符串（PowerShell）：
```powershell
$p = "D:\path\to\game\UnityPlayer.dll"
$s = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($p))
'RegisterRawInputDevices','GetRawInputData','GetCursorPos','ClipCursor','SetCursorPos' | % { "{0}: {1}" -f $_, $s.Contains($_) }
```
判据：`RegisterRawInputDevices`/`GetRawInputData` = True → 走 raw input（与 E1 结论应一致）；只有 `GetCursorPos` = True → 轮询模型成立。
同时看引擎类型：存在 `GameAssembly.dll` = IL2CPP；存在 `<Game>_Data\Managed\Assembly-CSharp.dll` = Mono。**这个结果直接决定 Q3 路线成本。**

**E3｜判定"消息没送到" vs "送到了但不认"（5 分钟）**
用 Spy++（VS 自带）或 `Window Detective` 挂到游戏窗口，Log Messages 过滤 `WM_LBUTTONDOWN`/`WM_KEYDOWN`，然后：
1. 脚本发 `PostMessage(WM_LBUTTONDOWN)`；
2. 脚本发 `PostMessage(WM_KEYDOWN, VK_ESCAPE)`。
判据：两条都能在 Spy++ 里看到 → 消息确实进了队列（obs#42 的"延迟生效"已经暗示这一点）→ 结论是"**UI 不认**"，`PostMessage` 路线**判死，不要再投入**。
若 `WM_LBUTTONDOWN` 在 Spy++ 里都看不到 → 是发送端问题（句柄错/权限），另查。

**E4｜判定 UIPI 是否是唯一障碍（10 分钟，只做一次）**
把脚本**以管理员运行**、游戏也以管理员运行，再跑 obs#1 的四组 `PostMessage`。
判据：仍然无反应 → UIPI 与前台锁都**不是**主因（主因是"不认消息"），UIAccess 方案直接放弃（省下证书钱）。
若这次有反应 → 说明 obs#1 是被 UIPI 拦的，那么"脚本与游戏同 IL"就是必须遵守的部署约定。

**E5｜`SendInput` 与 UIPI 的冲突点（10 分钟）**
在**游戏以管理员启动、脚本非管理员**的配置下，用脚本点一个必然会响应的按钮（例如主菜单"设置"），记录成功/失败；再以管理员启动脚本重复。
判据：非管理员也成功 → 文档所述 UIPI 拦截在此路径上不生效（obs#2 的观测成立，2.2 的冲突点以实测为准）；非管理员失败 → 文档与实测一致，obs#2 需要复核（可能那次游戏其实不是管理员）。

**工具清单（Q5 要求）**

| 工具 | 用途 | 成本 |
|---|---|---|
| **Spy++**（随 VS 安装）或 **Window Detective** | 看目标窗口实际收到哪些 `WM_*`（E3） | 免费，10 分钟 |
| **Process Explorer**（Sysinternals） | 看游戏进程完整性级别、是否加载了第三方 DLL、是否有反作弊 | 免费 |
| **PowerShell 字符串扫描** | 判定 raw input API（E2），无需反编译 | 免费，1 行 |
| **API Monitor（rohitab）** | 在游戏进程里挂 `GetCursorPos`/`RegisterRawInputDevices`，看调用频率与参数——**这是能直接证伪/证实"轮询 vs 事件"的唯一手段** | 免费，需管理员 |
| **ETW（`wpr`/WPA）** | 全局输入事件时序，排查"事件发了但被丢" | 重，EDR 场景才需要 |
| **自写 raw input 探针**（E6，20 行） | 证明注入事件进入了 raw input 流且 `INPUTSINK` 能后台收 | 半天 |

优先级：E2（字符串扫描）→ E1（hover 观感）→ E3（Spy++）→ E4/E5（IL 对照）。这四个做完，"有没有第三条路"不再是口水仗。

**E6（可选）｜raw input 可后台接收的反证**
写 20 行程序：注册隐藏窗口 + `RegisterRawInputDevices(RIDEV_INPUTSINK)`，然后游戏不在前台时发 `mouse_event(MOVE)`。
判据：探针收到 `WM_INPUT` → 证明(a) 注入事件确实进了 raw input 流，(b) raw input **可以**后台接收，只要注册了 `INPUTSINK`。这反证"游戏收不到后台输入是因为它没注册 `INPUTSINK`（Unity 默认不注册）"，从而彻底关闭"有没有第三条注入路"的讨论。

---

## 7. 建议下一步（按投入产出排，最多 3 条）

1. **【1 小时 / 零风险 / 立刻见效】改定位方式，消掉 P5**
   把 `_move_to()` 的 `MOVE|ABSOLUTE|VIRTUALDESK` 归一化改成 `ClientToScreen(hwnd, client_xy)` + `SetCursorPos`，点击仍用 `mouse_event(LEFTDOWN/UP)`（ok-endfield 的写法）。
   收益：直接消除 DPI/多屏归一化误差与 65535 量化误差（P5）；代码更短。保留归一化路径作为开关，A/B 对比命中率后再定。

2. **【半天 / 决策级】跑 E1–E4，把"唯一可行解"钉死**
   产出：引擎类型（Mono/IL2CPP）、输入路径（raw input / 轮询）、`PostMessage` 是否判死、UIPI 是否为主因。
   目的：这四条决定后面要不要投 in-process（Q3）——如果游戏是 IL2CPP + 有周期性更新，in-process 的维护成本可能超过收益，那就直接转 Q2 的会话隔离。

3. **【1–2 天 / 唯一真后台】先验证 in-process 的"能进得去"，不写点击**
   只做探测：能否注入、能否拿到 `EventSystem.current`、能否枚举一个按钮的 `RectTransform` 并打印路径。
   通过 → 再做 `RaycastAll + ExecuteEvents.Execute(pointerClickHandler)`；不通过（IL2CPP + 混淆/反作弊）→ 放弃 Q3，转 Q2（第二会话或双机）。
   **注意**：这是唯一会改变"脚本性质"的选项（从"模拟鼠标"变成"进程内注入"），若游戏有风控需自行评估封号风险，本报告不替你承担这个判断。

**不建议做的**（省时间）：UIAccess 清单（对 P1 零收益）、`PostMessage` 任何变体（E3 后判死）、Interception/ViGEmBus（前者不解决前台，后者是手柄）、`CreateDesktop` 当"用户不受影响"的方案（`SwitchDesktop` 语义决定它必然切屏）。

---

## 8. 查不到 / 不确定（不含糊，列清楚）

1. **Unity 内部输入栈的官方说明**：Unity 从不说 Windows 上鼠标走 raw input 还是窗口消息。1.3 的模型是**推测**，靠 E1/E2 判定。
2. **UIAccess 是否可无视前台锁**：本次抓到的 `SetForegroundWindow` / `LockSetForegroundWindow` / UIPI / manifests 四份文档都没有这句话 → 标未证实。
3. **UIAccess 是否必须 EV 证书**：没有一手来源；只说"必须签名 + 受信任路径"。
4. **Win11 上 UIAccess 的变化**：没查到。
5. **RDPWrap 在 Win11 的可用性**：README 只标到 Win10，未见 Win11 声明 → 未证实。
6. **RDP 会话内的 GPU 渲染 / WGC 帧率**：没拿到一手依据（RemoteFX vGPU 已被微软移除这点我记得，但本次没抓到文档，标未证实）。
7. **本游戏是 Mono 还是 IL2CPP、有无反作弊**：未知，E2 直接测。
8. **`AttachThreadInput` 长挂的副作用**：无官方说明，属未证实。
9. **`AllowSetForegroundWindow` 在"脚本自身无置前权"时的实际效果**：文档只说"前台进程可以授权别家"，反向场景无说明 → 未证实。
10. **obs#2 的 UIPI 冲突（`SendInput` 文档 vs 实测）**：保留为冲突点，E5 判定。

---

## 9. 参考链接

**Microsoft Learn**
- PostMessageW https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-postmessagew
- ChangeWindowMessageFilterEx https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changewindowmessagefilterex
- SendInput https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
- SetCursorPos https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setcursorpos
- SetForegroundWindow https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow
- LockSetForegroundWindow https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-locksetforegroundwindow
- AllowSetForegroundWindow https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-allowsetforegroundwindow
- AttachThreadInput https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-attachthreadinput
- About Raw Input https://learn.microsoft.com/en-us/windows/win32/inputdev/about-raw-input
- WM_INPUT https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-input
- User Interface Privilege Isolation https://learn.microsoft.com/en-us/windows/win32/secauthz/user-interface-privilege-isolation
- Application manifests (uiAccess) https://learn.microsoft.com/en-us/windows/win32/sbscs/application-manifests
- UIAccess security overview https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-securityoverview
- CreateDesktopW https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-createdesktopw
- SwitchDesktop https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-switchdesktop
- Window Stations https://learn.microsoft.com/en-us/windows/win32/winmsg/window-stations

**Unity**
- InputSettings.BackgroundBehavior https://docs.unity3d.com/Packages/com.unity.inputsystem@1.7/api/UnityEngine.InputSystem.InputSettings.BackgroundBehavior.html
- Input System Known Limitations https://docs.unity3d.com/Packages/com.unity.inputsystem@1.7/manual/KnownLimitations.html
- Application.runInBackground https://docs.unity3d.com/ScriptReference/Application-runInBackground.html
- ExecuteEvents.Execute https://docs.unity3d.com/ScriptReference/EventSystems.ExecuteEvents.Execute.html
- PointerEventData https://docs.unity3d.com/ScriptReference/EventSystems.PointerEventData.html

**参考实现**
- BetterGI PostMessageSimulator.cs https://github.com/babalae/better-genshin-impact/blob/main/BetterGenshinImpact/Core/Simulator/PostMessageSimulator.cs
- ok-script https://github.com/ok-oldking/ok-script
- ok-end-field Mouse.py https://github.com/ok-oldking/ok-end-field/blob/master/src/interaction/Mouse.py
- Interception https://github.com/oblitum/Interception
- ViGEmBus（已退役，手柄）https://github.com/nefarius/ViGEmBus
- RDPWrap https://github.com/stascorp/rdpwrap
- Easy-GPU-PV https://github.com/jamesstringerparsec/Easy-GPU-PV
- IddSampleDriver https://github.com/roshkins/IddSampleDriver
- BepInEx https://github.com/BepInEx/BepInEx
- MelonLoader https://github.com/LavaGang/MelonLoader
