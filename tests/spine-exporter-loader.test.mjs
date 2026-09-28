import assert from 'node:assert/strict';
import { test } from 'node:test';
import vm from 'node:vm';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { patchRenderer, patchHandler, viewportFunction, load } from '../patches/spine-exporter-loader.mjs';

function measure(duration, fps, boundsAtTime) {
    let time = 0;
    const samples = [];
    const skeleton = {
        data: {},
        setToSetupPose() { time = 0; },
        updateWorldTransform() {},
        getBounds(offset, size) {
            samples.push(time);
            const [x, y, width, height] = boundsAtTime(time);
            Object.assign(offset, { x, y });
            Object.assign(size, { x: width, y: height });
        },
    };
    class AnimationState {
        constructor() { this.time = 0; }
        setAnimation() {}
        update(delta) { this.time += delta; }
        apply() { time = Math.min(this.time, duration); }
    }
    const context = { AnimationState, AnimationStateData: class {}, Vector2: class {} };
    const calculate = vm.runInNewContext(viewportFunction + '\ncalculateAnimationViewport;', context);
    return { bounds: calculate({ name: 'idle', duration }, skeleton, fps), samples };
}

test('tall character uses height; round outward and preserve explicit canvas overrides', () => {
    const original = `function calculateAnimationViewport(animation, skeleton, fps) {
    return {};
}
this.canvas.width = viewsize?.width || Math.round(viewport.width);
this.canvas.height = viewsize?.height || Math.round(viewport.width);`;
    const patched = patchRenderer(original);
    const assignments = patched.slice(patched.indexOf('this.canvas.width'));
    const context = { canvas: {}, viewsize: undefined, viewport: { width: 100.1, height: 300.3 } };
    vm.runInNewContext(assignments, context);
    assert.deepEqual(context.canvas, { width: 101, height: 301 });
    context.viewsize = { width: 50, height: 60 };
    vm.runInNewContext(assignments, context);
    assert.deepEqual(context.canvas, { width: 50, height: 60 });
    assert.equal(patchRenderer(patched), patched);
});

test('largest pose and final pose both remain inside the padded viewport', () => {
    const { bounds, samples } = measure(1, 20, time => time >= 0.999
        ? [-15.2, -20.3, 60.5, 180.7] : [0, 0, 20, 40]);
    assert.ok(samples.length >= 21);
    assert.ok(bounds.x <= -17.2 && bounds.y <= -22.3);
    assert.ok(bounds.x + bounds.width >= 47.3);
    assert.ok(bounds.y + bounds.height >= 162.4 - 1e-10);
});

test('intermediate extreme at low FPS is sampled at the renderer delta cap', () => {
    const { bounds, samples } = measure(0.3, 5, time => time > 0.12 && time < 0.14
        ? [0, -100, 20, 300] : [0, 0, 20, 40]);
    assert.ok(samples.includes(0.128));
    assert.equal(bounds.y, -102);
    assert.equal(bounds.height, 304);
});

test('zero-duration animation and invalid bounds/FPS', () => {
    assert.equal(measure(0, 20, () => [0, 0, 20, 40]).bounds.height, 44);
    assert.throws(() => measure(1, 0, () => [0, 0, 1, 1]), /Invalid rendering FPS/);
    assert.throws(() => measure(1, 20, () => [0, Infinity, 1, 1]), /bounds are invalid/);
});

test('upstream and old forced auto-crop are disabled; unsupported layouts fail', () => {
    for (const value of ['viewSize !== undefined', 'true', 'false']) {
        assert.equal(patchHandler(`autoCrop: ${value},`), 'autoCrop: false,');
    }
    assert.throws(() => patchHandler('changed API'), /Unsupported/);
    assert.throws(() => patchRenderer('changed API'), /Unsupported/);
});

test('loader leaves unrelated packages alone', async () => {
    const result = { format: 'module', source: 'export const untouched = true;' };
    assert.equal(await load(pathToFileURL(path.resolve('other/dist/renderer.js')).href, {}, async () => result), result);
});
