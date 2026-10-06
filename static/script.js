const photoFeedbackForm = document.querySelector('[data-photo-feedback]');
if (photoFeedbackForm) {
    const entry = document.querySelector('[data-photo-feedback-entry]');
    const dialog = document.querySelector('[data-feedback-dialog]');
    const trigger = entry.querySelector('[data-feedback-open]');
    const status = entry.querySelector('[data-feedback-status]');
    const description = photoFeedbackForm.querySelector('[data-feedback-description]');
    const reasons = photoFeedbackForm.querySelector('[data-feedback-reasons]');
    const yes = photoFeedbackForm.querySelector('[data-feedback-yes]');
    const cancel = photoFeedbackForm.querySelector('[data-feedback-cancel]');
    const submit = photoFeedbackForm.querySelector('[data-feedback-submit]');
    const dialogStatus = photoFeedbackForm.querySelector('[data-feedback-dialog-status]');
    let confirmed = false;
    let sending = false;
    let sent = false;
    trigger.addEventListener('click', () => {
        if (sending || sent) return;
        photoFeedbackForm.reset();
        confirmed = false;
        reasons.hidden = true;
        reasons.disabled = true;
        yes.hidden = false;
        submit.hidden = true;
        submit.disabled = true;
        description.textContent = 'Does this photo lack city clues?';
        dialogStatus.textContent = '';
        dialog.showModal();
    });
    yes.addEventListener('click', () => {
        confirmed = true;
        reasons.hidden = false;
        reasons.disabled = false;
        yes.hidden = true;
        submit.hidden = false;
        description.textContent = 'Why does this photo lack city clues?';
        reasons.querySelector('input').focus();
    });
    photoFeedbackForm.addEventListener('change', () => {
        submit.disabled = sending || !photoFeedbackForm.elements.reason.value;
    });
    cancel.addEventListener('click', () => { if (!sending) dialog.close(); });
    dialog.addEventListener('cancel', event => { if (sending) event.preventDefault(); });
    photoFeedbackForm.addEventListener('submit', async event => {
        event.preventDefault();
        const reason = photoFeedbackForm.elements.reason.value;
        if (!confirmed || !reason || sending || sent) return;
        sending = true;
        submit.disabled = true;
        cancel.disabled = true;
        reasons.disabled = true;
        dialogStatus.textContent = 'Sending feedback…';
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 10000);
        let failureMessage = 'Could not send feedback. Please try again.';
        try {
            const response = await fetch(photoFeedbackForm.action, {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: {'Accept': 'application/json'}, signal: controller.signal,
                body: new URLSearchParams({question_id: photoFeedbackForm.elements.question_id.value, reason}),
            });
            const data = await response.json();
            if (!response.ok || data.ok !== true) {
                if (typeof data.message === 'string') failureMessage = data.message;
                throw new Error(failureMessage);
            }
            sent = true;
            dialog.close();
            trigger.disabled = true;
            trigger.setAttribute('aria-label', 'Feedback recorded for this photo');
            status.textContent = 'Feedback recorded. Thank you.';
        } catch (_) {
            dialogStatus.textContent = failureMessage;
        } finally {
            sending = false;
            reasons.disabled = false;
            cancel.disabled = false;
            submit.disabled = sent;
            clearTimeout(timeout);
        }
    });
    // Native dialog provides keyboard focus management and Escape cancellation.
    // Without dialog support, hide the entry rather than navigate to a JSON reply.
    if (typeof dialog.showModal === 'function') entry.hidden = false;
}

const themeToggle = document.querySelector('.theme-toggle');

function createPhotoViewer(photo, viewport) {
    const resetButton = viewport.querySelector('.photo-reset');
    if (!resetButton) return null;
    let scale = 1;
    let x = 0;
    let y = 0;
    let drag = null;
    const ready = () => photo.complete && photo.naturalWidth > 0 && !photo.hidden;
    function draw() {
        const width = viewport.clientWidth;
        const height = viewport.clientHeight;
        if (ready() && width && height) {
            const fit = Math.min(width / photo.naturalWidth, height / photo.naturalHeight);
            const limitX = Math.max(0, (photo.naturalWidth * fit * scale - width) / 2);
            const limitY = Math.max(0, (photo.naturalHeight * fit * scale - height) / 2);
            x = Math.max(-limitX, Math.min(limitX, x));
            y = Math.max(-limitY, Math.min(limitY, y));
        } else { x = 0; y = 0; }
        photo.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
        viewport.dataset.photoZoomed = String(scale > 1);
        resetButton.hidden = !ready() || scale === 1;
    }
    function stopDrag() {
        if (!drag) return;
        const pointerId = drag.id;
        drag = null;
        viewport.dataset.photoDragging = 'false';
        if (viewport.hasPointerCapture(pointerId)) viewport.releasePointerCapture(pointerId);
    }
    function reset() {
        stopDrag();
        scale = 1;
        x = y = 0;
        draw();
    }
    const isControl = event => event.target.closest('button, a, input');
    viewport.addEventListener('wheel', event => {
        if (!ready() || isControl(event) || event.ctrlKey || !event.deltaY) return;
        event.preventDefault();
        stopDrag();
        const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1;
        const delta = Math.max(-240, Math.min(240, event.deltaY * unit));
        let nextScale = Math.max(1, Math.min(4, scale * Math.exp(-delta * .002)));
        if (nextScale < 1.01) nextScale = 1;
        const bounds = viewport.getBoundingClientRect();
        const anchorX = event.clientX - bounds.left - viewport.clientWidth / 2;
        const anchorY = event.clientY - bounds.top - viewport.clientHeight / 2;
        const ratio = nextScale / scale;
        x = anchorX - (anchorX - x) * ratio;
        y = anchorY - (anchorY - y) * ratio;
        scale = nextScale;
        draw();
    }, {passive: false});
    viewport.addEventListener('pointerdown', event => {
        if (!ready() || scale === 1 || event.button !== 0 || isControl(event)
                || (event.pointerType && event.pointerType !== 'mouse') || drag) return;
        event.preventDefault();
        drag = {id: event.pointerId, x: event.clientX, y: event.clientY};
        viewport.setPointerCapture(event.pointerId);
        viewport.dataset.photoDragging = 'true';
    });
    viewport.addEventListener('pointermove', event => {
        if (!drag || event.pointerId !== drag.id) return;
        if (!(event.buttons & 1)) { stopDrag(); return; }
        event.preventDefault();
        x += event.clientX - drag.x;
        y += event.clientY - drag.y;
        drag.x = event.clientX;
        drag.y = event.clientY;
        draw();
    });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
        viewport.addEventListener(name, event => {
            if (drag && event.pointerId === drag.id) stopDrag();
        });
    }
    viewport.addEventListener('dblclick', event => {
        if (ready() && !isControl(event)) { event.preventDefault(); reset(); }
    });
    photo.addEventListener('dragstart', event => event.preventDefault());
    resetButton.addEventListener('click', reset);
    window.addEventListener('resize', draw);
    window.addEventListener('blur', stopDrag);
    window.addEventListener('pagehide', stopDrag);
    reset();
    return {reset};
}

// Warm only the existing next question, after the current clue has loaded.
const cityPhoto = document.querySelector('.city-photo');
if (cityPhoto) {
    const frame = cityPhoto.parentElement;
    const retry = frame.querySelector('.photo-retry');
    const error = cityPhoto.nextElementSibling;
    const viewer = createPhotoViewer(cityPhoto, frame);
    const retrySource = cityPhoto.dataset.remoteSrc || cityPhoto.getAttribute('src');
    let prefetchStarted = false;
    let stopped = false;
    let timer;
    let controller;
    let warmedPhoto;
    let measured = false;
    const updatePhoto = () => {
        const failed = cityPhoto.complete && cityPhoto.naturalWidth === 0;
        frame.dataset.photoState = failed ? 'error' : cityPhoto.complete ? 'ready' : 'loading';
        retry.hidden = !failed;
        if (!measured && cityPhoto.complete && cityPhoto.naturalWidth > 0) {
            measured = true;
            const perf = window.performance;
            const navigation = perf?.getEntriesByType('navigation')[0];
            if (navigation && perf.measure) {
                perf.measure('city-page-response', {start: 0, end: navigation.responseStart});
                perf.measure('city-photo-visible', {start: navigation.responseStart, end: perf.now()});
            }
        }
    };
    function warm(url) {
        if (stopped) return;
        warmedPhoto = new Image();
        warmedPhoto.fetchPriority = 'low';
        warmedPhoto.decoding = 'async';
        warmedPhoto.src = url;
    }
    async function poll(attempt = 0) {
        if (stopped || attempt >= 12) return;
        if (document.visibilityState === 'hidden') {
            timer = setTimeout(() => poll(attempt + 1), 2500);
            return;
        }
        const activeController = new AbortController();
        controller = activeController;
        const timeout = setTimeout(() => activeController.abort(), 3000);
        try {
            const response = await fetch(cityPhoto.dataset.prefetchEndpoint, {
                cache: 'no-store', signal: activeController.signal,
            });
            if (response.ok) {
                const data = await response.json();
                if (data.image_url) { warm(data.image_url); return; }
            }
        } catch (_) { /* Prewarming must never interrupt gameplay. */ }
        finally { clearTimeout(timeout); }
        if (!stopped) timer = setTimeout(() => poll(attempt + 1), 2500);
    }
    function startPrefetch() {
        if (prefetchStarted || stopped || cityPhoto.naturalWidth === 0) return;
        prefetchStarted = true;
        if (cityPhoto.dataset.nextImage) warm(cityPhoto.dataset.nextImage);
        else if (cityPhoto.dataset.prefetchEndpoint) poll();
    }
    cityPhoto.addEventListener('load', () => { viewer?.reset(); updatePhoto(); startPrefetch(); });
    cityPhoto.addEventListener('error', () => {
        viewer?.reset();
        // The inline handler may already have started the remote fallback.
        if (cityPhoto.hidden) { frame.dataset.photoState = 'error'; retry.hidden = false; }
    });
    retry.addEventListener('click', () => {
        cityPhoto.hidden = false;
        error.hidden = true;
        retry.hidden = true;
        frame.dataset.photoState = 'loading';
        viewer?.reset();
        cityPhoto.src = retrySource;
    });
    updatePhoto();
    if (cityPhoto.complete && cityPhoto.naturalWidth > 0) startPrefetch();
    window.addEventListener('pagehide', () => {
        stopped = true;
        clearTimeout(timer);
        controller?.abort();
    });
    window.addEventListener('pageshow', event => {
        if (event.persisted) { stopped = false; prefetchStarted = false; startPrefetch(); }
    });
}

function updateThemeButton() {
    const isLight = document.documentElement.dataset.theme === 'light';
    themeToggle.querySelector('.theme-icon').textContent = isLight ? '☾' : '☀';
    themeToggle.querySelector('.theme-label').textContent = isLight ? 'Dark' : 'Light';
    themeToggle.setAttribute('aria-label', `Switch to ${isLight ? 'dark' : 'light'} mode`);
    document.querySelector('meta[name="theme-color"]').content = isLight ? '#ffffff' : '#0b1720';
}

if (themeToggle) {
    updateThemeButton();
    themeToggle.addEventListener('click', () => {
        const theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
        document.documentElement.dataset.theme = theme;
        try { localStorage.setItem('city-guesser-theme', theme); } catch (_) { /* Keep switching available. */ }
        updateThemeButton();
    });
    window.addEventListener('pageshow', updateThemeButton);
}

const guessForm = document.querySelector('.guess-form');
const cityGuess = document.querySelector('#city-guess');
const cityFlagLookup = document.querySelector('#city-flag-lookup');
const hintTongue = document.querySelector('.hint-tongue');
if (hintTongue) {
    const isDaily = document.body.dataset.gameMode === 'daily';
    const storage = () => isDaily ? localStorage : sessionStorage;
    const key = isDaily ? `city-guesser-hint:${document.body.dataset.dailyDate}` : 'city-guesser-hint';
    const question = hintTongue.dataset.questionId;
    const field = hintTongue.querySelector('input[name="hint_level"]');
    const button = hintTongue.querySelector('.hint-toggle');
    const copy = hintTongue.querySelector('.hint-copy');
    let level = Number(field.value) || 0;
    let expanded = level > 0;
    try {
        const saved = JSON.parse(storage().getItem(key));
        if (saved?.question === question) {
            level = Math.max(level, Math.min(2, Math.max(0, Number(saved.level) || 0)));
            expanded = level > 0 && saved.expanded !== false;
            if (typeof saved.draft === 'string') cityGuess.value = saved.draft;
        }
    } catch (_) { /* Hints still work without browser storage. */ }
    function updateHints() {
        field.value = level;
        copy.hidden = !expanded;
        hintTongue.querySelector('[data-hint="2"]').hidden = level < 2;
        hintTongue.dataset.expanded = String(expanded);
        button.setAttribute('aria-expanded', String(expanded));
        button.setAttribute('aria-label', !expanded ? 'Show hints' : level < 2 ? 'Show country or region hint' : 'Hide hints');
        button.querySelector('.hint-label').textContent = !expanded ? 'Hint' : level < 2 ? 'More' : 'Hide';
        button.querySelector('.hint-arrow').textContent = expanded && level >= 2 ? '‹' : '›';
    }
    function saveHints() {
        try { storage().setItem(key, JSON.stringify({question, level, expanded, draft: cityGuess.value})); }
        catch (_) { /* The submitted hidden field still records hint use. */ }
    }
    button.addEventListener('click', () => {
        if (!expanded) { level = Math.max(1, level); expanded = true; }
        else if (level < 2) level++;
        else expanded = false;
        updateHints();
        saveHints();
    });
    cityGuess.addEventListener('input', saveHints);
    updateHints();
}

if (cityGuess && cityFlagLookup) {
    const cities = new Map(Object.entries(JSON.parse(cityFlagLookup.textContent)));
    const flag = document.querySelector('.input-country-flag');
    const label = document.querySelector('.input-country-label');

    function updateCityFlag() {
        // Match case-folded catalog names and aliases, without partial matches.
        const name = cityGuess.value.trim().toLowerCase().replace(/ß/g, 'ss').replace(/ς/g, 'σ');
        const city = cities.get(name);
        if (city) {
            flag.src = city.flag_url;
            flag.alt = `${city.country} flag`;
            flag.hidden = false;
            label.textContent = `Recognized city: ${city.name}, ${city.country}`;
        } else {
            flag.hidden = true;
            flag.removeAttribute('src');
            flag.alt = '';
            label.textContent = '';
        }
    }

    cityGuess.addEventListener('input', updateCityFlag);
    window.addEventListener('pageshow', updateCityFlag);
    updateCityFlag();
}

if (guessForm) {
    const submitButtons = [...guessForm.querySelectorAll('button[type="submit"]')];
    const defaultLabels = new Map(submitButtons.map(button => [button, button.innerHTML]));

    guessForm.addEventListener('submit', event => {
        const submitButton = event.submitter || submitButtons[0];
        // Disabled submit buttons are omitted from the form payload.
        if (submitButton.name) {
            let selected = guessForm.querySelector('input[data-selected-choice]');
            if (!selected) {
                selected = document.createElement('input');
                selected.type = 'hidden';
                selected.dataset.selectedChoice = 'true';
                guessForm.appendChild(selected);
            }
            selected.name = submitButton.name;
            selected.value = submitButton.value;
        }
        submitButtons.forEach(button => { button.disabled = true; });
        submitButton.textContent = submitButton.classList.contains('reveal-button')
            ? 'Loading answer…'
            : 'Checking your guess…';
    });

    window.addEventListener('pageshow', () => {
        guessForm.querySelector('input[data-selected-choice]')?.remove();
        submitButtons.forEach(button => {
            button.disabled = false;
            button.innerHTML = defaultLabels.get(button);
        });
    });
}
