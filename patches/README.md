# spine-exporter Patches

## Problem
spine-exporter's `calculateAnimationViewport()` takes the union of ALL animation frame bounds,
producing an oversized canvas (e.g., 2728x5490 for a ~500x1000 character). This causes:
- Wasted memory during rendering
- Oversized GIF output when no `--canvas-size` is specified

## Patches Applied

### 1. renderer.js — Height bug fix (line 120)
**File**: `node_modules/spine-exporter/dist/renderer.js`
**Original**: `this.canvas.height = viewsize?.height || Math.round(viewport.width);`
**Fixed**: `this.canvas.height = viewsize?.height || Math.round(viewport.height);`
**Reason**: Typo — `viewport.width` should be `viewport.height`

### 2. handler.js — Always enable autoCrop (line 51)
**File**: `node_modules/spine-exporter/dist/handler.js`
**Original**: `autoCrop: viewSize !== undefined,`
**Fixed**: `autoCrop: true,`
**Reason**: The auto-crop infrastructure (sharp.trim()) already exists in exporter.js but was only
activated when `--canvas-size` was explicitly passed. Enabling it unconditionally makes GIF output
match Spine Pro's behavior: tight crop around actual visible content.

## How to Apply
Run from the project root:
```powershell
.\patches\apply-spine-exporter-patches.ps1
```

## When to Re-apply
After any `npm install -g spine-exporter` or `npm update -g spine-exporter`.
