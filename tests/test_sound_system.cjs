const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const source = readFileSync(join(__dirname, '../static/sound-system.js'), 'utf8');
const settle = () => new Promise(resolve => setTimeout(resolve, 30));

function visit(options = {}) {
    const played = [];
    const handlers = {};
    const local = options.local || {};
    const session = options.session || {};
    const storage = data => ({getItem: key => data[key] || null,
        setItem(key, value) {if (options.storageBlocked) throw Error('blocked'); data[key] = value;}});
    const label = {};
    const toggle = {hidden: true, attrs: {}, setAttribute(k, v) {this.attrs[k] = v;}, querySelector() {return label;}};
    const controls = {hidden: true};
    const volumeValue = {};
    const volumeInput = {attrs: {}, setAttribute(k, v) {this.attrs[k] = v;},
        addEventListener(name, cb) {this[name] = cb;}};
    let serial = 0;
    class Howl {
        constructor(config) {this.config = config; this.listeners = [];}
        state() {return 'loaded';}
        once(name, callback, id) {this.listeners.push({name, callback, id});}
        off(name, callback, id) {this.listeners = this.listeners.filter(x => !(x.name === name && x.callback === callback && x.id === id));}
        duration() {return .01;}
        stop() {}
        play() {
            const id = ++serial;
            played.push(this.config.src[0]);
            setTimeout(() => {
                for (const x of [...this.listeners]) if (x.id === id && x.name === (options.playError ? 'playerror' : 'end')) x.callback();
            }, 1);
            return id;
        }
    }
    const engine = {noAudio: false, usingWebAudio: true,
        ctx: {state: options.locked ? 'suspended' : 'running', resume: async () => {}},
        mute(value) {this.muted = value;}, volume(value) {this.level = value;}};
    const win = {Howl: options.noLibrary ? undefined : Howl, Howler: engine,
        addEventListener(name, cb) {handlers[name] = cb;}};
    const document = {hidden: false,
        querySelector(selector) {return {'[data-sound-toggle]': toggle, '[data-sound-controls]': controls,
            '[data-sound-volume]': volumeInput, '[data-sound-volume-value]': volumeValue,
            '#sound-events-data': options.events ? {textContent: JSON.stringify(options.events)} : null}[selector] || null;},
        addEventListener(name, cb) {handlers[name] = cb;}};
    class CustomEvent extends Event {constructor(name, init) {super(name); this.detail = init.detail;}}
    vm.runInNewContext(source, {window: win, document, localStorage: storage(local), sessionStorage: storage(session),
        performance: {getEntriesByType: () => [{type: options.navigation || 'navigate'}]},
        EventTarget, CustomEvent, setTimeout, clearTimeout, Date});
    return {played, api: win.CityGuesserSound, handlers, toggle, label, engine, document, local, session,
        controls, volumeInput, volumeValue};
}
const result = [{type: 'correct', id: 'a'}, {type: 'streak', id: 'b'}];

test('outcomes play in order and are deduplicated across visits', async () => {
    const session = {};
    const first = visit({events: result, session});
    await settle();
    assert.deepEqual(first.played, ['/static/audio/correct.wav', '/static/audio/streak.wav']);
    first.api.emit('outcome', result);
    const next = visit({events: result, session});
    await settle();
    assert.equal(first.played.length, 2);
    assert.equal(next.played.length, 0);
});
test('reload and back navigation do not replay even without storage', async () => {
    for (const navigation of ['reload', 'back_forward']) {
        const page = visit({events: result, navigation, storageBlocked: true});
        await settle();
        assert.equal(page.played.length, 0);
    }
});
test('muted and locked outcomes are consumed without delayed playback', async () => {
    for (const options of [{locked: true}, {local: {'city-guesser-sound-v1': '{"muted":true}'}}]) {
        const page = visit({...options, events: result});
        await settle();
        page.api.setMuted(false);
        page.engine.ctx.state = 'running';
        page.api.emit('outcome', result);
        await settle();
        assert.equal(page.played.length, 0);
    }
});
test('toggle persists, volume clamps, absent audio library is harmless', () => {
    const page = visit();
    page.api.setMuted(true);
    page.api.setVolume(2);
    assert.equal(page.toggle.attrs['aria-label'], 'Enable sound effects');
    assert.equal(page.toggle.attrs['aria-pressed'], 'true');
    assert.equal(JSON.parse(page.local['city-guesser-sound-v1']).volume, 1);
    assert.equal(visit({local: page.local}).engine.muted, true);
    const silent = visit({noLibrary: true, events: result});
    assert.equal(silent.toggle.hidden, true);
});
test('submit emits once, disabled controls are silent, clicks are throttled', async () => {
    const page = visit();
    const submit = {type: 'submit', form: {}, matches: () => true, getAttribute: () => null};
    page.handlers.click({target: {closest: () => submit}});
    assert.equal(page.played.length, 0);
    page.handlers.submit();
    page.handlers.submit();
    await settle();
    assert.equal(page.played.length, 1);
    submit.disabled = true;
    page.handlers.click({target: {closest: () => submit}});
    assert.equal(page.played.length, 1);
});
test('play errors drop remaining rewards and unknown events are ignored', async () => {
    const page = visit({playError: true});
    page.api.emit('outcome', [{type: 'unknown', id: 'x'}, ...result]);
    await settle();
    assert.deepEqual(page.played, ['/static/audio/correct.wav']);
});
test('hidden page does not play rewards', async () => {
    const page = visit();
    page.document.hidden = true;
    page.handlers.visibilitychange();
    page.api.emit('outcome', result);
    await settle();
    assert.equal(page.played.length, 0);
});

test('volume slider uses 30% default, persists adjustments, and preserves mute', () => {
    const page = visit();
    assert.equal(page.engine.level, .3);
    assert.equal(page.volumeValue.textContent, '30%');
    page.api.setMuted(true);
    page.volumeInput.value = '15';
    page.volumeInput.input();
    assert.equal(page.engine.level, .15);
    assert.equal(page.engine.muted, true);
    assert.equal(page.volumeInput.attrs['aria-valuetext'], '15%');
    assert.equal(visit({local: page.local}).volumeValue.textContent, '15%');
    page.api.setVolume(0);
    assert.equal(page.volumeValue.textContent, '0%');
    page.api.setVolume(1);
    assert.equal(page.volumeValue.textContent, '100%');
});
test('other-tab updates and preference removal refresh volume display', () => {
    const page = visit();
    page.handlers.storage({key: 'city-guesser-sound-v1', newValue: '{"volume":0.2,"muted":true}'});
    assert.equal(page.volumeValue.textContent, '20%');
    assert.equal(page.engine.level, .2);
    assert.equal(page.toggle.attrs['aria-label'], 'Enable sound effects');
    page.handlers.storage({key: 'city-guesser-sound-v1', newValue: null});
    assert.equal(page.engine.level, .3);
    assert.equal(page.volumeValue.textContent, '30%');
    assert.equal(visit({noLibrary:true}).controls.hidden, true);
});
