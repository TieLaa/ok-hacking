# ok-hacking

《篮球少女：飞跃巅峰》Windows 客户端的养成自动化，基于 [ok-script](https://github.com/ok-oldking/ok-script) 构建。

[English](en/index.md) · [GitHub 仓库](https://github.com/TieLaa/ok-hacking) · [README](https://github.com/TieLaa/ok-hacking#readme)

!!! warning "免责声明"
    本项目与游戏官方无关，仅供学习与个人研究。使用自动化工具可能违反游戏用户服务条款，可能导致账号被处罚，风险与后果由使用者自负。

## 这是什么

一个跑养成循环的自动化程序：识别游戏界面、按配置做决策、把一局养成从头跑到赛季结束。核心是「读界面 → 判断 → 操作 → 验证」的循环，所有判据都是本地图像/OCR 规则，不在运行时调用任何大模型。

## 快速上手

1. 到 [Releases](https://github.com/TieLaa/ok-hacking/releases) 下载安装包，或以管理员身份从源码启动；
2. 确认「截图方式」页能抓到游戏画面；
3. 「任务」页展开「养成-训练循环」改配置，点开始；
4. 「监控」页看进度；卡住时先读 `logs/debug/<时间>_<tag>/ocr.txt`。

安装与运行细节见 [README](https://github.com/TieLaa/ok-hacking#readme)。

## 工程文档

| 文档 | 内容 |
| --- | --- |
| [架构](ARCHITECTURE.md) | 分层：flow / parsers / scoring / handlers / places |
| [工程约定](ENGINEERING.md) | 业务代码边界、门禁、命名与提交规范 |
| [监控页](UI_MONITOR.md) | 布局 JSON、QSS、热重载与数据来源 |
| [界面地图](UI_MAP.md) | 游戏各界面与对应 handler 的对照 |
| [MCP 开发循环](MCP_DEV.md) | 改 handler 不重启 GUI 的验证流程 |
| [提示词手册](PROMPT_PLAYBOOK.md) | 踩过的坑与下次该怎么说 |
| [规则](RULES.md) | 项目内的硬规则 |
| [打包与发布](release.md) | 构建产物、tag 流程、依赖锁定 |

## 许可

代码 [MIT](https://github.com/TieLaa/ok-hacking/blob/main/LICENSE)；用到的第三方组件（ok-script、PyAppify、PyQt-Fluent-Widgets）各自遵循其许可，详见 [README](https://github.com/TieLaa/ok-hacking#readme)。
