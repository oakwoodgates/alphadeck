<#
.SYNOPSIS
  Live gate-2 check for S5 5b -- the narrative->chain decompose seam (PR #62).

.DESCRIPTION
  Rebuilds + restarts the docker stack (picking up `.env` and the phase2-s5b backend), drafts a value chain
  from a narrative, prints the resolved chain, and SCANS every prose string for numbers -- the one bound
  that rests on the prompt (the drafter must never state a figure).

  PREREQS:
    1. Be on the phase2-s5b branch -- the /draft-chain endpoint is not on main until #62 merges, and the
       image is built from the working tree.
    2. Put your ANTHROPIC_API_KEY in `.env` (copy `.env.example`). With no key the draft comes back EMPTY
       (fail-open) -- that proves the contract, but won't exercise the prompt (nothing to read).

.PARAMETER Api
  API base URL. Default http://127.0.0.1:8000 (the compose backend's direct port).

.PARAMETER Narrative
  The narrative to decompose. Default is a small-modular-nuclear thesis (its names are in the demo seed,
  so they resolve to PLACED).

.PARAMETER NoBuild
  Skip the image rebuild on a re-run (faster once the stack is already up on this branch's code).

.EXAMPLE
  .\scripts\run_5b_draft_check.ps1
#>
param(
    [string]$Api = "http://127.0.0.1:8000",
    [string]$Narrative = "small modular nuclear is about to rip - reactor developers, the enrichment and fuel supply chain, and the utilities that will run them",
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

if ($NoBuild) {
    Write-Host "==> Restarting the stack (no rebuild)..."
    docker compose up -d
} else {
    Write-Host "==> Rebuilding + restarting the stack (picks up .env and the phase2-s5b backend)..."
    docker compose up -d --build
}

Write-Host "==> Waiting for the API healthcheck at $Api/health ..."
$deadline = (Get-Date).AddMinutes(3)
$ok = $false
do {
    Start-Sleep -Seconds 3
    try { Invoke-RestMethod "$Api/health" -TimeoutSec 5 | Out-Null; $ok = $true } catch { $ok = $false }
} until ($ok -or (Get-Date) -gt $deadline)
if (-not $ok) { throw "API did not become healthy at $Api/health within 3 minutes (check: docker compose logs backend)" }

Write-Host "==> Creating a throwaway thesis with the narrative..."
$create = Invoke-RestMethod -Method Post -Uri "$Api/workbench/theses" -ContentType "application/json" `
    -Body (@{ name = "5b draft check"; narrative = $Narrative } | ConvertTo-Json)
$tid = $create.id
Write-Host "    thesis id: $tid  (an Incubating draft on the demo tenant; delete it when done)"

Write-Host "==> Drafting the chain (POST /workbench/theses/$tid/draft-chain)..."
$draft = Invoke-RestMethod -Method Post -Uri "$Api/workbench/theses/$tid/draft-chain"

if (-not $draft.placements -or @($draft.placements).Count -eq 0) {
    Write-Host "`n(empty draft -- FAIL-OPEN. No ANTHROPIC_API_KEY reached the container, or the model declined." -ForegroundColor Yellow
    Write-Host " Put your key in .env and re-run; or this is the no-key contract working as designed.)" -ForegroundColor Yellow
    return
}

Write-Host "`n--- SEGMENTS ---"
foreach ($s in $draft.segments) {
    $d = if ($s.descriptor) { " ($($s.descriptor))" } else { "" }
    Write-Host ("  - {0}{1}" -f $s.label, $d)
}

Write-Host "`n--- PLACEMENTS ---"
foreach ($p in $draft.placements) {
    switch ($p.status) {
        "placed"    { $tag = "PLACED    $($p.ticker)  [$($p.security_id)]" }
        "ambiguous" { $tag = "AMBIGUOUS -> you pick: " + ((@($p.candidates) | ForEach-Object { "$($_.ticker)/CIK $($_.cik)" }) -join ", ") }
        "absent"    { $tag = "ABSENT (suggested, not in your universe)" }
        default     { $tag = $p.status }
    }
    Write-Host ("  [{0}] {1}" -f $p.segment, $p.name)
    Write-Host ("        {0}" -f $tag)
    Write-Host ("        prose: {0}" -f $p.prose)
}

Write-Host "`n--- THE NO-NUMBER CHECK (the bound that rests on the prompt) ---"
$withDigits = @($draft.placements | Where-Object { $_.prose -match '\d' })
if ($withDigits.Count -eq 0) {
    Write-Host "  PASS -- no digit appears in any prose. The prompt bound held." -ForegroundColor Green
} else {
    Write-Host "  REVIEW -- these prose strings contain a digit; confirm none is a financial figure" -ForegroundColor Yellow
    Write-Host "  (price / % / share count / runway / market cap / catalyst value):" -ForegroundColor Yellow
    foreach ($p in $withDigits) { Write-Host ("    - [{0}] {1}: {2}" -f $p.segment, $p.name, $p.prose) }
    Write-Host "  If a real figure slipped in, that's the cue to pull the lever (staged-decompose / regex post-filter)."
}
