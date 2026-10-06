const themeToggle = document.querySelector('.theme-toggle');

// Warm only the existing next question, after the current clue has loaded.
const cityPhoto = document.querySelector('.city-photo');
if (cityPhoto) {
    const frame = cityPhoto.parentElement;
    const retry = frame.querySelector('.photo-retry');
    const error = cityPhoto.nextElementSibling;
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
    cityPhoto.addEventListener('load', () => { updatePhoto(); startPrefetch(); });
    cityPhoto.addEventListener('error', () => {
        // The inline handler may already have started the remote fallback.
        if (cityPhoto.hidden) { frame.dataset.photoState = 'error'; retry.hidden = false; }
    });
    retry.addEventListener('click', () => {
        cityPhoto.hidden = false;
        error.hidden = true;
        retry.hidden = true;
        frame.dataset.photoState = 'loading';
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
