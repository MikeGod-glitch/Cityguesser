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
    const frame = {dataset: {}, querySelector() {return retry;}};
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
