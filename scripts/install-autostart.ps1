[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$watcher = Join-Path $root 'src\watcher.py'
$icon = Join-Path $root 'assets\AppIcon.ico'
$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'Codex 最近任务自动启动.lnk'

if (-not (Test-Path -LiteralPath $watcher -PathType Leaf)) {
    throw "找不到监视器：$watcher"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$watcher`""
$shortcut.WorkingDirectory = $root
$shortcut.Description = '随 Codex 自动打开和关闭最近任务侧栏'
if (Test-Path -LiteralPath $icon -PathType Leaf) {
    $shortcut.IconLocation = "$icon,0"
}
$shortcut.Save()

$watcherKey = $watcher.ToLowerInvariant()
$running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($watcherKey) }

if (-not $running) {
    Start-Process -FilePath $pythonw -ArgumentList @("`"$watcher`"") -WorkingDirectory $root -WindowStyle Hidden
}

Write-Output "AUTOSTART_INSTALLED shortcut=$shortcutPath"
