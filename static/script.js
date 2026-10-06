const themeToggle = document.querySelector('.theme-toggle');

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
