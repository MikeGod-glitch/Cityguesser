const guessForm = document.querySelector('.guess-form');

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
