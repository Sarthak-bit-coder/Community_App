# Starts the app (port 5000) and the password-protected ngrok link, both hidden in the background.
# Runs at Windows sign-in from the Startup folder (see README). Safe to run by hand too:
#   powershell -ExecutionPolicy Bypass -File .\start_all.ps1
# Anything already running is left alone. App output goes to logs\app.log.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$logs = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Test-AppUp {
    try { (New-Object Net.Sockets.TcpClient("127.0.0.1", 5000)).Close(); return $true } catch { return $false }
}

if (-not (Test-AppUp)) {
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUNBUFFERED = "1"
    Start-Process -FilePath (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") -ArgumentList "app.py" `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "app.log") -RedirectStandardError (Join-Path $logs "app-errors.log")
    for ($i = 0; $i -lt 60 -and -not (Test-AppUp); $i++) { Start-Sleep -Seconds 1 }
}

if (-not (Get-Process ngrok -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -WorkingDirectory $PSScriptRoot `
        -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "go_online.ps1")
}
