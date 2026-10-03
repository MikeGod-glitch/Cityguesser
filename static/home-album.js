(() => {
    const album = document.querySelector('#home-album');
    if (!album) return;

    const cards = [...album.querySelectorAll('.album-card')];
    const description = document.querySelector('#album-description');
    const source = document.querySelector('#album-source');
    const counter = document.querySelector('#album-counter');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let current = 0;
    let touchStart = null;

    function turn(step) {
        current = (current + step + cards.length) % cards.length;
        cards.forEach((card, index) => {
            const depth = (index - current + cards.length) % cards.length;
            card.dataset.depth = depth;
            card.setAttribute('aria-hidden', String(depth !== 0));
        });
        const photo = cards[current].dataset;
        const city = document.createElement('strong');
        city.textContent = photo.city;
        description.replaceChildren(`${photo.clues}—the photo on the right was taken in `,
            city, `. ${photo.question}`);
        source.href = photo.source;
        source.textContent = `Photo by ${photo.author} · Unsplash ↗`;
        counter.textContent = `${current + 1} / ${cards.length}`;
        if (!reducedMotion.matches) {
            description.getAnimations().forEach(animation => animation.cancel());
            description.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 250 });
            cards[current].getAnimations().forEach(animation => animation.cancel());
            cards[current].animate([
                { opacity: .5, transform: `translateX(${step > 0 ? 24 : -24}px) rotate(${step > 0 ? 2 : -2}deg)` },
                { opacity: 1, transform: 'translateX(0) rotate(0)' }
            ], { duration: 320, easing: 'ease-out' });
        }
    }

    album.querySelector('.album-controls').hidden = false;
    album.querySelectorAll('[data-album-step]').forEach(button => {
        button.addEventListener('click', () => turn(Number(button.dataset.albumStep)));
    });
    album.addEventListener('keydown', event => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault();
            turn(event.key === 'ArrowRight' ? 1 : -1);
        }
    });
    const stack = album.querySelector('.travel-photo-stack');
    stack.addEventListener('touchstart', event => {
        touchStart = event.touches.length === 1
            ? { x: event.touches[0].clientX, y: event.touches[0].clientY } : null;
    }, { passive: true });
    stack.addEventListener('touchend', event => {
        if (!touchStart) return;
        const dx = event.changedTouches[0].clientX - touchStart.x;
        const dy = event.changedTouches[0].clientY - touchStart.y;
        touchStart = null;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) turn(dx < 0 ? 1 : -1);
    }, { passive: true });
    stack.addEventListener('touchcancel', () => { touchStart = null; }, { passive: true });
})();
