# Hints, daily challenge and personal records

## Rules

- Type-answer games offer two optional hints: geographic region, then country/region. Singapore, Hong Kong and Macau use a descriptive second clue to avoid spelling out the answer.
- Hints are preloaded and revealed by a compact tab below the input, without page navigation or network requests. Ordinary hints and drafts use sessionStorage; daily hints and drafts use localStorage so they survive closing the tab. Hint use is sent with the answer. Collapsing the tab does not undo hint use.
- Hints keep the existing score and streak rules. Correct answers with hints are counted separately; wrong answers and reveals are not assisted correct answers.
- Daily challenge uses the same random photo selection, background prefetch, recent-photo/city history and local fallback as an ordinary ten-question challenge. Each player gets their own questions; photos and choice options for the current question remain fixed when reloading or resuming.
- A day starts at 00:00 Beijing time (UTC+8). A game begun before midnight can finish its original day's questions.
- Starting a daily game reserves the day's single attempt across both answer modes. Its answer mode is locked. Leaving, closing the tab, switching to ordinary play or losing the server session does not reset it: the browser keeps signed progress for resuming the same questions and score. A finished run can only be viewed, never replayed.
- Only ordinary ten-question challenges have personal bests, with separate text/choice records. Highest score wins; equal scores prefer more correct answers without hints. Daily games save one dated result without a best-score comparison. Endless mode shows cumulative statistics without recording a total-score best.

## Storage and image supply

New daily games no longer generate or read a shared daily manifest. Their first question can request a dynamic photo just like ordinary play, and later questions use the same prefetch/cache/fallback path. Network failure can still lead to local fallback photos; this change does not expand the underlying photo library or guarantee ten dynamic photos.

Signed daily snapshots contain the current question (including photo, credits and choices), fallback-pool position and ten recent photo/city entries. On resume these entries are merged with the current session's history. Legacy browser snapshots that contain only a question index are migrated using their original `instance/daily/YYYY-MM-DD.json`; retain old files while those snapshots may still be used. A missing original file reports an error instead of silently replacing the question or resetting the score. Once restored, later questions use ordinary selection and the updated snapshot no longer needs the old file.

Personal records, daily results and signed daily progress use `localStorage` in the current browser and site origin. They do not sync between devices; clearing site data removes them and the browser's one-attempt restriction. The server session retains two recent daily snapshots, and new-game forms restore signed browser snapshots after cookie expiry. Earlier snapshots of the same run cannot rewind newer server progress. Keep the Flask secret stable across server restarts so saved progress remains readable. The home page offers unfinished earlier daily games for resuming across midnight. Existing daily scores are preserved during migration; old daily-best entries are removed.

## Verification

Completed daily results are displayed directly from the browser's dated result on the home page. Viewing does not submit a reset request or require a signed progress token, so results from before progress-token support remain viewable. Signed snapshots are used when resuming unfinished games.

```powershell
python -m unittest discover -s tests -q
node --test tests/test_records.cjs
python -m flask --app app run --port 5011 --no-reload
```

Automated coverage checks dynamic first-question fetching, shared prefetch/selection, signed question and choice restoration, history merging, legacy snapshot migration, cookie size, ten-question completion, mode locking and midnight resumption. Browser verification of shared selection confirmed a real dynamic second photo and recovery of the same question, 100-point score, hint and draft after switching to ordinary play. Earlier verification covered completed-day result viewing and desktop/mobile hint layouts.
