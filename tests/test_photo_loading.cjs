const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const source = readFileSync(join(__dirname, '../static/script.js'), 'utf8');
const settle = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

function visit({complete = false, failed = false, next, replies = []} = {}) {
    const events = {}, windowEvents = {}, calls = [], images = [], timers = new Map();
    let serial = 0;
    const retry = {hidden: true, addEventListener(name, cb) {this[name] = cb;}};
    const error = {hidden: true};
    const frame = {dataset: {}, querySelector(selector) {return selector === '.photo-retry' ? retry : null;}};
    const photo = {complete, naturalWidth: complete && !failed ? 100 : 0, hidden: failed,
        dataset: {prefetchEndpoint: '/prefetch-image?question_id=current', nextImage: next},
        parentElement: frame, nextElementSibling: error,
        getAttribute() {return '/photos/current';},
        addEventListener(name, cb) {events[name] = cb;}};
    const document = {visibilityState: 'visible', querySelector(selector) {
        return selector === '.city-photo' ? photo : null;
    }};
    const window = {addEventListener(name, cb) {windowEvents[name] = cb;}};
    const context = {document, window, AbortController,
        Image: class {constructor() {images.push(this);}},
        setTimeout(cb, delay) {timers.set(++serial, {cb, delay}); return serial;},
        clearTimeout(id) {timers.delete(id);},
        fetch: async (url, options) => {
            calls.push({url, options});
            return {ok: true, json: async () => replies.shift() || {}};
        },
    };
    vm.runInNewContext(source, context);
    return {photo, retry, error, frame, document, events, windowEvents, calls, images, timers,
        async tick() {
            const item = [...timers].find(([, timer]) => timer.delay === 2500);
            if (item) {timers.delete(item[0]); item[1].cb(); await settle();}
        }};
}

test('next image waits for current photo and uses low priority exactly once', async () => {
    const page = visit({replies: [{image_url: '/photos/next'}]});
    assert.equal(page.calls.length, 0);
    page.photo.complete = true;
    page.photo.naturalWidth = 100;
    page.events.load();
    page.events.load();
    await settle();
    assert.equal(page.frame.dataset.photoState, 'ready');
    assert.equal(page.calls.length, 1);
    assert.equal(page.calls[0].options.cache, 'no-store');
    assert.equal(page.images.length, 1);
    assert.equal(page.images[0].src, '/photos/next');
    assert.equal(page.images[0].fetchPriority, 'low');
});

test('already-loaded result photo warms supplied next image without polling', () => {
    const page = visit({complete: true, next: '/photos/next'});
    assert.equal(page.calls.length, 0);
    assert.equal(page.images[0].src, '/photos/next');
});

test('pending prefetch stops at 12 checks and navigation cancels retries', async () => {
    const page = visit({complete: true});
    await settle();
    for (let i = 0; i < 20; i++) await page.tick();
    assert.equal(page.calls.length, 12);
    assert.equal(page.images.length, 0);
    const leaving = visit({complete: true});
    await settle();
    leaving.windowEvents.pagehide();
    await leaving.tick();
    assert.equal(leaving.calls.length, 1);
    assert.equal(leaving.calls[0].options.signal.aborted, true);
});

test('hidden tabs skip prefetch requests', async () => {
    const page = visit();
    page.document.visibilityState = 'hidden';
    page.photo.complete = true;
    page.photo.naturalWidth = 100;
    page.events.load();
    await page.tick();
    assert.equal(page.calls.length, 0);
    page.document.visibilityState = 'visible';
    await page.tick();
    assert.equal(page.calls.length, 1);
});

test('photo failure permits same-image retry without navigation or answer submission', () => {
    const page = visit({complete: true, failed: true});
    assert.equal(page.frame.dataset.photoState, 'error');
    assert.equal(page.retry.hidden, false);
    assert.equal(page.calls.length, 0);
    page.retry.click();
    assert.equal(page.photo.src, '/photos/current');
    assert.equal(page.photo.hidden, false);
    assert.equal(page.error.hidden, true);
    assert.equal(page.frame.dataset.photoState, 'loading');
    assert.equal(page.calls.length, 0);
});

function viewer({width = 1200, height = 800, ready = true} = {}) {
    const handlers = {}, windowHandlers = {}, captures = new Set();
    const reset = {hidden: true, addEventListener(name, callback) {this[name] = callback;}};
    const viewport = {clientWidth: 600, clientHeight: 400, dataset: {},
        querySelector() {return reset;},
        getBoundingClientRect() {return {left: 20, top: 30};},
        addEventListener(name, callback, options) {handlers[name] = {callback, options};},
        setPointerCapture(id) {captures.add(id);},
        hasPointerCapture(id) {return captures.has(id);},
        releasePointerCapture(id) {captures.delete(id);}};
    const photo = {complete: ready, naturalWidth: ready ? width : 0, naturalHeight: height,
        hidden: false, style: {}, addEventListener(name, callback) {handlers[name] = {callback};}};
    const context = {document: {querySelector() {return null;}},
        window: {addEventListener(name, callback) {windowHandlers[name] = callback;}}};
    vm.runInNewContext(source, context);
    const api = context.createPhotoViewer(photo, viewport);
    return {photo, viewport, reset, captures, api, windowHandlers,
        state() {
            return photo.style.transform.match(/translate\(([-\d.e]+)px, ([-\d.e]+)px\) scale\(([-\d.e]+)\)/).slice(1).map(Number);
        },
        fire(name, options = {}) {
            const event = {target: {closest() {return null;}}, deltaY: -120, deltaMode: 0,
                clientX: 320, clientY: 230, pointerId: 1, pointerType: 'mouse', button: 0, buttons: 1,
                prevented: false, preventDefault() {this.prevented = true;}, ...options};
            handlers[name].callback(event);
            return event;
        }, handlers,
    };
}

test('zoom stays between 1x and 4x, anchors at the mouse, and restores full view', () => {
    const page = viewer();
    assert.equal(page.handlers.wheel.options.passive, false);
    const wheel = page.fire('wheel', {clientX: 420});
    const [x, y, scale] = page.state();
    assert.equal(wheel.prevented, true);
    assert.ok(scale > 1 && scale < 4);
    assert.ok(Math.abs((100 - x) / scale - 100) < .00001);
    assert.equal(y, 0);
    assert.equal(page.reset.hidden, false);
    for (let i = 0; i < 30; i++) page.fire('wheel');
    assert.equal(page.state()[2], 4);
    for (let i = 0; i < 30; i++) page.fire('wheel', {deltaY: 120});
    assert.deepEqual(page.state(), [0, 0, 1]);
    assert.equal(page.reset.hidden, true);
});

test('left drag captures the pointer and clamps photo edges, including letterboxing', () => {
    const page = viewer({width: 1600, height: 800});
    for (let i = 0; i < 10; i++) page.fire('wheel');
    assert.equal(page.fire('pointerdown').prevented, true);
    assert.equal(page.captures.has(1), true);
    page.fire('pointermove', {clientX: 10000, clientY: -10000});
    assert.deepEqual(page.state(), [900, -400, 4]);
    page.fire('pointerup');
    assert.equal(page.captures.size, 0);
    assert.equal(page.viewport.dataset.photoDragging, 'false');
    page.fire('pointermove', {clientX: 320});
    assert.deepEqual(page.state(), [900, -400, 4]);
});

test('portrait photo remains centered on axes smaller than the viewport', () => {
    const page = viewer({width: 600, height: 1200});
    page.fire('wheel');
    page.fire('pointerdown');
    page.fire('pointermove', {clientX: 10000, clientY: 10000});
    const [x, y, scale] = page.state();
    assert.equal(x, 0);
    assert.ok(Math.abs(y - (400 * scale - 400) / 2) < .00001);
});

test('double click, reset button, resize and pointer cancellation recover safely', () => {
    const page = viewer();
    page.fire('wheel');
    page.fire('pointerdown');
    page.fire('pointercancel');
    assert.equal(page.captures.size, 0);
    page.fire('dblclick');
    assert.deepEqual(page.state(), [0, 0, 1]);
    page.fire('wheel');
    page.reset.click();
    assert.deepEqual(page.state(), [0, 0, 1]);
    page.fire('wheel');
    page.fire('pointerdown');
    page.fire('pointermove', {clientX: 5000});
    page.viewport.clientWidth = 900;
    page.windowHandlers.resize();
    assert.equal(page.state()[0], 0);
    page.windowHandlers.blur();
    assert.equal(page.captures.size, 0);
    page.api.reset();
    assert.deepEqual(page.state(), [0, 0, 1]);
});

test('loading images, controls, ctrl-wheel and non-left mouse buttons retain default behavior', () => {
    const loading = viewer({ready: false});
    assert.equal(loading.fire('wheel').prevented, false);
    const page = viewer();
    assert.equal(page.fire('wheel', {ctrlKey: true}).prevented, false);
    assert.equal(page.fire('wheel', {target: {closest() {return {};}}}).prevented, false);
    assert.equal(page.fire('pointerdown').prevented, false);
    page.fire('wheel');
    assert.equal(page.fire('pointerdown', {button: 2}).prevented, false);
    assert.equal(page.fire('pointerdown', {pointerType: 'touch'}).prevented, false);
    assert.equal(page.fire('dragstart').prevented, true);
});
