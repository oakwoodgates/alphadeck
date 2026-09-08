<#
.SYNOPSIS
  ensure-venv.ps1 -- provision (or refresh) THIS checkout's backend venv with the extras the suite needs.

.DESCRIPTION
  The PowerShell twin of scripts/ensure-venv.sh (same contract, same stamp -- they interoperate on one venv).

  Makes backend\.venv exist and carry `pip install -e ".[dev,replay]"` -- exactly what CI installs
  (.github/workflows/ci.yml): the runtime deps, pytest + pytest-xdist (`pytest -n 6`) + pytest-timeout (the
  hang guard), the pinned ruff/black, and duckdb + pyarrow so the replay tests EXECUTE instead of being
  collect-skipped in a lean venv (skipped != passed). Per-checkout BY CONSTRUCTION: the venv lives inside the
  checkout this script sits in, so every git worktree provisions its OWN -- never borrow a sibling's or the
  main checkout's (its editable install points at the OTHER tree; cwd shadowing only makes it look fine).

  Agent-legible by contract: non-interactive, idempotent (a ready venv is a ~1 s no-op -- the stamp + import
  probe), non-destructive (never deletes a venv; `Remove-Item -Recurse backend\.venv` is the operator's call),
  exit-code-clean (0 = ready, 1 = fail), greppable (--> progress + a terminal OK:/FAIL: line).

  "Current" = the venv's stamp matches the sha1 of backend\pyproject.toml (so a dependency bump on your branch
  re-pips on the next run) AND scripts\ensure-venv-probe.py passes: the imports are present and the editable
  `app` resolves to THIS checkout's backend\ (a borrowed/copied venv fails that and gets re-pointed).

  Windows PowerShell 5.1 compatible: no &&, explicit $LASTEXITCODE checks, no native 2>&1 -- and ASCII ONLY
  (5.1 reads a BOM-less file as the ANSI codepage, where a UTF-8 em dash decodes to a curly quote that
  PowerShell treats as a string delimiter; keep this file 7-bit).

.PARAMETER Check
  Report only: exit 0 if the venv is ready, 1 if not; installs NOTHING.

.PARAMETER Force
  Re-run the pip install even when the stamp says current.

.PARAMETER Python
  The interpreter used to CREATE a missing venv (default: $env:ALPHADECK_PYTHON, else `python`); must be
  >= 3.11 (requires-python). Ignored once the venv exists.

.EXAMPLE
  .\scripts\ensure-venv.ps1                 # from the checkout root (any cwd works: paths derive from the script's own location)
  .\scripts\ensure-venv.ps1 -Check          # is it ready? (no install)
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ensure-venv.ps1    # from Git Bash / cmd
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Force,
    [string]$Python = $(if ($env:ALPHADECK_PYTHON) { $env:ALPHADECK_PYTHON } else { 'python' })
)

# Native exit codes are checked explicitly via $LASTEXITCODE. Under 'Stop', PowerShell 5.1 can turn a native
# command's stderr chatter (pip WARNING lines) into a terminating error -- so stay on 'Continue'. No
# Set-StrictMode: a native command that fails to LAUNCH leaves $LASTEXITCODE unset, and strict mode would turn
# that into a variable-undefined exception instead of the clean FAIL: line the `-ne 0` checks below produce.
$ErrorActionPreference = 'Continue'

function Say([string]$Message)  { Write-Output "--> $Message" }
function Fail([string]$Message) { [Console]::Error.WriteLine("FAIL: $Message"); exit 1 }

# --- paths --------------------------------------------------------------------------------------------
$ScriptDir = $PSScriptRoot
$Root      = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$Backend   = Join-Path $Root 'backend'
$Venv      = Join-Path $Backend '.venv'
$Probe     = Join-Path $ScriptDir 'ensure-venv-probe.py'
$Extras    = 'dev,replay'
$Stamp     = Join-Path $Venv '.alphadeck-venv-stamp'
$PyProject = Join-Path $Backend 'pyproject.toml'

if (-not (Test-Path $PyProject)) { Fail "no backend\pyproject.toml under $Root -- is this an Alpha Deck checkout?" }
if (-not (Test-Path $Probe))     { Fail "missing $Probe" }

function Get-VenvPython {  # the venv's interpreter: Windows layout first, then POSIX (pwsh on Linux/macOS)
    foreach ($rel in @('Scripts/python.exe', 'bin/python')) {
        $candidate = Join-Path $Venv $rel
        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    }
    return $null
}

function Test-PythonAtLeast311([string]$Exe) {  # $true when $Exe runs and is >= 3.11
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    & $Exe -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
    return ($LASTEXITCODE -eq 0)
}

function Get-PythonVersion([string]$Exe) {  # for messages only
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return 'not found' }
    $v = (& $Exe --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $v) { return 'not runnable' }
    return $v
}

# --- 1. create the venv if absent ---------------------------------------------------------------------
$Vpy = Get-VenvPython
if (-not $Vpy) {
    if (Test-Path $Venv) { Fail "$Venv exists but has no interpreter (a broken venv) -- remove it and re-run" }
    if ($Check) { Fail "no venv at $Venv (run scripts\ensure-venv.ps1 to create it)" }
    if (-not (Test-PythonAtLeast311 $Python)) {
        Fail "$Python is $(Get-PythonVersion $Python) -- need Python >= 3.11 (pass -Python or set ALPHADECK_PYTHON)"
    }
    Say "creating $Venv with $Python ($(Get-PythonVersion $Python))"
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed (exit $LASTEXITCODE)" }
    $Vpy = Get-VenvPython
    if (-not $Vpy) { Fail "venv created but no interpreter found under $Venv" }
}

# --- 2. the venv's interpreter must run and be >= 3.11 ------------------------------------------------
if (-not (Test-PythonAtLeast311 $Vpy)) {
    Fail "$Vpy is not runnable or is below 3.11 ($(Get-PythonVersion $Vpy)) -- remove $Venv and re-run"
}

# --- 3. current? (stamp + probe) -> fast no-op --------------------------------------------------------
# No quotes inside the one-liner: PowerShell 5.1 strips embedded double quotes from native-command arguments
# (Python would see `open(path, rb)`), so the file is read via pathlib instead of an open() mode string.
$sha = (& $Vpy -c 'import hashlib, pathlib, sys; print(hashlib.sha1(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' $PyProject | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $sha) { Fail "could not hash $PyProject with $Vpy" }
$wantStamp = "pyproject_sha1=$sha;extras=$Extras"
$haveStamp = if (Test-Path $Stamp) { ([IO.File]::ReadAllText($Stamp)).Trim() } else { '' }

$probeOut = ''
$probeOk  = $false
if ($haveStamp -eq $wantStamp) {
    $probeOut = (& $Vpy $Probe $Backend | Out-String).Trim()
    $probeOk  = ($LASTEXITCODE -eq 0)
}
if (-not $Force -and $haveStamp -eq $wantStamp -and $probeOk) {
    Write-Output "OK: venv ready (no-op) -- $Vpy | $probeOut"
    exit 0
}
if ($Check) {
    $reason = if ($haveStamp -ne $wantStamp) { 'stamp mismatch (never installed, or backend\pyproject.toml changed since)' }
              elseif ($probeOut) { $probeOut } else { 'probe failed' }
    Fail "venv at $Venv is not ready -- $reason (run scripts\ensure-venv.ps1)"
}

# --- 4. install (editable + the extras) ---------------------------------------------------------------
if ($Force)                          { Say '-Force: re-running the pip install' }
elseif ($haveStamp -ne $wantStamp)   { Say 'venv not current (never installed, or backend\pyproject.toml changed since)' }
else                                 { Say "venv not ready: $probeOut" }
Say "pip install -e `".[$Extras]`" into $Venv (a fresh venv takes a minute or two; pip's wheel cache makes it faster after)"
Push-Location $Backend
$pipExit = 1
try {
    & $Vpy -m pip install --progress-bar off -e ".[$Extras]"
    $pipExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($pipExit -ne 0) { Fail "pip install failed (exit $pipExit)" }

# --- 5. prove it, then stamp it -----------------------------------------------------------------------
$probeOut = (& $Vpy $Probe $Backend | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail "venv still not ready after the install -- $probeOut" }
[IO.File]::WriteAllText($Stamp, $wantStamp)   # UTF-8 without BOM, no newline: byte-identical to the .sh twin's stamp
Write-Output "OK: venv ready -- $Vpy | $probeOut"
exit 0
