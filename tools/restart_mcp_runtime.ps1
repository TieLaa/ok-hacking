# 重启 MCP runtime（管理员）：先杀掉占着 8765 的旧进程，再拉新的。
# 为什么单独放脚本：runtime 是管理员进程，普通权限 Stop-Process 会「拒绝访问」；
# 用 Start-Process -Verb RunAs 跑这个脚本，一次 UAC 完成「杀掉 + 重启」。
# 用法：
#   Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','D:\workspace\ok-hacking\tools\restart_mcp_runtime.ps1'
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
Set-Location 'D:\workspace\ok-hacking'
$env:PYTHONIOENCODING = 'utf-8'
& .\.venv\Scripts\python.exe -m ok mcp --config src.config:config *> logs\mcp\runtime.log
