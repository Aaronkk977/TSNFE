param(
    [int]$Port = 9222,
    [string]$ProfileDir = "$env:USERPROFILE\ChromeDevProfile",
    [switch]$OpenGemini,
    [switch]$BindAll,
    [switch]$KillExisting
)

$ErrorActionPreference = "Stop"

function Get-ChromePath {
    $candidates = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$chromePath = Get-ChromePath
if (-not $chromePath) {
    Write-Error "Chrome not found. Please install Google Chrome or edit this script path."
}

if ($KillExisting) {
    Write-Host "Killing existing Chrome processes..."
    Get-Process chrome -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
}

if (-not (Test-Path $ProfileDir)) {
    New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
}

Write-Host "Starting Chrome with CDP on port $Port ..."
Write-Host "Profile: $ProfileDir"

$args = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$ProfileDir",
    "--no-first-run",
    "--no-default-browser-check"
)

if ($BindAll) {
    $args += "--remote-debugging-address=0.0.0.0"
    Write-Host "CDP bind address: 0.0.0.0 (for WSL/external access)"
}

if ($OpenGemini) {
    $args += "https://gemini.google.com/app"
}

Start-Process -FilePath $chromePath -ArgumentList $args | Out-Null

Start-Sleep -Seconds 2

try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 3
    Write-Host "CDP is ready: $($resp.webSocketDebuggerUrl)"
    Write-Host "Now run: python scripts/process_with_gemini_web.py <youtube_url> --cdp-url http://127.0.0.1:$Port"
}
catch {
    Write-Warning "Chrome started, but CDP endpoint check failed. Wait a few seconds and retry."
}
