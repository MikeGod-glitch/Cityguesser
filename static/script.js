const themeToggle = document.querySelector('.theme-toggle');

function updateThemeButton() {
    const isLight = document.documentElement.dataset.theme === 'light';
    themeToggle.querySelector('.theme-icon').textContent = isLight ? '☾' : '☀';
    themeToggle.querySelector('.theme-label').textContent = isLight ? 'Dark' : 'Light';
    themeToggle.setAttribute('aria-label', `Switch to ${isLight ? 'dark' : 'light'} mode`);
    document.querySelector('meta[name="theme-color"]').content = isLight ? '#f6f5f0' : '#0b1720';
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
