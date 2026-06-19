<#
.SYNOPSIS
    One-command Facebook session capture for Windows.

.DESCRIPTION
    The Docker target `make capture-fb` opens a headful Chromium INSIDE the Linux
    worker-browser container, which has no display on Windows. This wrapper instead runs
    the login on the Windows host (where there is a display):

      1. Creates an isolated venv at .\.venv-capture (only playwright + cryptography).
      2. Installs the Chromium browser for Playwright (first run only; shared user cache).
      3. Launches backend\scripts\capture_fb_windows.py, which opens a real Chrome window,
         waits for you to log in, then writes the ENCRYPTED session to
         backend\.sessions\facebook.session.

    That file is bind-mounted into the container at /app/.sessions, so the browser worker
    picks it up automatically -- no rebuild, no container changes.

.PARAMETER PythonVersion
    Python launcher version to use (default 3.13). 3.14 is avoided because playwright /
    cryptography wheels are often not yet published for it.

.PARAMETER TimeoutSeconds
    How long to wait for login before prompting (default 300).

.EXAMPLE
    .\capture-fb.ps1
    .\capture-fb.ps1 -TimeoutSeconds 600
#>
[CmdletBinding()]
param(
    [string]$PythonVersion = "3.13",
    [int]$TimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
$venvDir  = Join-Path $repoRoot ".venv-capture"
$script   = Join-Path $repoRoot "backend\scripts\capture_fb_windows.py"
$envFile  = Join-Path $repoRoot ".env"

function Fail($msg) { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $script))  { Fail "Capture script not found at $script" }
if (-not (Test-Path $envFile)) { Fail "No .env at $envFile (needed for APP_ENCRYPTION_KEY)." }

# 1. Resolve a usable Python (avoid 3.14 wheel gaps).
$pyExe = $null
try {
    $probe = & py "-$PythonVersion" -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $probe) { $pyExe = $probe.Trim() }
} catch { }
if (-not $pyExe) {
    Fail "Python $PythonVersion not found via the 'py' launcher. Installed: `n$(& py -0p 2>&1 | Out-String)Pick one with -PythonVersion, e.g. .\capture-fb.ps1 -PythonVersion 3.10"
}
Write-Host "Using Python $PythonVersion -> $pyExe" -ForegroundColor Cyan

# 2. Create the isolated venv once.
$venvPy = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating capture venv at $venvDir ..." -ForegroundColor Cyan
    & $pyExe -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed." }
} else {
    Write-Host "Reusing existing capture venv at $venvDir" -ForegroundColor Cyan
}

# 3. Install the two deps (idempotent; fast when already satisfied).
Write-Host "Ensuring playwright + cryptography are installed ..." -ForegroundColor Cyan
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet playwright cryptography
if ($LASTEXITCODE -ne 0) { Fail "pip install of playwright/cryptography failed." }

# 4. Install the Chromium browser binary (no-op if already in the shared user cache).
Write-Host "Ensuring Playwright Chromium is installed ..." -ForegroundColor Cyan
& $venvPy -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Fail "playwright install chromium failed." }

# 5. Run the capture. A Chrome window opens -- log in there.
Write-Host "`nLaunching login window. Log in as your DEDICATED scraping account.`n" -ForegroundColor Green
& $venvPy $script --env-file $envFile --timeout $TimeoutSeconds
$code = $LASTEXITCODE

if ($code -eq 0) {
    Write-Host "`nDone. Session saved to backend\.sessions\facebook.session" -ForegroundColor Green
    Write-Host "The worker-browser container reads it automatically via the bind mount." -ForegroundColor Green
} else {
    Write-Host "`nCapture did not complete (exit $code). Nothing was written if you aborted." -ForegroundColor Yellow
}
exit $code
