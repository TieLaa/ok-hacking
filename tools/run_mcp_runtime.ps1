# 以管理员权限启动 MCP runtime（游戏进程是管理员权限，非管理员发不进点击）。
# 用法：Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','D:\workspace\ok-hacking\tools\run_mcp_runtime.ps1'
Set-Location 'D:\workspace\ok-hacking'
$env:PYTHONIOENCODING = 'utf-8'
& .\.venv\Scripts\python.exe -m ok mcp --config src.config:config *> logs\mcp\runtime.log
