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
| `photo_sources.py` | Bounded geographic and city-category discovery, identity merging, and partial-failure isolation. |
| `photo_rotation.py` | Bounded per-player, per-city seen cycles, advanced only on actual display. |
| `photo_statistics.py` | Supply/failure counters and a 1,000-display window per city. |
| `city_provider.py` | Commons/Wikipedia requests, metadata filtering, thumbnail preparation, retry policies, and process-local caches. |
| `local_photos.py` | Legacy photo metadata and presentation for existing saved questions; never selected for new gameplay. |
| `question_prefetch.py` | Per-player background tasks, bounded waits, cancellation, expiration, and safe consumption. No Flask dependency. |
| `dynamic_pool.py` | Shared background replenishment, city diversity, and atomic persistence of prepared dynamic questions. |
| `daily_progress.py` | Signed progress snapshots, validation, snapshot selection, and browser descriptions. |
| `game_features.py` | Beijing-time dates and two-level question hints. |
| `app.py` | HTTP routes, session transitions, scoring, history, daily restoration, and template context. |

`app.py` supplies the current-history conflict check to the prefetch manager.
The manager invokes it on the request thread, not inside the background fetch.
Background fetches receive history tuples and do not read Flask sessions.

## Question supply

All new questions consume a usable completed prefetch, try prepared cache, wait
at most 0.3 seconds for prefetch, and recheck the cache. If no dynamic question is
ready, a themed loading page retries every five seconds and provides manual retry
and home links. Pending work and game progress survive foreground timeouts.
There is no fixed question fallback, and waiting never records an answer or photo.

Non-static requests also trigger one shared background replenishment worker.
Every catalog city has a minimum-stock goal of eight photos and a target of twelve.
Cities below the minimum are replenished before topped-up cities, with the lowest
stock first and randomized ties. Each batch attempts at most 24 preparations,
with three seconds between attempts. A failed city is deferred for five minutes
without stopping other cities; global API backoff stops the batch. Further batches
are triggered by requests after at least 30 seconds. These are inventory goals,
not a guarantee that Commons provides enough valid photos for every city. Prepared
questions are saved atomically to `instance/prepared-questions.json` and restored
on the first request after restart, retaining their original six-hour expiry.
Testing requests do not start the shared worker. The fixed homepage album is
independent of this gameplay pool.

Candidate retrieval, filtering/family deduplication, individual thumbnail
preparation, and prepared-question selection are separate stages. Metadata scores
control eligibility and family representatives. Each refresh samples the central
5 km area and two randomized adjacent 2.5 km areas (centers approximately 2.5 km
away), up to 100 files per request. Sparse pools are supplemented by city-scoped
street/building/skyline category searches, including a direct-category fallback.
Discovery is bounded to five requests, two seconds apart, and merges file identities
before family deduplication. Partial query failures retain valid discoveries.
Category results require city evidence in their title or metadata. Existing format,
pixel, scene-quality, cache-expiry and recent-history rules remain in force.

Preparation favors named scenes with fewer already-stocked photos. Scene keys are
metadata heuristics, not visual similarity detection. Cache drawing still chooses
an eligible city uniformly, then randomly chooses among that player's unseen photos
in the city, recycling when no unseen eligible photo remains. Prefetch uses a frozen
seen snapshot and does not advance cycles; consumption revalidates current history
and unseen cache alternatives. Cycles are kept outside cookie sessions, bounded to
2,048 players/128 identities per city, expire after six inactive hours, and survive
ordinary game resets through a stable player ID. Process restarts reset cycles;
the cookie's last-100 image history continues to protect short-term repeats.

`instance/photo-pool-stats.json` is written atomically after batches and at most once
per minute on requests, including each city's inventory, discovery source counts,
failures, and actual-display frequencies. The display window keeps the latest 1,000
displays per city; its repeat fraction is a window diversity metric, not a 100-question
repeat rate or a browser image-load success rate. Statistics reset on process restart.

History retains 20 cities and 100 image identities across ordinary restarts.
Existing saved fixed questions remain renderable, but are not selected again.
The historical
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
