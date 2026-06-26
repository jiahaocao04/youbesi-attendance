$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Candidates = @($BundledPython, "python")

foreach ($Candidate in $Candidates) {
    try {
        & $Candidate --version *> $null
        if ($LASTEXITCODE -eq 0) {
            & $Candidate "app.py"
            exit $LASTEXITCODE
        }
    } catch {
    }
}

Write-Host "没有找到可用的 Python。"
Write-Host "如果不是在 Codex 桌面里运行，请先安装 Python 3.11 或更高版本。"
Read-Host "按回车退出"
exit 1
