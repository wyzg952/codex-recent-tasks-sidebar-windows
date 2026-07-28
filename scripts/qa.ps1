[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Get-SharedHash {
    param([Parameter(Mandatory)][string]$LiteralPath)
    $stream = [System.IO.File]::Open($LiteralPath, 'Open', 'Read', 'ReadWrite')
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            return [Convert]::ToHexString($sha.ComputeHash($stream))
        }
        finally {
            $sha.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

Push-Location $root
try {
    $env:PYTHONPATH = Join-Path $root 'src'
    python -m compileall -q src tests
    if ($LASTEXITCODE -ne 0) { throw 'Python 编译检查失败' }

    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw '自动测试失败' }

    $dataFiles = @(
        Join-Path $HOME '.codex\state_5.sqlite'
        Join-Path $HOME '.codex\session_index.jsonl'
        Join-Path $HOME '.codex\.codex-global-state.json'
    ) | Where-Object { Test-Path -LiteralPath $_ }
    $before = @{}
    foreach ($file in $dataFiles) { $before[$file] = Get-SharedHash -LiteralPath $file }

    @'
from codex_sidebar.data import TaskRepository
from codex_sidebar.usage import fetch_usage
tasks = TaskRepository().load()
usage = fetch_usage(timeout=15)
assert isinstance(tasks, list)
assert len(usage) >= 1
print(f"REAL_SELF_TEST_OK tasks={len(tasks)} usage_windows={len(usage)}")
'@ | python -
    if ($LASTEXITCODE -ne 0) { throw '真实环境只读自检失败' }

    foreach ($file in $dataFiles) {
        $after = Get-SharedHash -LiteralPath $file
        if ($before[$file] -ne $after) { throw "只读校验失败：$file 被修改" }
    }

    $secretPattern = 'gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|BEGIN (RSA |OPENSSH )?PRIVATE KEY'
    $matches = rg -n --hidden --glob '!build/**' --glob '!__pycache__/**' --glob '!*.pyc' $secretPattern .
    if ($LASTEXITCODE -eq 0 -and $matches) { throw "脱敏扫描失败：`n$matches" }
    if ($LASTEXITCODE -gt 1) { throw '脱敏扫描命令失败' }

    Write-Output 'QA_OK Windows Codex 最近任务栏'
}
finally {
    Pop-Location
}
