[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$watcher = Join-Path $root 'src\watcher.py'
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'Codex 最近任务自动启动.lnk'
$watcherKey = $watcher.ToLowerInvariant()

$running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($watcherKey) }
foreach ($process in $running) {
    Stop-Process -Id $process.ProcessId
}

if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
    Remove-Item -LiteralPath $shortcutPath
}

Write-Output 'AUTOSTART_UNINSTALLED'
