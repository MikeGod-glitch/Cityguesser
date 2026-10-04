"""Prepare Kenney CC0 jingles: python tools/prepare_game_audio.py EXTRACTED_PACK.

Development only: requires numpy and soundfile, not needed by the game.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import soundfile as sf


def prepare(directory):
    destination = Path(__file__).resolve().parents[1] / 'static' / 'audio'
    selections = {'correct.wav': ('jingles_PIZZI00.ogg', .85),
                  'wrong.wav': ('jingles_PIZZI04.ogg', .85),
                  'streak.wav': ('jingles_PIZZI01.ogg', 1.25),
                  'level-complete.wav': ('jingles_PIZZI07.ogg', 2.2)}
    records = []
    for target, (source, limit) in selections.items():
        original = directory / 'Audio' / 'Pizzicato jingles' / source
        samples, rate = sf.read(original, always_2d=True)
        samples = samples.mean(axis=1)
        audible = np.flatnonzero(np.abs(samples) > .002)
        if len(audible):
            samples = samples[max(0, audible[0] - int(rate * .01)):audible[-1] + 1]
        samples = samples[:int(rate * limit)]
        fade = min(int(rate * .02), len(samples) // 2)
        samples[:fade] *= np.linspace(0, 1, fade)
        samples[-fade:] *= np.linspace(1, 0, fade)
        peak = np.max(np.abs(samples))
        if peak:
            samples *= .75 / peak
        sf.write(destination / target, samples, rate, subtype='PCM_16')
        records.append({'file': target, 'original': source, 'license': 'CC0-1.0',
                        'source': 'https://kenney.nl/assets/music-jingles',
                        'original_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
                        'sha256': hashlib.sha256((destination / target).read_bytes()).hexdigest(),
                        'duration_seconds': round(len(samples) / rate, 3)})
    click = destination / 'button-click.wav'
    with sf.SoundFile(click) as audio:
        duration = round(len(audio) / audio.samplerate, 3)
    records.insert(0, {'file': click.name, 'original': 'click4.wav', 'license': 'CC0-1.0',
                      'source': 'https://github.com/Calinou/kenney-ui-audio/blob/8c3d81b9159d058c444f89d12d518276b0b09345/addons/kenney_ui_audio/click4.wav',
                      'sha256': hashlib.sha256(click.read_bytes()).hexdigest(),
                      'duration_seconds': duration})
    (destination / 'sources.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(records, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    prepare(parser.parse_args().directory)
