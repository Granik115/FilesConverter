$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$versionMatch = Select-String `
    -Path "src\filesconverter\__init__.py" `
    -Pattern '__version__\s*=\s*"([^"]+)"'
$version = $versionMatch.Matches.Groups[1].Value
if (-not $version) {
    throw "Version not found"
}

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts/generate_icon.py
python -m pytest -q
python -m PyInstaller --clean --noconfirm packaging/FilesConverter.spec

$isccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 not found"
}

New-Item -ItemType Directory -Force releases | Out-Null
& $iscc "/DMyAppVersion=$version" "installer\FilesConverter.iss"

Write-Host "Built releases\FilesConverter-$version-setup.exe" -ForegroundColor Green
