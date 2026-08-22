# Serve vidsmith on a public URL through a Cloudflare quick tunnel.
#
# Free, no Cloudflare account, no domain. The render happens on this machine, so
# it runs at full local speed rather than a hosted instance's fraction of a CPU.
# The URL lives only as long as this window does.
#
#   cd ~/claude/vidsmith; .\scripts\serve-public.ps1
#
# Ctrl+C stops the tunnel; the server is stopped on the way out.

param(
    [int]$Port = 8077
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "no venv at $python - see the README install step" }

$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path", "User")
$cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cloudflared) { throw "cloudflared not found - winget install Cloudflare.cloudflared" }

# A public URL with no token is an open renderer spending your Pexels and Gemini
# quota, so one is minted into .env the first time this runs.
$envFile = Join-Path $root ".env"
$envText = if (Test-Path $envFile) { Get-Content $envFile -Raw } else { "" }
if ($envText -notmatch "VIDSMITH_TOKEN=") {
    $token = & $python -c "import secrets; print(secrets.token_urlsafe(18))"
    Add-Content -Path $envFile -Value "VIDSMITH_TOKEN=$token" -Encoding utf8
    Write-Host "minted a new access token into .env"
}
$token = ((Get-Content $envFile) | Where-Object { $_ -match "^VIDSMITH_TOKEN=" }) -replace "^VIDSMITH_TOKEN=", ""

Write-Host "starting vidsmith on port $Port"
$server = Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "web.app:app", "--host", "127.0.0.1", "--port", "$Port" `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden

try {
    $ready = $false
    foreach ($i in 1..40) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-RestMethod "http://127.0.0.1:$Port/healthz" -TimeoutSec 3 | Out-Null
            $ready = $true
            break
        } catch { }
    }
    if (-not $ready) { throw "the server did not come up on port $Port" }

    Write-Host ""
    Write-Host "access token: $token" -ForegroundColor Yellow
    Write-Host "the page asks for it once and remembers it in that browser."
    Write-Host ""
    Write-Host "opening a tunnel - the https://...trycloudflare.com line below is your URL"
    Write-Host ""
    & $cloudflared tunnel --url "http://127.0.0.1:$Port" --no-autoupdate
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        Write-Host "server stopped"
    }
}
