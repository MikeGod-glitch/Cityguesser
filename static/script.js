const guessForm = document.querySelector('.guess-form');

if (guessForm) {
    const submitButton = guessForm.querySelector('button[type="submit"]');
    const defaultLabel = submitButton.innerHTML;

    guessForm.addEventListener('submit', () => {
        submitButton.disabled = true;
        submitButton.textContent = 'Checking your guess…';
    });

    window.addEventListener('pageshow', () => {
        submitButton.disabled = false;
        submitButton.innerHTML = defaultLabel;
    });
}
