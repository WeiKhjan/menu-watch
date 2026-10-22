# Builds menuwatch-deploy.tar.gz (code + model + demo footage, no .venv or database) for uploading to the server.
#   powershell -ExecutionPolicy Bypass -File deploy\make_package.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $env:TEMP "menuwatch-stage"
$out = Join-Path $root "menuwatch-deploy.tar.gz"

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
$dst = New-Item -ItemType Directory -Force (Join-Path $stage "menuwatch")
foreach ($item in "menu_watch", "deploy", "footage", "run.py", "wsgi.py", "seed_demo.py", "config.json", "yolo11n.pt", "README.md", "DEPLOY.md", "requirements.txt") {
    Copy-Item -Recurse (Join-Path $root $item) $dst
}
Get-ChildItem $dst -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# Shell scripts must have Linux line endings
Get-ChildItem $dst -Recurse -Filter "*.sh" | ForEach-Object {
    $text = [IO.File]::ReadAllText($_.FullName) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($_.FullName, $text, (New-Object Text.UTF8Encoding $false))
}

if (Test-Path $out) { Remove-Item $out }
# Windows' own tar explicitly: Git-for-Windows' tar (often first on PATH) misreads "C:\..." as a remote host
& "$env:SystemRoot\System32\tar.exe" -czf $out -C $stage menuwatch
if ($LASTEXITCODE -ne 0) { throw "tar failed" }
Remove-Item -Recurse -Force $stage
"Created $out ({0:N1} MB)" -f ((Get-Item $out).Length / 1MB)
