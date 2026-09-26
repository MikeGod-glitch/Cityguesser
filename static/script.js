const guessForm = document.querySelector('.guess-form');

if (guessForm) {
    const submitButtons = [...guessForm.querySelectorAll('button[type="submit"]')];
    const defaultLabels = new Map(submitButtons.map(button => [button, button.innerHTML]));

    guessForm.addEventListener('submit', event => {
        const submitButton = event.submitter || submitButtons[0];
        submitButton.disabled = true;
        submitButton.textContent = submitButton.classList.contains('reveal-button')
            ? 'Loading answer…'
            : 'Checking your guess…';
    });

    window.addEventListener('pageshow', () => {
        submitButtons.forEach(button => {
            button.disabled = false;
            button.innerHTML = defaultLabels.get(button);
        });
    });
}
