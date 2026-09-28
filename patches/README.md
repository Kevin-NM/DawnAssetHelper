# Complete-image Spine export

DawnAssetHelper automatically loads `spine-exporter-loader.mjs` when running the
npm spine-exporter CLI. Fixes are applied in memory; the global npm package is
unchanged. Restart DawnAssetHelper after updating the application.

The loader:

- Fixes the upstream canvas-height typo (`viewport.width` used as height).
- Measures every rendered pose, including the final pose and low-FPS time steps,
  using the same AnimationState playback as the renderer.
- Replaces the old percentile patch, which discarded the largest 10% of poses
  and could clip raised arms, accessories, and moving clothing.
- Adds two output pixels on each side and rounds dimensions upward to retain edges.
- Disables `sharp.trim()` automatic cropping to preserve the full rendered canvas.

Large or unusual poses may produce a larger canvas. This is intentional: the
export preserves the content instead of guessing which geometry to discard.
Existing cropped GIFs must be exported again from their skeleton/atlas assets;
their missing pixels cannot be recovered from the GIF itself.

The supported source layout is spine-exporter 0.8.0, including the project's old
patches. Unrecognized source layouts fail with a diagnostic instead of exporting
with potentially unsafe bounds. Node.js and the npm package must be available;
NVM shims are resolved using `npm root -g`.

For optional manual patching outside DawnAssetHelper:

```powershell
.\patches\apply-spine-exporter-patches.ps1
# Or select an explicit installed package:
.\patches\apply-spine-exporter-patches.ps1 -PackageRoot C:\path\to\spine-exporter
```

Manual patching keeps the first original files as `.dawn-backup`. Reapply after
npm updates only if you also use the CLI directly. The application does not need
manual reapplication.

Regression checks:

```powershell
node tests/spine-exporter-loader.test.mjs
python -m unittest discover -s tests -p "test_*.py"
```
