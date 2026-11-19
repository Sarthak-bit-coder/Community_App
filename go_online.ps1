# Puts the app online through ngrok, protected by a username and password.
# Usage (with the app already running on port 5000):   powershell -ExecutionPolicy Bypass -File .\go_online.ps1
# The username/password come from SITE_USER / SITE_PASSWORD in .env.
# The WhatsApp webhook (/webhooks/...) stays open so Twilio can reach it; the app verifies
# Twilio's signature on it when TWILIO_AUTH_TOKEN is set.

$ErrorActionPreference = "Stop"
$envFile = Join-Path $PSScriptRoot ".env"
$vars = @{}
Get-Content $envFile | Where-Object { $_ -match '^\s*([A-Z_]+)=(.*)$' } | ForEach-Object { $vars[$Matches[1]] = $Matches[2].Trim() }

$user = $vars["SITE_USER"]; $pass = $vars["SITE_PASSWORD"]
if (-not $user -or -not $pass -or $pass.Length -lt 8) { throw "Set SITE_USER and SITE_PASSWORD (8+ characters) in .env first." }

# Write the traffic policy outside the project folder so the password never ends up in git.
$policyDir = Join-Path $env:LOCALAPPDATA "ngrok"
New-Item -ItemType Directory -Force -Path $policyDir | Out-Null
$policy = Join-Path $policyDir "neighborhood-watch-policy.yml"
@"
on_http_request:
  - expressions:
      - "!req.url.path.startsWith('/webhooks/')"
    actions:
      - type: basic-auth
        config:
          realm: "Neighborhood Watch"
          credentials:
            - "${user}:${pass}"
          enforce: true
"@ | Set-Content -Path $policy -Encoding utf8

$ngrok = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
if (-not $ngrok) { $ngrok = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe" }

Write-Host "Starting ngrok on port 5000 with password protection (user: $user). Press Ctrl+C to go offline."
& $ngrok http 5000 --traffic-policy-file $policy
