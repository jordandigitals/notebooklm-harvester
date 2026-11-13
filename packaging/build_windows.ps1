# Build the self-contained Windows app (R11, D-018): bundles Python, a static ffmpeg and
# Playwright (for the fresh Google sign-in, see the M5 Windows report).
# Run from the repo root in Windows PowerShell: powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# Output: dist\NotebookLM-Harvester-win-x64-<version>.zip
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # the PowerShell 5.1 progress bar makes downloads crawl
Set-Location (Split-Path -Parent $PSScriptRoot)

$Version = [regex]::Match((Get-Content nlh\__init__.py -Raw), '__version__ = "(.+?)"').Groups[1].Value
$Name = "NotebookLM Harvester"
$BuildVenv = if ($env:BUILD_VENV) { $env:BUILD_VENV } else { Join-Path $env:USERPROFILE ".nlh-build-venv" }
$Cache = "build\ffmpeg-cache"

# Static ffmpeg 9.0.2 "essentials" for Windows x64 by Gyan Doshi, pinned by SHA-256
# (the digest GitHub publishes for this release asset).
$FfUrl = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
$FfSha = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"

New-Item -ItemType Directory -Force $Cache | Out-Null
$Zip = Join-Path $Cache "ffmpeg-9.0.2-essentials_build.zip"
if (-not (Test-Path $Zip)) {
    Write-Host "Downloading ffmpeg 9.0.2..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $FfUrl -OutFile "$Zip.download" -UseBasicParsing
    Move-Item "$Zip.download" $Zip  # only a finished download gets the real name
}
$Got = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower()
if ($Got -ne $FfSha) { throw "ffmpeg download checksum mismatch: $Got" }
$FfDir = Join-Path $Cache "ffmpeg-9.0.2-essentials_build"
if (-not (Test-Path "$FfDir\bin\ffmpeg.exe")) { Expand-Archive -Path $Zip -DestinationPath $Cache -Force }

if (-not (Test-Path "$BuildVenv\Scripts\pyinstaller.exe")) {
    py -3.14 -m venv $BuildVenv
    & "$BuildVenv\Scripts\python.exe" -m pip install -q --upgrade pip
    & "$BuildVenv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller==6.22.3
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

foreach ($p in @("build\pyinstaller", "dist\$Name")) { if (Test-Path $p) { Remove-Item -Recurse -Force $p } }
Get-ChildItem dist -Filter "NotebookLM-Harvester-win-*" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force

$Root = (Get-Location).Path
& "$BuildVenv\Scripts\pyinstaller.exe" --noconfirm --clean --windowed `
    --name $Name `
    --workpath build\pyinstaller --distpath dist --specpath build\pyinstaller `
    --add-data "$Root\nlh\static;nlh\static" `
    --add-binary "$Root\$FfDir\bin\ffmpeg.exe;." `
    --add-binary "$Root\$FfDir\bin\ffprobe.exe;." `
    --collect-all notebooklm `
    --collect-all rookie_cookies `
    --collect-all playwright `
    nlh\app_entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Copy-Item packaging\THIRD-PARTY-NOTICES.txt "dist\$Name\"
Copy-Item "$FfDir\LICENSE" "dist\$Name\FFMPEG-LICENSE.txt"
$Stage = "dist\NotebookLM-Harvester-win-x64-$Version"
New-Item -ItemType Directory -Force $Stage | Out-Null
Copy-Item -Recurse "dist\$Name" "$Stage\"
Copy-Item packaging\THIRD-PARTY-NOTICES.txt, packaging\README-FIRST-win.txt "$Stage\"
Compress-Archive -Path $Stage -DestinationPath "$Stage.zip" -Force
Write-Host "Built $Stage.zip"
"{0:N0} MB app folder" -f ((Get-ChildItem -Recurse "dist\$Name" | Measure-Object Length -Sum).Sum / 1MB)
"{0:N0} MB zip" -f ((Get-Item "$Stage.zip").Length / 1MB)
(Get-FileHash "$Stage.zip" -Algorithm SHA256).Hash.ToLower()
