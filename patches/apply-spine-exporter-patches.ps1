#!/usr/bin/env pwsh
# apply-spine-exporter-patches.ps1
# Patches spine-exporter for DawnAssetHelper GIF export.
# Re-run after `npm install -g spine-exporter` or `npm update -g spine-exporter`.

$ErrorActionPreference = "Stop"

$rendererPath = "$env:APPDATA\npm\node_modules\spine-exporter\dist\renderer.js"
$handlerPath = "$env:APPDATA\npm\node_modules\spine-exporter\dist\handler.js"

foreach ($f in @($rendererPath, $handlerPath)) {
    if (-not (Test-Path -LiteralPath $f)) {
        Write-Error "File not found: $f`nIs spine-exporter installed globally? Run: npm install -g spine-exporter"
        exit 1
    }
}

Write-Host "Applying spine-exporter patches..." -ForegroundColor Cyan

# --- Patch 1: renderer.js — height bug fix ---
$renderer = Get-Content -LiteralPath $rendererPath -Raw
$old1 = 'viewsize?.height || Math.round(viewport.width)'
$new1 = 'viewsize?.height || Math.round(viewport.height)'
if ($renderer.Contains($old1)) {
    $renderer = $renderer.Replace($old1, $new1)
    Set-Content -LiteralPath $rendererPath -Value $renderer -NoNewline
    Write-Host "  [OK] renderer.js: viewport.height fix" -ForegroundColor Green
} elseif ($renderer.Contains($new1)) {
    Write-Host "  [SKIP] renderer.js: already patched (height)" -ForegroundColor Yellow
} else {
    Write-Warning "renderer.js: height fix target not found."
}

# --- Patch 2: renderer.js — percentile AABB (replace entire function) ---
$renderer = Get-Content -LiteralPath $rendererPath -Raw
$oldFn = @'
function calculateAnimationViewport(animation, skeleton, fps) {
    skeleton.setToSetupPose();
    let steps = animation.duration ? fps * animation.duration : 1;
    let stepTime = animation.duration ? animation.duration / steps : 0, time = 0;
    let minX = 100000000, maxX = -100000000, minY = 100000000, maxY = -100000000;
    let offset = new Vector2(), size = new Vector2();
    for (let i = 0; i < steps; i++, time += stepTime) {
        animation.apply(skeleton, time, time, false, [], 1, MixBlend.setup, MixDirection.mixIn);
        skeleton.updateWorldTransform();
        skeleton.getBounds(offset, size);
        if (!isNaN(offset.x) && !isNaN(offset.y) && !isNaN(size.x) && !isNaN(size.y)) {
            minX = Math.min(offset.x, minX);
            maxX = Math.max(offset.x + size.x, maxX);
            minY = Math.min(offset.y, minY);
            maxY = Math.max(offset.y + size.y, maxY);
        }
        else
            throw new Error("Animation bounds are invalid: " + animation.name);
    }
    return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}
'@
$newFn = @'
function calculateAnimationViewport(animation, skeleton, fps) {
    skeleton.setToSetupPose();
    let steps = animation.duration ? fps * animation.duration : 1;
    let stepTime = animation.duration ? animation.duration / steps : 0, time = 0;
    let offset = new Vector2(), size = new Vector2();
    const frames = [];
    for (let i = 0; i < steps; i++, time += stepTime) {
        animation.apply(skeleton, time, time, false, [], 1, MixBlend.setup, MixDirection.mixIn);
        skeleton.updateWorldTransform();
        skeleton.getBounds(offset, size);
        if (!isNaN(offset.x) && !isNaN(offset.y) && !isNaN(size.x) && !isNaN(size.y)) {
            frames.push({ minX: offset.x, maxX: offset.x + size.x, minY: offset.y, maxY: offset.y + size.y });
        }
        else
            throw new Error("Animation bounds are invalid: " + animation.name);
    }
    if (frames.length === 0)
        throw new Error("No valid animation bounds: " + animation.name);
    frames.sort((a, b) => ((a.maxX - a.minX) * (a.maxY - a.minY)) - ((b.maxX - b.minX) * (b.maxY - b.minY)));
    const trimIdx = Math.max(1, Math.floor(frames.length * 0.9));
    const trimmed = frames.slice(0, trimIdx);
    let minX = 100000000, maxX = -100000000, minY = 100000000, maxY = -100000000;
    for (const f of trimmed) {
        minX = Math.min(f.minX, minX);
        maxX = Math.max(f.maxX, maxX);
        minY = Math.min(f.minY, minY);
        maxY = Math.max(f.maxY, maxY);
    }
    return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}
'@
if ($renderer.Contains("const frames = [];")) {
    Write-Host "  [SKIP] renderer.js: already patched (percentile AABB)" -ForegroundColor Yellow
} elseif ($renderer.Contains($oldFn)) {
    $renderer = $renderer.Replace($oldFn, $newFn)
    Set-Content -LiteralPath $rendererPath -Value $renderer -NoNewline
    Write-Host "  [OK] renderer.js: percentile AABB applied" -ForegroundColor Green
} else {
    Write-Warning "renderer.js: AABB function target not found."
}

# --- Patch 3: handler.js — always enable autoCrop ---
$handler = Get-Content -LiteralPath $handlerPath -Raw
$old3 = 'autoCrop: viewSize !== undefined,'
$new3 = 'autoCrop: true,'
if ($handler.Contains($old3)) {
    $handler = $handler.Replace($old3, $new3)
    Set-Content -LiteralPath $handlerPath -Value $handler -NoNewline
    Write-Host "  [OK] handler.js: autoCrop always-on" -ForegroundColor Green
} elseif ($handler.Contains($new3)) {
    Write-Host "  [SKIP] handler.js: already patched (autoCrop)" -ForegroundColor Yellow
} else {
    Write-Warning "handler.js: autoCrop target not found."
}

Write-Host "`nDone. Restart the DawnAssetHelper server for Python changes to take effect." -ForegroundColor Cyan
