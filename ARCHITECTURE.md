# Project structure

City Guesser uses Flask/Jinja with full-page navigation and small browser scripts.
There is no frontend build step or external database service. Photo feedback uses
Python's built-in SQLite support; no additional runtime dependency is required.

## Backend responsibilities

| Module | Responsibility |
| --- | --- |
| `city_catalog.py` | The 150-city catalog, aliases, coordinates, countries, flags, and styles. No I/O. |
| `city_choices.py` | Randomized distractor generation using catalog profiles. |
| `photo_rules.py` | Stable file identities, filename-family grouping, and metadata scoring. No I/O. |
| `photo_sources.py` | Bounded geographic and city-category discovery, identity merging, and partial-failure isolation. |
| `photo_rotation.py` | Bounded per-player, per-city seen cycles, advanced only on actual display. |
| `photo_statistics.py` | Supply/failure counters and a 1,000-display window per city. |
| `city_provider.py` | Paced Commons requests, Wikipedia requests, metadata filtering, thumbnail preparation, and versioned candidate cache snapshots. |
| `local_photos.py` | Legacy photo metadata and presentation for existing saved questions; never selected for new gameplay. |
| `question_prefetch.py` | Per-player background tasks, bounded waits, cancellation, expiration, and safe consumption. No Flask dependency. |
| `dynamic_pool.py` | Shared background replenishment, city diversity, and atomic persistence of prepared dynamic questions. |
| `image_cache.py` | Optional background thumbnail downloads and bounded atomic disk storage; no question selection. |
| `photo_feedback.py` | Isolated photo feedback storage and local listing; never used by question selection. |
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
When strict prepared stock is empty, gameplay progressively reduces the recent-city
window to 10, 5, 2, 1, then 0 entries. Recent-photo exclusions always remain in force.
When possible it leaves at least two older cities eligible for a random draw, avoids
the latest city, and keeps the ordinary strict window whenever it has stock.
Foreground cache and prefetch conflict checks share this policy.

Ordinary page requests start one shared daemon replenishment worker when stock
is below target. Once started, it continues without further browser requests.
Every catalog city has a minimum-stock goal of twelve photos and a target of twenty-four.
Scheduling normally prioritizes empty cities, then cities with fresh, unstocked candidates,
ordered by minimum-stock status and stock count with randomized ties. It prepares
up to three photos per city turn, rechecking freshness, exclusions and stock each
time. Every fourth candidate-priority turn returns to the normal lowest-stock
order so cities needing discovery retain a share of the request budget. City runs
rotate when alternatives exist and stop at the minimum/target boundary.
When stock is below 100 photos or covers fewer than 21 cities, up to three city
turns prioritize existing candidates before reserving a turn for empty-city coverage.
Each batch attempts at most 24 preparations,
with three seconds between attempts. A failed city is deferred for five minutes
without stopping other cities; global API backoff stops the batch without marking
the city as failed. The worker waits at least 30 seconds between batches, honors
longer API retry periods, and sleeps for 60 seconds when stock is full or all
deficient cities are deferred. `stop()` interrupts idle/between-attempt waits;
an active HTTP request finishes first. These are inventory goals,
not a guarantee that Commons provides enough valid photos for every city. Prepared
questions are saved atomically to `instance/prepared-questions.json` and restored
on the first request after restart, retaining their original expiry. Newly prepared
question metadata lasts 24 hours. Expired metadata remains usable and is restored
only while its verified thumbnail is still in the local cache; the original metadata
expiry is never extended. The application retains thumbnails for up to 30 days,
subject to the existing 256 MiB cache capacity. Missing or evicted local files cannot
keep expired metadata usable. Remote-only expired questions are discarded.
Testing requests do not start the shared worker. The fixed homepage album is
independent of this gameplay pool.

Player prefetch first draws from eligible prepared inventory using the same city,
unseen-photo and history rules; it only attempts fresh preparation when no eligible
stock exists. All Commons API discovery/preparation calls share a process-local
serial request lock and a minimum three-second start interval. Error handling
publishes retry state before releasing the lock. HTTP 429/503, ratelimited and
maxlag retain global backoff. A cirrussearch-too-busy-error pauses only search
requests for 30 seconds; geosearch and thumbnail/credit metadata requests remain
available under the shared pacing budget. Discovery skips paused search queries. Wikipedia introductions and thumbnail file downloads use their own paths.
This budget is per application process, not coordinated across multiple servers.

Eligible candidate lists are saved atomically to `instance/photo-candidates.json`
when discovery changes them, including discoveries followed by preparation failure.
They are restored alongside prepared inventory on first use. Entries keep their
original six-hour freshness and stale-fallback deadlines; no restart extends them.
A fingerprint of discovery, filtering, eligibility constants and catalog source
invalidates old rule decisions; unrelated provider selection edits do not. Explicitly compatible fingerprints from before this supply-policy change
can migrate only while the separate discovery/filtering fingerprint still matches;
future eligibility-rule changes invalidate that migration. Restoring validates city names, dates and candidate fields, limits each
city to 500 entries, and ignores files exceeding 20 MiB or corrupt data without
discarding prepared questions. Unchanged candidate lists are not rewritten after
every preparation. This adds no service, database or runtime dependency.

Candidate retrieval, filtering/family deduplication, individual thumbnail
preparation, and prepared-question selection are separate stages. Metadata scores
control eligibility and family representatives. Each refresh samples the central
5 km area and two randomized opposite outer 3.5 km areas, centered approximately
4–8 km away, up to 100 files per request. Every refresh also attempts a city-scoped
street/building/skyline category search with one randomly chosen theme (parks,
markets, squares, stations, residences or museums). Direct categories supplement
failed/empty deep searches, or pools below 80 candidates.
Discovery is bounded to five requests, two seconds apart, and merges file identities
before family deduplication. When stock is below 100 photos or covers fewer than
21 cities, it returns early once 12 filtered candidates not already in prepared
stock are found. Early or partially failed discoveries expire after five minutes
to allow later area/category diversification; complete healthy discoveries retain
the six-hour freshness window. Quality and geographic evidence filters are unchanged. Partial query failures retain valid discoveries.
Category and outer-area results require city evidence in their title or metadata
to reduce neighboring-city contamination. JPEG/PNG/WebP,
the 300,000-pixel minimum and the score floor of five remain in force. Candidate
metadata refreshes every six hours, with existing stale-data failure handling.

Gameplay prioritizes random draws, unseen photos and scene variety over landmark
recognizability. The subject vocabulary includes panoramas, markets, residential
areas, aerial views, public art and city parks/gardens. Explicit map/icon, indoor,
portrait, food and close-up titles remain blocked. Incidental indoor/nature category
tags do not veto an explicit city scene. Green spaces require city-name evidence;
plant/animal subjects without city-scene wording remain excluded. These are metadata
heuristics, not visual reviews or a guarantee of correct municipal boundaries.

Exact file identities are deduplicated. A filename family can retain up to three
representatives distinguished by named scene, view/time cues, year or aspect class.
Counter-only duplicates still keep one representative. Resolution/metadata score
break ties within the same view; distinct eligible variants are sampled randomly
when more than three exist. Gameplay drawing does not weight photos by score.

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
failures, filtering rejection counts, filename-family counts, and actual-display
frequencies. Rejection counts distinguish unsupported formats, tiny files, unsuitable
subjects, insufficient scene evidence and family deduplication. The display window keeps the latest 1,000
displays per city; its repeat fraction is a window diversity metric, not a 100-question
repeat rate or a browser image-load success rate. Statistics reset on process restart.

History retains 20 cities and 100 image identities across ordinary restarts.
Existing saved fixed questions remain renderable, but are not selected again.
The historical
snapshot replay command has its own 20-photo window; use its stated limitations
when interpreting results, or the live fixture path to exercise current routes.

## Browser and templates

Dynamic thumbnails are opportunistically downloaded by a single daemon worker
into `instance/thumbnails`. The queue holds at most 64 downloads; files are limited
to 8 MiB, expire after seven days, and the cache is capped at 256 MiB. Downloads
accept only HTTPS Wikimedia thumbnail/upload hosts and JPEG, PNG or WebP, with
signature checks, timeouts and a five-minute failure cooldown. Existing prepared
inventory is warmed on ordinary requests at most every 30 seconds. Fresh pool
and per-player preparations also enqueue their image without waiting for download.

Selection, city weighting, image identities and seen/history checks are unchanged.
Rendering uses `/photos/<opaque-hash>` on a file-cache hit and the original remote
URL on a miss. Files use conditional HTTP caching and a one-day browser max-age.
If a local image is evicted between render and fetch, the browser tries the original
URL for that same image. Cache failures never redraw a question.

The current image is preloaded with high priority and decoded asynchronously.
After it loads, the browser warms one following image with low priority. A result
page can supply its already-ready next image directly; otherwise `/prefetch-image`
peeks at the existing background task, bound to the current question ID. It returns
only an image URL, never draws a new question or records history. Checks are bounded
to 12 attempts, skip hidden tabs, and stop on navigation. Image errors offer a retry
of the same photograph without scoring or revealing an answer.

The gameplay photo viewport supports mouse-wheel zoom from the full-photo view
to 4x, anchored to the cursor. Left-button pointer capture pans a zoomed photo;
translation is clamped using the actual contained image aspect ratio. Double-click
or the small Reset view button restores the photo. Each image load/retry resets
the view; page navigation creates a fresh viewer. Loading/error images and overlay
controls do not intercept wheel input. Touch scrolling and Ctrl-wheel browser zoom
retain their native behavior. This uses the existing bitmap and CSS transforms,
with no additional image downloads or runtime dependency.

Browser Performance entries `city-page-response` and `city-photo-visible` measure
navigation-to-first-response and first-response-to-image-ready respectively. Inspect
them through `performance.getEntriesByType('measure')` in developer tools. These
local diagnostics are not uploaded analytics; image timing includes rendering and
script scheduling. Actual improvements require before/after browser measurements
on the deployment and network used by players.

- `script.js`: photo loading/retry/prewarming, theme control, hints/drafts, recognized-city flags, and submit state.
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
Commons metadata. Commons-backed image load failures retain the error UI with a
same-image retry; there is no automatic SVG substitution.

## Photo feedback

The question-mark icon sits above and outside the gameplay photo frame. Hover or
keyboard focus reveals "missing city clues". A native modal dialog first asks
"Does this photo lack city clues?"; Yes reveals four single-choice reasons:
Indoor scene, Person close-up, Object close-up, and Other. Only confirmation,
a selected reason, and Submit feedback send the current question ID and reason
to `POST /photo-feedback` without
navigating, answering, changing scores, or advancing history. The route reads the
existing signed session's photo metadata rather than accepting image URLs or city
names from the browser. Feedback requests skip background pool startup and image
warming, and never draw or prefetch a question.

Reports are saved only on submission to `instance/photo-feedback.sqlite3`, separate
from the photo stock and candidates. Each photo merges reports from distinct
anonymous reporters; repeat submissions by the same player count once. Reporter
identities are keyed hashes, and gameplay sessions are not saved in the database.
Reasons are validated server-side and aggregated in the local listing. Older
reports remain readable as "Unspecified (legacy)" and migrate transactionally on
the next submission. Cancellation does not send a request; submission errors
retain the selected reason for retry. Native dialog manages focus and Escape;
unsupported browsers keep the entry hidden.
Concurrent submissions use SQLite transactions. A storage failure returns a
retryable message without changing the game. The browser also permits retry after
a network failure or ten-second timeout. Without JavaScript the control stays
hidden rather than navigating to the JSON endpoint.

To inspect feedback locally, including image/source links, city, report count and
timestamps, run `python -B -m photo_feedback` (or supply `--path` for another saved
database). The command is read-only and prints an empty list before any reports.
No AI, moderation interface, automatic exclusion, blacklist or weighting exists in
this stage. The database must be retained on persistent storage and included in
backups if reports should survive deployment replacement. It is ignored by Git.

## Verification

```powershell
python -B -m unittest discover -s tests -q
node --test tests/test_records.cjs tests/test_sound_system.cjs tests/test_photo_loading.cjs
python -B tools/validate_city_catalog.py
git diff --check
```

The lifecycle tests exercise expiration, cancellation isolation, and replacement
task retention. Existing tests retain their behavioral assertions; only their
internal prefetch/source-data references follow the new module boundaries.
