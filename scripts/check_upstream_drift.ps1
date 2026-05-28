#!/usr/bin/env pwsh
# Report how far this fork has drifted from the latest dograh-vX.Y.Z release tag
# on the upstream remote. Run weekly (or from CI) so merges never get scary.
#
# Exits 1 if drift exceeds DRIFT_WARN_THRESHOLD commits behind (default 30).

$ErrorActionPreference = 'Stop'

$DriftWarnThreshold = if ($env:DRIFT_WARN_THRESHOLD) { [int]$env:DRIFT_WARN_THRESHOLD } else { 30 }
$UpstreamRemote     = if ($env:UPSTREAM_REMOTE) { $env:UPSTREAM_REMOTE } else { 'upstream' }

try {
    git remote get-url $UpstreamRemote 2>$null | Out-Null
} catch {
    Write-Host "Error: remote '$UpstreamRemote' not configured. Run scripts/setup_fork.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host 'Fetching upstream tags...' -ForegroundColor Blue
git fetch $UpstreamRemote --tags --quiet

$LatestTag = (git tag --sort=-creatordate | Select-String -Pattern '^dograh-v' | Select-Object -First 1).ToString()
if (-not $LatestTag) {
    Write-Host 'No dograh-vX.Y.Z tags found. Did the upstream fetch succeed?' -ForegroundColor Red
    exit 1
}

$CurrentBranch   = (git rev-parse --abbrev-ref HEAD).Trim()
$MergeBase       = (git merge-base HEAD $LatestTag).Trim()
$Behind          = [int]((git rev-list --count "HEAD..$LatestTag").Trim())
$Ahead           = [int]((git rev-list --count "$LatestTag..HEAD").Trim())
$AncestorSubject = (git log -1 --format='%h %s' $MergeBase).Trim()

Write-Host ''
Write-Host "Branch:          $CurrentBranch"      -ForegroundColor Blue
Write-Host "Latest upstream: $LatestTag"          -ForegroundColor Blue
Write-Host "Common ancestor: $AncestorSubject"    -ForegroundColor Blue
Write-Host "Ahead:           $Ahead commits (your fork's work)" -ForegroundColor Blue
Write-Host "Behind:          $Behind commits"     -ForegroundColor Blue
Write-Host ''

if ($Behind -eq 0) {
    Write-Host "OK - Up to date with $LatestTag" -ForegroundColor Green
    exit 0
}

if ($Behind -gt $DriftWarnThreshold) {
    Write-Host "WARNING - Drift exceeds $DriftWarnThreshold commits - merge soon to keep conflicts manageable." -ForegroundColor Red
    Write-Host "  Suggested: git checkout -b merge/$LatestTag; git merge $LatestTag" -ForegroundColor Blue
    exit 1
}

Write-Host "Drift within tolerance ($Behind / $DriftWarnThreshold). No action required yet." -ForegroundColor Yellow
Write-Host "  When ready: git checkout -b merge/$LatestTag; git merge $LatestTag" -ForegroundColor Blue
