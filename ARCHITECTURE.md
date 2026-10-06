# Project structure

City Guesser uses Flask/Jinja with full-page navigation and small browser scripts.
There is no database, frontend build step, or new runtime dependency introduced by
the structural refactor.

## Backend responsibilities

| Module | Responsibility |
| --- | --- |
| `city_catalog.py` | The 100-city catalog, aliases, coordinates, countries, flags, and styles. No I/O. |
| `city_choices.py` | Randomized distractor generation using catalog profiles. |
| `photo_rules.py` | Stable file identities, filename-family grouping, and metadata scoring. No I/O. |
| `city_provider.py` | Commons/Wikipedia requests, metadata filtering, thumbnail preparation, retry policies, and process-local caches. |
| `local_photos.py` | Ordered fixed photo sources/credits and image presentation for fixed or dynamic questions. |
| `question_prefetch.py` | Per-player background tasks, bounded waits, cancellation, expiration, and safe consumption. No Flask dependency. |
| `daily_progress.py` | Signed progress snapshots, validation, snapshot selection, and browser descriptions. |
| `game_features.py` | Beijing-time dates and two-level question hints. |
| `app.py` | HTTP routes, session transitions, scoring, history, daily restoration, and template context. |

`app.py` supplies the current-history conflict check to the prefetch manager.
The manager invokes it on the request thread, not inside the background fetch.
Background fetches receive history tuples and do not read Flask sessions.

## Question supply

The first question tries a prepared dynamic question, then synchronous dynamic
fetching, then the fixed pool. Later questions consume a usable completed
prefetch, try prepared cache, wait at most 0.3 seconds for prefetch, recheck the
cache, and finally use the fixed pool. Pending work survives a foreground timeout.

Candidate retrieval, filtering/family deduplication, individual thumbnail
preparation, and prepared-question selection are separate stages. Metadata scores
control eligibility and family representatives; accepted photos remain randomly
selected. The 5 km radius, 100 candidates, four city attempts, three file attempts,
scoring thresholds, cache TTLs, and retry behavior are unchanged.

History retains 20 cities and 100 image identities across ordinary restarts.
The fixed pool can relax recent-city exclusion when necessary. The historical
snapshot replay command has its own 20-photo window; use its stated limitations
when interpreting results, or the live fixture path to exercise current routes.

## Browser and templates

- `script.js`: theme control, hints/drafts, recognized-city flags, and submit state.
- `records.js`: record loading/migration, signed-run synchronization, completion
  recording, status rendering, and daily-entry form behavior.
- `sound-system.js`: sound preferences, semantic events, playback, and deduplication.
- `home-album.js`: the independent fixed homepage album.
- `brand.html`, `theme-toggle.html`, and `theme-bootstrap.html`: shared markup;
  the page-specific asset order and rendered DOM are retained.

Gameplay remains in the signed Flask session cookie. Records and signed daily
snapshots retain their existing localStorage keys and formats. Legacy daily
manifest restoration and existing browser-record migrations remain supported.

The static fallback SVGs remain available as initial images for questions without
Commons metadata. Commons-backed image load failures retain the current error UI;
this refactor does not introduce automatic SVG substitution.

## Verification

```powershell
python -B -m unittest discover -s tests -q
node --test tests/test_records.cjs tests/test_sound_system.cjs
python -B tools/validate_city_catalog.py
git diff --check
```

The lifecycle tests exercise expiration, cancellation isolation, and replacement
task retention. Existing tests retain their behavioral assertions; only their
internal prefetch/source-data references follow the new module boundaries.
