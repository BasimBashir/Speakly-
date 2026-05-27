# Launch the Next.js dev server. PowerShell counterpart of start_ui_dev.sh.
$ErrorActionPreference = "Stop"

$BaseDir = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location (Join-Path $BaseDir "ui")

Write-Host "Starting Dograh UI (DEV MODE) at $(Get-Date) in $(Get-Location)"
Write-Host "Listening on http://0.0.0.0:3000"
Write-Host ""

npm run dev -- --hostname 0.0.0.0
