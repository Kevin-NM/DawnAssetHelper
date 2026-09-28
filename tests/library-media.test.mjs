import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Exercise the actual UI functions with small DOM/clipboard doubles.
const html = fs.readFileSync(new URL('../server/templates/index.html', import.meta.url), 'utf8');
const code = html.slice(html.indexOf('    function stopGifPlayback(card)'), html.indexOf('    const deletingLibraryHeroes'));
function setup(options = {}) {
  const cards = [];
  const alerts = [];
  const writes = [];
  const png = new Blob(['full-size-transparent-png'], {type: 'image/png'});
  const context = {
    window: {isSecureContext: true, ClipboardItem: true, addEventListener() {}},
    navigator: {clipboard: {write(items) {
      writes.push(items);
      return Promise.all(items.map(item => item.data['image/png'])).then(() => {
        if (options.denied) throw new Error('Permission denied');
      });
    }}},
    ClipboardItem: class { constructor(data) { this.data = data; } },
    fetch: options.fetch || (() => Promise.resolve({ok: true, blob: async () => png})),
    alert: message => alerts.push(message), setTimeout() {},
    document: {
      querySelectorAll: () => cards,
      createElement: () => ({style: {}, removeAttribute(name) { delete this[name]; }, remove() { this.owner.animation = null; }})
    }
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  function card(src) {
    const result = {dataset: {src}, label: {}, animation: null,
      querySelector(selector) {
        if (selector === '.gif-anim') return this.animation;
        if (selector === '.gif-play-label') return this.label;
        return {appendChild: image => { image.owner = this; this.animation = image; }};
      }};
    cards.push(result);
    return result;
  }
  return {context, cards, card, alerts, writes, png};
}

test('hover loads only one GIF, repeated enter is stable and leave releases src', () => {
  const {context, card} = setup();
  const first = card('/first.gif'), second = card('/second.gif');
  context.startGifPlayback(first);
  const image = first.animation;
  context.startGifPlayback(first);
  assert.equal(first.animation, image);
  assert.equal(image.src, '/first.gif');
  context.startGifPlayback(second);
  assert.equal(first.animation, null);
  assert.equal(image.src, undefined);
  assert.equal(second.animation.src, '/second.gif');
  const secondImage = second.animation;
  context.stopGifPlayback(second);
  assert.equal(second.animation, null);
  assert.equal(secondImage.src, undefined);
});

test('decode error restores static preview', () => {
  const {context, card} = setup();
  const target = card('/broken.gif');
  context.startGifPlayback(target);
  target.animation.onerror();
  assert.equal(target.animation, null);
});

test('copy writes PNG within user gesture, before fetch finishes, and waits for success', async () => {
  let resolveFetch;
  const {context, writes, png, alerts} = setup({fetch: () => new Promise(resolve => { resolveFetch = resolve; })});
  const button = {textContent: 'Copy', dataset: {image: '/full.png'}, isConnected: true};
  const pending = context.copyLibraryImage(button);
  assert.equal(writes.length, 1);
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, 'Copy');
  resolveFetch({ok: true, blob: async () => png});
  await pending;
  assert.equal(await writes[0][0].data['image/png'], png);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, '已複製，可直接貼上');
  assert.equal(alerts.length, 0);
});

test('unsupported clipboard reports failure and does not fetch', async () => {
  const {context, writes, alerts} = setup({fetch: () => { throw new Error('Must not fetch'); }});
  context.window.isSecureContext = false;
  const button = {textContent: 'Copy', dataset: {image: '/full.png'}};
  await context.copyLibraryImage(button);
  assert.equal(writes.length, 0);
  assert.equal(button.textContent, '複製失敗');
  assert.match(alerts[0], /不支援/);
  assert.equal(button.disabled, false);
});

test('permission denial and missing PNG never report success', async () => {
  for (const options of [{denied: true}, {fetch: async () => ({ok: false})}]) {
    const {context, alerts} = setup(options);
    const button = {textContent: 'Copy', dataset: {image: '/full.png'}};
    await context.copyLibraryImage(button);
    assert.equal(button.textContent, '複製失敗');
    assert.equal(button.disabled, false);
    assert.equal(alerts.length, 1);
  }
});
