# ok-hacking

English | [中文](README.md)

Automation for the Windows client of **Basketball Girls: Leap to the Top** (篮球少女：飞跃巅峰), built on [ok-script](https://github.com/ok-oldking/ok-script).

## Status

Work in progress. The ok-script scaffold runs, the game window is detected (`k3_windows_ob.exe` / `UnityWndClass` / 1920x1080), and capture works.

## Hard constraints (measured, not assumed)

1. **Run as administrator.** The game process is elevated; a non-elevated process cannot post input to it (`PostMessage` returns access denied).
2. **ASCII-only install path.** ok-script refuses non-ASCII paths.
3. **The game window must stay visible.** When the window is fully occluded or minimized, WGC stops delivering frames, so the bot has nothing to work with.

## Run

```powershell
cd D:\workspace\ok-hacking
Start-Process -FilePath "D:\workspace\ok-hacking\.venv\Scripts\python.exe" `
  -ArgumentList "main_debug.py" -WorkingDirectory "D:\workspace\ok-hacking" -Verb RunAs
```

## Layout

| Path | Purpose |
| --- | --- |
| `src/config.py` | Window, capture/interaction backend, task registration |
| `src/tasks/AutoBattleTask.py` | Background combat task (TriggerTask) |
| `src/tasks/InputTestTask.py` | Input self-test (click + frame diff) |
| `tools/wgc_capture.py` | Standalone frame grabber (works while occluded) |

## License

ok-script is AGPL-3.0. Automating an online game may violate its terms of service.
