# Serve vidsmith on a public URL through a Cloudflare quick tunnel.
#
# Free, no Cloudflare account, no domain. The render happens on this machine, so
# it runs at full local speed rather than a hosted instance's fraction of a CPU.
# The URL lives only as long as this window does.
#
#   cd ~/claude/vidsmith; .\scripts\serve-public.ps1
#
# Ctrl+C stops the tunnel; the server is stopped on the way out.
#
# -NoToken opens the tunnel with no access gate at all. Anyone with the URL can
# then render, and every render spends this machine's Pexels, Pixabay and Gemini
# keys. Reasonable for showing one person for ten minutes, since the address is
# four random words and dies with the window; not for anywhere public.

param(
    [int]$Port = 8077,
    [switch]$NoToken
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
# Both of these must be anchored and must require a value. An unanchored test
# here once matched a commented-out `# VIDSMITH_TOKEN=...` line, so no token was
# minted, none was read, and the tunnel opened with the gate wide open.
$live = if (Test-Path $envFile) {
    @(Get-Content $envFile | Where-Object { $_ -match "^\s*VIDSMITH_TOKEN=\S" })
} else { @() }

if ($NoToken) {
    # asked for deliberately, so the server must not pick a token up from .env
    # either: it reads that file itself, and a commented line is the only way to
    # leave the value recoverable without the gate coming back on.
    if ($live.Count -gt 0) {
        (Get-Content $envFile) |
            ForEach-Object { if ($_ -match "^\s*VIDSMITH_TOKEN=\S") { "# $_" } else { $_ } } |
            Set-Content $envFile -Encoding utf8
        Write-Host "commented out the token in .env for this run"
    }
    $token = ""
    Write-Host "no access gate: anyone with the URL can render on your keys" -ForegroundColor Red
}
else {
    if ($live.Count -eq 0) {
        # A native command's output can arrive with a trailing carriage return.
        # Left in, it travels into the .env line and into every later print of
        # the token, where the CR returns the cursor to column zero and the
        # displayed value collapses to its last character. Strip to printable
        # ASCII at the source, which is all token_urlsafe ever produces.
        $fresh = (& $python -c "import secrets; print(secrets.token_urlsafe(18))") `
                 -replace "[^\x21-\x7E]", ""
        Add-Content -Path $envFile -Value "VIDSMITH_TOKEN=$fresh" -Encoding utf8
        Write-Host "minted a new access token into .env"
        $live = @("VIDSMITH_TOKEN=$fresh")
    }
    $token = ($live[-1] -replace "^\s*VIDSMITH_TOKEN=", "") -replace "[^\x21-\x7E]", ""

    # Never expose an ungated renderer by accident. -NoToken is the way to mean it.
    if (-not $token) {
        throw "refusing to open a tunnel with no access token; pass -NoToken to mean it"
    }
}

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
    if ($token) {
        # Delimited and counted: if this line is ever truncated again, it is
        # visible here rather than being discovered by someone locked out of
        # their own tunnel.
        Write-Host "access token: [$token] ($($token.Length) chars)" -ForegroundColor Yellow
        Write-Host "the page asks for it once and remembers it in that browser."
    }
    else {
        Write-Host "no access token: the page opens straight into the form." -ForegroundColor Red
    }
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
