// Ordinary bests and one resumable daily run per date, stored in this browser.
(() => {
    const key = 'city-guesser-records-v1';
    const date = document.body.dataset.dailyDate;
    function loadRecords() {
        let records;
        try {
            records = JSON.parse(localStorage.getItem(key) || '{}');
            if (!records || typeof records !== 'object') throw new Error('Invalid records');
        } catch (_) { records = {}; }
        records.best ||= {};
        records.daily ||= {};
        records.runs ||= {};
        delete records.best['daily:text'];
        delete records.best['daily:choice'];
        // Preserve old daily scores while removing the old per-mode replay policy.
        for (const [day, value] of Object.entries(records.daily)) {
            if (value && !('answered' in value)) records.daily[day] = value.text || value.choice;
        }
        return records;
    }
    const records = loadRecords();
    const save = () => {
        try { localStorage.setItem(key, JSON.stringify(records)); }
        catch (_) { document.querySelector('[data-storage-status]')?.append('Browser storage is unavailable. Progress and results could not be saved.'); }
    };
    const describe = entry => `${entry.score} points · ${entry.correct}/${entry.answered} correct · ${entry.unassisted} without hints · ${entry.assisted} with hints · ${entry.date}`;
    const modeName = mode => mode === 'choice' ? 'Multiple choice' : 'Type answer';
    const addField = (form, name, value) => {
        const input = document.createElement('input');
        input.type = 'hidden'; input.name = name; input.value = value;
        form.appendChild(input);
    };
    const resume = run => {
        const form = document.createElement('form');
        form.action = '/reset'; form.method = 'post';
        for (const [name, value] of Object.entries({mode:'daily', daily_date:run.date, daily_token:run.token})) addField(form, name, value);
        document.body.appendChild(form);
        form.submit();
    };
    function restoreServerRuns() {
        const runsData = document.querySelector('#daily-runs-data');
        for (const incoming of runsData ? JSON.parse(runsData.textContent) : []) {
            const saved = records.runs[incoming.date];
            if (incoming.active && saved?.token && (saved.position > incoming.position || saved.run_id !== incoming.run_id)) {
                resume(saved);
                return true;
            }
            if (incoming.active && records.daily[incoming.date] && !incoming.complete && !saved?.token) {
                window.location.assign('/');
                return true;
            }
            if (!saved || incoming.position >= saved.position) records.runs[incoming.date] = incoming;
        }
        return false;
    }
    function saveCompletion() {
        const completion = document.querySelector('#completion-data');
        if (!completion) return;
        const result = JSON.parse(completion.textContent);
        const feedback = document.querySelector('[data-record-feedback]');
        if (result.mode === 'daily') {
            records.daily[result.date] ||= result;
            if (feedback) feedback.textContent = 'Daily challenge complete. Your result: ' + describe(records.daily[result.date]);
            return;
        }
        const bucket = `challenge:${result.answer_mode}`;
        const previous = records.best[bucket];
        const isBetter = !previous || result.score > previous.score
            || (result.score === previous.score && result.unassisted > previous.unassisted);
        if (isBetter) records.best[bucket] = result;
        if (feedback) feedback.textContent = records.best[bucket].run_id === result.run_id
            ? 'New personal best! ' + describe(result) : 'Personal best: ' + describe(records.best[bucket]);
    }
    function renderOrdinaryBests() {
        document.querySelectorAll('[data-record]').forEach(element => {
            const entry = records.best[element.dataset.record];
            if (entry) element.textContent = describe(entry);
        });
    }
    function renderDailyStatus() {
        if (!status) return;
        if (dailyResult) {
            status.textContent = `Today's daily result · ${modeName(dailyResult.answer_mode)}: ${describe(dailyResult)}`;
        } else if (daily) {
            status.textContent = `Today's daily challenge · ${modeName(daily.answer_mode)} · ${daily.answered}/10 answered. Your answer mode is locked.`;
        } else {
            status.textContent = 'One daily attempt available today. Choose your answer mode before starting.';
        }
    }
    function renderPendingRuns() {
        const pending = document.querySelector('[data-daily-pending]');
        if (pending) {
            for (const run of Object.values(records.runs).filter(run => run.date < date && !run.complete && run.token)) {
                const form = document.createElement('form');
                form.action = '/reset'; form.method = 'post';
                const button = document.createElement('button');
                button.type = 'submit'; button.name = 'mode'; button.value = 'daily';
                button.dataset.dailyDate = run.date; button.className = 'game-mode-button';
                button.textContent = `Continue ${run.date} · ${run.answered}/10 answered`;
                form.appendChild(button); pending.appendChild(form);
            }
        }
    }
    function configureStartForm() {
        const startForm = document.querySelector('.start-form');
        if (startForm) {
            const button = startForm.querySelector('[data-start-button]');
            const ordinaryLabel = button.textContent;
            const update = () => {
                const selected = startForm.querySelector('input[name="mode"]:checked').value === 'daily';
                let label = ordinaryLabel;
                if (selected) {
                    if (dailyResult || daily?.complete) label = "View today's result";
                    else if (daily) label = "Continue today's challenge";
                    else label = 'Start daily challenge →';
                }
                button.textContent = label;
                startForm.querySelectorAll('input[name="answer_mode"]').forEach(input => {
                    const locked = daily?.answer_mode || dailyResult?.answer_mode;
                    input.disabled = selected && Boolean(locked) && input.value !== locked;
                    if (selected && locked) input.checked = input.value === locked;
                });
            };
            startForm.addEventListener('change', update);
            update();
        }
    }
    function showDailyResult(event) {
        event.preventDefault();
        status.focus({preventScroll:true});
        const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth';
        status.scrollIntoView({block:'center', behavior});
        clearTimeout(highlightTimer);
        status.classList.remove('daily-result-highlight');
        void status.offsetWidth; // Restart the effect on repeated clicks.
        status.classList.add('daily-result-highlight');
        highlightTimer = setTimeout(() => status.classList.remove('daily-result-highlight'), 2000);
    }

    function handleDailyEntry(form, event) {
        const mode = event.submitter?.name === 'mode' ? event.submitter.value
            : form.querySelector('input[name="mode"]:checked')?.value || form.querySelector('input[name="mode"]')?.value;
        if (mode !== 'daily') return;
        const day = event.submitter?.dataset.dailyDate || date;
        if (records.daily[day] && status) {
            showDailyResult(event);
            return;
        }
        const run = records.runs[day];
        addField(form, 'daily_date', day);
        addField(form, 'daily_token', run?.token || '');
        if (run) {
            addField(form, 'answer_mode', run.answer_mode);
        } else {
            records.runs[day] = {date:day, answer_mode:form.querySelector('input[name="answer_mode"]:checked')?.value || 'text', answered:0, position:-1, complete:false};
            save();
        }
    }

    function bindDailyForms() {
        document.querySelectorAll('form[action="/reset"]').forEach(form => {
            form.addEventListener('submit', event => handleDailyEntry(form, event));
        });
    }

    if (restoreServerRuns()) return;
    saveCompletion();
    save();
    renderOrdinaryBests();
    const daily = records.runs[date];
    const dailyResult = records.daily[date];
    const status = document.querySelector('[data-daily-status]');
    let highlightTimer;
    renderDailyStatus();
    renderPendingRuns();
    configureStartForm();
    bindDailyForms();
})();
