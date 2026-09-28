#!/usr/bin/env pwsh
# Optional manual patching. DawnAssetHelper applies these fixes in memory itself.
param([string]$PackageRoot = "")
$ErrorActionPreference = "Stop"
if (-not $PackageRoot) {
    $npmRoot = & npm root -g
    if ($LASTEXITCODE -ne 0) { throw "Cannot locate the global npm directory." }
    $PackageRoot = Join-Path ($npmRoot | Select-Object -Last 1) "spine-exporter"
}
& node (Join-Path $PSScriptRoot "spine-exporter-loader.mjs") --apply $PackageRoot
if ($LASTEXITCODE -ne 0) { throw "spine-exporter patch failed." }
Write-Host "Complete-image export patches applied. Original files saved as .dawn-backup."
