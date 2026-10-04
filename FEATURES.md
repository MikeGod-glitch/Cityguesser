# Hints, daily challenge and personal records

## Rules

- Type-answer games offer two optional hints: geographic region, then country/region. Singapore, Hong Kong and Macau use a descriptive second clue to avoid spelling out the answer.
- Hints are preloaded and revealed by a compact tab below the input, without page navigation or network requests. Ordinary hints and drafts use sessionStorage; daily hints and drafts use localStorage so they survive closing the tab. Hint use is sent with the answer. Collapsing the tab does not undo hint use.
- Hints keep the existing score and streak rules. Correct answers with hints are counted separately; wrong answers and reveals are not assisted correct answers.
- Daily challenge has ten distinct cities and images. Text and choice games share photos; choice options are also fixed for the day.
- A day starts at 00:00 Beijing time (UTC+8). A game begun before midnight can finish its original day's questions.
- Starting a daily game reserves the day's single attempt across both answer modes. Its answer mode is locked. Leaving, closing the tab, switching to ordinary play or losing the server session does not reset it: the browser keeps signed progress for resuming the same questions and score. A finished run can only be viewed, never replayed.
- Only ordinary ten-question challenges have personal bests, with separate text/choice records. Highest score wins; equal scores prefer more correct answers without hints. Daily games save one dated result without a best-score comparison. Endless mode shows cumulative statistics without recording a total-score best.

## Storage and image supply

Daily manifests are saved under `instance/daily/YYYY-MM-DD.json`. Complete files are published atomically; concurrent processes sharing this directory use the same manifest. Keep this directory on persistent storage during deployment. Server restarts reuse the saved photos and choices.

The first daily entry uses already prepared dynamic photos, then the existing 20-city fallback pool if necessary. It does not fetch ten new photos synchronously. A cold server can therefore produce an entirely fallback-based daily challenge. This feature does not expand the underlying photo library.

Personal records, daily results and signed daily progress use `localStorage` in the current browser and site origin. They do not sync between devices; clearing site data removes them and the browser's one-attempt restriction. The server session retains two recent daily snapshots, and new-game forms restore signed browser snapshots after cookie expiry. Earlier snapshots of the same run cannot rewind newer server progress. Keep the Flask secret stable across server restarts so saved progress remains readable. The home page offers unfinished earlier daily games for resuming across midnight. Existing daily scores are preserved during migration; old daily-best entries are removed.

## Verification

Completed daily results are displayed directly from the browser's dated result on the home page. Viewing does not submit a reset request or require a signed progress token, so results from before progress-token support remain viewable. Signed snapshots are used when resuming unfinished games.

```powershell
python -m unittest discover -s tests -q
node --test tests/test_records.cjs
python -m flask --app app run --port 5011 --no-reload
```

Browser verification covered daily entry, locked answer mode, switching to ordinary play and resuming, restoring progress/hints/drafts after closing the tab and losing the session, all ten answers, result composition, the completed-day view-only entry, and removal of daily-best cards. Earlier verification covered desktop/mobile hint layouts.
