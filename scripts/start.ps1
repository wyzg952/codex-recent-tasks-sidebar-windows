[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$main = Join-Path $root 'src\main.py'
$python = (Get-Command pythonw.exe -ErrorAction Stop).Source

Start-Process -FilePath $python -ArgumentList @("`"$main`"") -WorkingDirectory $root
