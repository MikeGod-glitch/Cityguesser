// Only this module knows about Howler. Gameplay publishes semantic events.
(() => {
    const manifest = Object.freeze({
        buttonClick: {src: ['/static/audio/button-click.wav'], volume: .28, cooldown: 80},
        correct: {src: ['/static/audio/correct.wav'], volume: .38},
        wrong: {src: ['/static/audio/wrong.wav'], volume: .30},
        streak: {src: ['/static/audio/streak.wav'], volume: .38},
        levelComplete: {src: ['/static/audio/level-complete.wav'], volume: .42},
    });
    const preferencesKey = 'city-guesser-sound-v1';
    const seenKey = 'city-guesser-sound-events-v1';
    const GameEvents = new EventTarget();

    class SoundManager {
        constructor() {
            this.muted = false;
            this.volume = .3;
            this.sounds = new Map();
            this.lastPlayed = new Map();
            this.active = new Set();
            this.generation = 0;
            this.gesture = false;
            try {
                const saved = JSON.parse(localStorage.getItem(preferencesKey) || '{}');
                this.muted = saved.muted === true;
                if (Number.isFinite(saved.volume)) this.volume = Math.max(0, Math.min(1, saved.volume));
            } catch (_) { /* Preferences are optional. */ }
            this.available = typeof window.Howl === 'function' && !window.Howler.noAudio;
            if (this.available) {
                window.Howler.mute(this.muted);
                window.Howler.volume(this.volume);
                this.preload();
                // A browser may permit playback on arrival after a form submission.
                window.Howler.ctx?.resume().catch(() => {});
            }
        }
        preload() {
            for (const [name, config] of Object.entries(manifest)) {
                const sound = new window.Howl({src: config.src, volume: config.volume,
                    preload: true, onloaderror: () => { sound.failed = true; },
                    onplayerror: id => sound.stop(id)});
                this.sounds.set(name, sound);
            }
        }
        unlock() {
            this.gesture = true;
            window.Howler?.ctx?.resume().catch(() => {});
        }
        persist() {
            try { localStorage.setItem(preferencesKey, JSON.stringify({muted: this.muted, volume: this.volume})); }
            catch (_) { /* Sound remains usable without storage. */ }
        }
        setMuted(muted) {
            this.muted = Boolean(muted);
            window.Howler?.mute(this.muted);
            if (this.muted) this.stop();
            this.persist();
        }
        setVolume(volume) {
            if (!Number.isFinite(volume)) return;
            this.volume = Math.max(0, Math.min(1, volume));
            window.Howler?.volume(this.volume);
            this.persist();
        }
        stop() {
            this.generation++;
            for (const sound of this.sounds.values()) sound.stop();
            for (const finish of [...this.active]) finish(false);
        }
        async play(name) {
            const generation = this.generation;
            const sound = this.sounds.get(name);
            const config = manifest[name];
            if (!sound || this.muted || sound.failed || document.hidden) return false;
            const now = Date.now();
            if (now - (this.lastPlayed.get(name) || 0) < (config.cooldown || 0)) return false;
            this.lastPlayed.set(name, now);
            // Bound loading waits; never queue old sounds until a later gesture.
            if (sound.state() !== 'loaded') {
                await new Promise(resolve => {
                    const done = () => { clearTimeout(timer); sound.off('load', done); sound.off('loaderror', done); resolve(); };
                    const timer = setTimeout(done, 1200);
                    sound.once('load', done); sound.once('loaderror', done);
                });
            }
            const engine = window.Howler;
            if (generation !== this.generation || this.muted || document.hidden || sound.state() !== 'loaded' ||
                (engine.usingWebAudio ? engine.ctx?.state !== 'running' : !this.gesture)) return false;
            return new Promise(resolve => {
                let id;
                let timer;
                const finish = played => {
                    clearTimeout(timer);
                    sound.off('end', ended, id); sound.off('playerror', failed, id);
                    this.active.delete(finish);
                    if (!played && id !== undefined) sound.stop(id);
                    resolve(played);
                };
                const ended = () => finish(true);
                const failed = () => finish(false);
                try {
                    // Clicks never overlap one another; result sounds are sequenced.
                    if (name === 'buttonClick') sound.stop();
                    id = sound.play();
                    sound.once('end', ended, id); sound.once('playerror', failed, id);
                    this.active.add(finish);
                    timer = setTimeout(failed, Math.max(1500, sound.duration() * 1000 + 500));
                } catch (_) { finish(false); }
            });
        }
    }

    class SoundController {
        constructor(manager) {
            this.manager = manager;
            this.seen = new Set();
            try {
                const saved = JSON.parse(sessionStorage.getItem(seenKey) || '[]');
                if (Array.isArray(saved)) this.seen = new Set(saved.filter(id => typeof id === 'string'));
            } catch (_) { /* Reload protection below still works. */ }
            GameEvents.addEventListener('buttonClick', () => { void manager.play('buttonClick'); });
            GameEvents.addEventListener('outcome', event => { void this.consume(event.detail); });
        }
        async consume(events) {
            if (!Array.isArray(events)) return;
            const fresh = events.filter(event => event && manifest[event.type] && typeof event.id === 'string'
                && !this.seen.has(event.id) && (this.seen.add(event.id), true));
            // Consume even when muted/blocked so these events cannot replay later.
            try { sessionStorage.setItem(seenKey, JSON.stringify([...this.seen].slice(-256))); }
            catch (_) { /* In-memory deduplication remains available. */ }
            for (const event of fresh) {
                if (!await this.manager.play(event.type)) break;
            }
        }
    }

    const manager = new SoundManager();
    new SoundController(manager);
    window.CityGuesserSound = Object.freeze({events: GameEvents,
        emit: (name, detail) => GameEvents.dispatchEvent(new CustomEvent(name, {detail})),
        setMuted: value => { manager.setMuted(value); updateToggle(); },
        setVolume: value => { manager.setVolume(value); updateToggle(); }});
    const toggle = document.querySelector('[data-sound-toggle]');
    const controls = document.querySelector('[data-sound-controls]');
    const volumeInput = document.querySelector('[data-sound-volume]');
    const volumeValue = document.querySelector('[data-sound-volume-value]');
    function updateToggle() {
        if (controls) controls.hidden = !manager.available;
        if (volumeInput) {
            const percent = Math.round(manager.volume * 100);
            volumeInput.value = percent;
            volumeInput.setAttribute('aria-valuetext', `${percent}%`);
            if (volumeValue) volumeValue.textContent = `${percent}%`;
        }
        if (!toggle) return;
        toggle.hidden = !manager.available;
        toggle.setAttribute('aria-pressed', String(manager.muted));
        toggle.setAttribute('aria-label', manager.muted ? 'Enable sound effects' : 'Mute sound effects');
    }
    updateToggle();
    volumeInput?.addEventListener('input', () => {
        manager.setVolume(Number(volumeInput.value) / 100);
        updateToggle();
    });
    document.addEventListener('pointerdown', () => manager.unlock(), {passive: true});
    document.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') manager.unlock(); });
    document.addEventListener('click', event => {
        if (event.isTrusted) manager.unlock();
        const target = event.target.closest('button, a.primary-button, a.secondary-button');
        if (!target || target.disabled || target.getAttribute('aria-disabled') === 'true') return;
        if (target === toggle) { manager.setMuted(!manager.muted); updateToggle(); return; }
        // Submit interactions emit through submit, including keyboard submission.
        if (target.matches('button') && target.type === 'submit' && target.form) return;
        window.CityGuesserSound.emit('buttonClick');
    });
    document.addEventListener('submit', () => window.CityGuesserSound.emit('buttonClick'), true);
    document.addEventListener('visibilitychange', () => { if (document.hidden) manager.stop(); });
    window.addEventListener('pagehide', () => manager.stop());
    window.addEventListener('storage', event => {
        if (event.key !== preferencesKey) return;
        try {
            const saved = JSON.parse(event.newValue || '{}');
            manager.muted = saved.muted === true;
            manager.volume = Number.isFinite(saved.volume) ? Math.max(0, Math.min(1, saved.volume)) : .3;
            window.Howler?.mute(manager.muted); window.Howler?.volume(manager.volume);
            if (manager.muted) manager.stop();
            updateToggle();
        } catch (_) { /* Ignore invalid preferences from another tab. */ }
    });
    const data = document.querySelector('#sound-events-data');
    const navigation = performance.getEntriesByType('navigation')[0];
    if (data && !['reload', 'back_forward'].includes(navigation?.type)) {
        try { window.CityGuesserSound.emit('outcome', JSON.parse(data.textContent)); }
        catch (_) { /* Audio must never interrupt the game. */ }
    }
})();
