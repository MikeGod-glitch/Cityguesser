const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const source = readFileSync(join(__dirname, '../static/records.js'), 'utf8');

function visit(store, result = null, options = {}) {
    const feedback = {textContent: ''};
    const storageStatus = {append(message) { this.textContent = message; }, textContent: ''};
    const form = {inputs: [], appendChild(input) { this.inputs.push(input); }, querySelector() {return {value:'daily'};},
        addEventListener(name, handler) { this.submit = handler; }};
    const cards = ['challenge:text', 'challenge:choice']
        .map(bucket => ({dataset: {record: bucket}, textContent: ''}));
    const elements = {'#completion-data': result && {textContent: JSON.stringify(result)},
        '[data-record-feedback]': feedback, '[data-storage-status]': storageStatus,
        '#daily-runs-data': options.runs && {textContent:JSON.stringify(options.runs)}};
    const details = {textContent:''};
    const resultView = {hidden:true, querySelector() {return details;}, focus() {this.focused = true;}, scrollIntoView() {}};
    const button = {textContent:'Start exploring', disabled:false};
    if (options.home) {
        elements['[data-daily-result-view]'] = resultView;
        elements['.start-form'] = {
            querySelector(selector) {return selector === '[data-start-button]' ? button : {value:'daily'};},
            querySelectorAll() {return [];}, addEventListener() {},
        };
    }
    vm.runInNewContext(source, {
        document: {
            body: {dataset: {dailyDate: '2026-10-04', answerMode: 'text'}, appendChild(form) {store.restored = form;}},
            querySelector: selector => elements[selector] || null,
            querySelectorAll: selector => selector === '[data-record]' ? cards : [form],
            createElement: () => ({inputs:[], appendChild(input) {this.inputs.push(input);}, submit() {store.submitted = true;}}),
        },
        localStorage: {getItem: () => store.value || null, setItem(key, value) {
            if (options.unavailable) throw new Error('Storage blocked');
            store.value = value;
        }},
    });
    return {feedback, storageStatus, cards, form, button, resultView, details, saved: store.value && JSON.parse(store.value)};
}
const score = (overrides = {}) => ({mode: 'challenge', answer_mode: 'text', score: 1000,
    correct: 8, answered: 10, unassisted: 6, assisted: 2, date: '2026-10-04',
    official: true, run_id: 'first', ...overrides});

test('best survives reloads and a lower score does not replace it', () => {
    const store = {};
    visit(store, score());
    visit(store, score({score: 900, run_id: 'second'}));
    const home = visit(store);
    assert.equal(home.saved.best['challenge:text'].run_id, 'first');
    assert.match(home.cards[0].textContent, /1000 points/);
});
test('equal scores prefer independent answers and answer modes stay separate', () => {
    const store = {};
    visit(store, score());
    visit(store, score({unassisted: 8, assisted: 0, run_id: 'independent'}));
    const result = visit(store, score({answer_mode: 'choice', score: 1800, run_id: 'choice'}));
    assert.equal(result.saved.best['challenge:text'].run_id, 'independent');
    assert.equal(result.saved.best['challenge:choice'].score, 1800);
});
test('daily result is immutable across answer modes and never creates a best', () => {
    const store = {};
    visit(store, score({mode: 'daily', score: 500}));
    const replay = visit(store, score({mode: 'daily', answer_mode:'choice', score: 1800, run_id: 'replay'}));
    assert.equal(replay.saved.daily['2026-10-04'].score, 500);
    assert.equal(replay.saved.best['daily:text'], undefined);
    assert.equal(replay.saved.best['daily:choice'], undefined);
    assert.doesNotMatch(replay.feedback.textContent, /personal best/i);
});
test('revisiting a daily completion is idempotent and shows only its result', () => {
    const store = {};
    visit(store, score({mode: 'daily'}));
    const original = store.value;
    const refresh = visit(store, score({mode: 'daily'}));
    assert.equal(store.value, original);
    assert.match(refresh.feedback.textContent, /Daily challenge complete/);
});
test('each day has one result regardless of answer mode', () => {
    const store = {};
    visit(store, score({mode: 'daily'}));
    visit(store, score({mode: 'daily', answer_mode: 'choice', run_id: 'choice'}));
    const next = visit(store, score({mode: 'daily', date: '2026-10-05', run_id: 'tomorrow', score: 1200}));
    assert.equal(Object.keys(next.saved.daily).length, 2);
    assert.equal(next.saved.daily['2026-10-04'].run_id, 'first');
    assert.equal(next.saved.daily['2026-10-05'].score, 1200);
    assert.deepEqual(next.saved.best, {});
});
test('signed progress is persisted and sent on daily-entry forms after session loss', () => {
    const store = {};
    const run = {date:'2026-10-04', answer_mode:'choice', answered:3, position:7, token:'signed-progress', run_id:'only-run'};
    visit(store, null, {runs:[run]});
    const home = visit(store);
    home.form.submit({submitter:{name:'mode', value:'daily', dataset:{}}});
    assert.equal(home.form.inputs.find(input => input.name === 'daily_token').value, 'signed-progress');
    assert.equal(home.form.inputs.find(input => input.name === 'daily_date').value, '2026-10-04');
    assert.equal(home.form.inputs.find(input => input.name === 'answer_mode').value, 'choice');
});

test('stale active server progress is restored from the newer browser snapshot', () => {
    const store = {};
    const run = {date:'2026-10-04', answer_mode:'text', answered:4, position:9, token:'newer', run_id:'only-run'};
    visit(store, null, {runs:[run]});
    visit(store, null, {runs:[{...run, answered:1, position:2, active:true, token:'older'}]});
    assert.equal(store.submitted, true);
    assert.equal(store.restored.inputs.find(input => input.name === 'daily_token').value, 'newer');
});

test('old daily bests are removed while an existing daily result is preserved', () => {
    const old = score({mode:'daily'});
    const store = {value:JSON.stringify({best:{'daily:text':old}, daily:{'2026-10-04':{text:old}}})};
    const migrated = visit(store);
    assert.deepEqual(migrated.saved.best, {});
    assert.equal(migrated.saved.daily['2026-10-04'].run_id, 'first');
});
test('storage failure reports that the result was not saved', () => {
    const result = visit({}, score(), {unavailable: true});
    assert.match(result.storageStatus.textContent, /could not be saved/);
});

test('legacy completed result without a progress token can be viewed without starting a game', () => {
    const old = score({mode:'daily', score:500});
    const store = {value:JSON.stringify({daily:{'2026-10-04':{text:old}}})};
    const home = visit(store, null, {home:true});
    assert.equal(home.button.textContent, "View today's result");
    assert.equal(home.button.disabled, false);
    let prevented = false;
    home.form.submit({submitter:{dataset:{}}, preventDefault() {prevented = true;}});
    assert.equal(prevented, true);
    assert.equal(home.resultView.hidden, false);
    assert.equal(home.resultView.focused, true);
    assert.match(home.details.textContent, /500 points/);
    assert.equal(home.form.inputs.length, 0);
    assert.equal(home.saved.runs['2026-10-04'], undefined);
});

test('completed signed run also views its stored result without restoring the game', () => {
    const store = {};
    const run = {date:'2026-10-04', answer_mode:'text', answered:10, position:20, token:'completed', run_id:'first', complete:true};
    visit(store, score({mode:'daily'}), {runs:[run]});
    const home = visit(store, null, {home:true});
    let prevented = false;
    home.form.submit({submitter:{dataset:{}}, preventDefault() {prevented = true;}});
    assert.equal(prevented, true);
    assert.match(home.details.textContent, /1000 points/);
    assert.equal(home.form.inputs.length, 0);
    assert.equal(home.saved.runs['2026-10-04'].token, 'completed');
});
