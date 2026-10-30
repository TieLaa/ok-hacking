# 重启 MCP runtime（管理员）。
# 除了杀掉占 8765 的旧进程，还要处理 ok 的**单实例互斥量**：
# 旧进程假死后 mutex 不释放，新实例会报
# "Another instance of this application is already running ... could not be stopped safely"。
# 所以顺带清掉最近 15 分钟内的 python 进程（只包含刚才失败的启动残渣，不会碰长时间运行的 GUI）。
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object {
        $_.StartTime -gt (Get-Date).AddMinutes(-15) -and
        (-not $_.Path -or $_.Path -like '*ok-hacking*')
    } |
    ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3
Set-Location 'D:\workspace\ok-hacking'
$env:PYTHONIOENCODING = 'utf-8'
& .\.venv\Scripts\python.exe -m ok mcp --config src.config:config *> logs\mcp\runtime.log
