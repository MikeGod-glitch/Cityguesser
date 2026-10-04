# Game sound system

`static/sound-system.js` owns the event-to-sound manifest, SoundController, and
SoundManager. Only SoundManager calls Howler. Both templates load the same local
Howler core 2.2.4 build and sound system, and include a persistent mute control.

## Events

- `buttonClick`: delegated button activation and accepted form submission;
  submit-button clicks emit only through submit to avoid duplication. Button-like
  links also emit. Disabled controls are ignored. Clicks have an 80ms cooldown.
- `correct` / `wrong`: structured data from a newly resolved `/check` request.
  Old question IDs, invalid choices, repeat submissions, and restored outcomes
  do not emit. `/reveal` does not emit `wrong`.
- `streak`: follows the correct sound at 3, 5, and 10 consecutive correct answers.
  This sound rule does not change scoring.
- `levelComplete`: first visit to `/results`, keyed by completion run ID.
  Challenge and Daily use the same completion path; Endless never completes.

Server events are serialized into `#sound-events-data` with Jinja `tojson`.
Question IDs and completion run IDs supply stable event identities. The
controller consumes identities before playback, including when muted or blocked,
and remembers the latest 256 in sessionStorage. Reload/back navigation suppresses
arrival sounds even if storage is unavailable. Returning via BFCache does not
reinitialize the sound system. Audio event storage never changes game records.

## Extension

Add an event entry to the manifest, add its licensed local audio asset, and emit
through the event layer. Local one-shot interactions can use
`CityGuesserSound.emit('buttonClick')`. Structured outcomes use
`CityGuesserSound.emit('outcome', [{type: 'correct', id: 'unique-event-id'}])`.
New server mechanics should publish the same payload format at their authoritative
transition. No gameplay handler needs audio filenames or Howler calls.

`CityGuesserSound.setMuted(boolean)` and `.setVolume(0..1)` persist preferences
in localStorage. Other tabs apply storage updates. The UI provides mute/unmute;
both pages also provide a 0–100% slider with a live percentage display. The default
volume is 30%; existing saved volume choices are preserved. Adjusting the slider
does not change the independent mute setting. Keyboard arrow keys adjust by 1%.

## Playback behavior

Outcome sounds play sequentially. Assets preload, and loading waits are bounded
at 1.2 seconds. Failed loads, blocked playback, absent audio support, and missing
storage degrade without affecting gameplay. Hiding/leaving the page stops audio
and invalidates pending playback. Failed or muted rewards are never saved for a
later interaction.

The application still uses full document navigation. Click sounds may be cut
short by navigation, and some browsers require a new gesture before arrival
sounds can play. Howler's gesture unlock and a permitted AudioContext resume are
used; playback restrictions are respected. Reliable uninterrupted cross-page
audio would require preserving the document during navigation, a separate change.

## Validation

Run `python -m unittest discover -s tests` and
`node --test tests/test_sound_system.cjs tests/test_records.cjs`.
The sound tests cover event boundaries, streak milestones, reveal/stale/duplicate
requests, completion identity, local asset serving, ordered playback, refresh
deduplication, mute persistence, blocked playback, failures, and disabled controls.

See `static/audio/SOURCES.md` for licenses and reproducible preparation.

The vendored core is 26,924 bytes (7,914 bytes gzip); the five WAV files total
304,888 bytes before HTTP compression. Chrome smoke validation confirmed all
five files decoded, mute persisted across navigation, the AudioContext ran after
submission, there were no JavaScript errors, and the 390px layout did not overflow.
