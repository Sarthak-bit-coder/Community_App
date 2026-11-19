# Stops the background app and the ngrok link started by start_all.ps1.
#   powershell -ExecutionPolicy Bypass -File .\stop_all.ps1

Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force
$app = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $app) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue }
Write-Host "Stopped the app and ngrok."
