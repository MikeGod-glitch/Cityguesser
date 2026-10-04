# Game sound effects

All five audio files are CC0-1.0. Keep the two upstream license files and
`sources.json` alongside the assets. No runtime downloads or external CDN are used.

| Event | Local file | Original |
| --- | --- | --- |
| buttonClick | button-click.wav | Kenney UI Audio / click4.wav |
| correct | correct.wav | Kenney Music Jingles / jingles_PIZZI00.ogg |
| wrong | wrong.wav | Kenney Music Jingles / jingles_PIZZI04.ogg |
| streak | streak.wav | Kenney Music Jingles / jingles_PIZZI01.ogg |
| levelComplete | level-complete.wav | Kenney Music Jingles / jingles_PIZZI07.ogg |

UI audio: https://github.com/Calinou/kenney-ui-audio
Pinned commit: 8c3d81b9159d058c444f89d12d518276b0b09345
Original author: https://kenney.nl/assets/ui-audio

Music Jingles: https://kenney.nl/assets/music-jingles
Archive: https://kenney.nl/media/pages/assets/music-jingles/f37e530b9e-1677590399/kenney_music-jingles.zip

The UI click is unchanged. Selected jingles are converted to mono PCM16 WAV,
trimmed, faded at both ends, and peak-normalized with headroom. Reproduce them
with `tools/prepare_game_audio.py` and the extracted upstream pack. That optional
development tool needs numpy/soundfile; the Flask application does not.
Original and prepared file hashes and durations are in `sources.json`.

Howler.js core 2.2.4 is vendored separately under `static/vendor/howler/`, with
its MIT license: https://github.com/goldfire/howler.js/tree/v2.2.4
