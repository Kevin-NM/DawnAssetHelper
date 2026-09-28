import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Applied in memory so npm upgrades and old global patches cannot restore clipping.
export const viewportFunction = `function calculateAnimationViewport(animation, skeleton, fps) {
    if (!Number.isFinite(fps) || fps <= 0)
        throw new Error("Invalid rendering FPS: " + fps);
    skeleton.setToSetupPose();
    const previewState = new AnimationState(new AnimationStateData(skeleton.data));
    previewState.setAnimation(0, animation.name, false);
    // Match TimeKeeper.update(), including its delta cap at low FPS.
    const delta = Math.min(1 / fps, 0.064);
    const steps = Math.max(1, Math.ceil(animation.duration / delta));
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const offset = new Vector2(), size = new Vector2();
    for (let i = 0; i <= steps; i++) {
        if (i > 0) previewState.update(delta);
        previewState.apply(skeleton);
        skeleton.updateWorldTransform();
        skeleton.getBounds(offset, size);
        if (![offset.x, offset.y, size.x, size.y].every(Number.isFinite) || size.x < 0 || size.y < 0)
            throw new Error("Animation bounds are invalid: " + animation.name);
        minX = Math.min(minX, offset.x);
        maxX = Math.max(maxX, offset.x + size.x);
        minY = Math.min(minY, offset.y);
        maxY = Math.max(maxY, offset.y + size.y);
    }
    skeleton.setToSetupPose();
    // Keep antialiased edges inside the canvas, even for fractional coordinates.
    const padding = 2;
    return { x: minX - padding, y: minY - padding,
        width: maxX - minX + padding * 2, height: maxY - minY + padding * 2 };
}`;

export function patchRenderer(source) {
    const bounds = /function calculateAnimationViewport\(animation, skeleton, fps\) \{[\s\S]*?\n\}/;
    const width = /this\.canvas\.width = viewsize\?\.width \|\| Math\.(?:round|ceil)\(viewport\.width\);/;
    const height = /this\.canvas\.height = viewsize\?\.height \|\| Math\.(?:round|ceil)\(viewport\.(?:width|height)\);/;
    if (!bounds.test(source) || !width.test(source) || !height.test(source))
        throw new Error('Unsupported spine-exporter renderer; cannot guarantee complete export bounds.');
    return source.replace(bounds, () => viewportFunction)
        .replace(width, 'this.canvas.width = viewsize?.width || Math.ceil(viewport.width);')
        .replace(height, 'this.canvas.height = viewsize?.height || Math.ceil(viewport.height);');
}

export function patchHandler(source) {
    const autoCrop = /autoCrop: (?:viewSize !== undefined|true|false),/;
    if (!autoCrop.test(source))
        throw new Error('Unsupported spine-exporter handler; cannot disable automatic cropping.');
    return source.replace(autoCrop, 'autoCrop: false,');
}

export async function load(url, context, nextLoad) {
    const result = await nextLoad(url, context);
    if (url.startsWith('file:')) {
        const filename = fileURLToPath(url).replaceAll('\\', '/');
        const patch = filename.endsWith('/spine-exporter/dist/renderer.js') ? patchRenderer
            : filename.endsWith('/spine-exporter/dist/handler.js') ? patchHandler : null;
        if (patch) {
            const source = String(result.source).replace(/\/\/# sourceMappingURL=.*$/m, '');
            return { ...result, source: patch(source) };
        }
    }
    return result;
}

// Optional manual installation; the application normally uses the in-memory loader.
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
    if (process.argv[2] !== '--apply' || !process.argv[3])
        throw new Error('Usage: node spine-exporter-loader.mjs --apply <spine-exporter package directory>');
    const root = path.resolve(process.argv[3]);
    const targets = [['renderer.js', patchRenderer], ['handler.js', patchHandler]];
    const changes = await Promise.all(targets.map(async ([name, patch]) => {
        const filename = path.join(root, 'dist', name);
        const source = await fs.readFile(filename, 'utf8');
        return { filename, source, patched: patch(source) };
    }));
    for (const { filename, source, patched } of changes) {
        if (source === patched) continue;
        try { await fs.writeFile(filename + '.dawn-backup', source, { flag: 'wx' }); }
        catch (error) { if (error.code !== 'EEXIST') throw error; }
        await fs.writeFile(filename, patched, 'utf8');
        console.log('Patched ' + filename);
    }
}
