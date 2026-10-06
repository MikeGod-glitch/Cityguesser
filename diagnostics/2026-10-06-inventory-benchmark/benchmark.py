"""Isolated inventory-policy replay plus passive observation of real live files.

No external API requests; never writes application instance files. Controlled
preparations assume valid thumbnails succeed and take 0.5 simulated seconds.
Only cities actually present in the saved candidate snapshot are replenished.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from io import StringIO
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
from threading import Event
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import city_provider as provider
import dynamic_pool
from photo_rotation import PhotoRotation

TZ = timezone(timedelta(hours=8))
OUT = Path(__file__).resolve().parent


def stamp(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, TZ).isoformat()


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read(path):
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if attempt == 4:
                raise
            time.sleep(.05)


def richness(entries, now):
    catalog = [city[0] for city in provider.CITIES]
    counts = Counter(entry["question"]["answer"] for entry in entries if entry["expires_at"] > now)
    values = [counts[name] for name in catalog]
    return {"photos": sum(values), "cities": sum(v > 0 for v in values),
            "cities_with_at_most_2": sum(v <= 2 for v in values),
            "cities_at_minimum_12": sum(v >= 12 for v in values),
            "cities_at_target_24": sum(v >= 24 for v in values),
            "median_per_city": statistics.median(values),
            "stock_distribution": dict(sorted(Counter(values).items()))}


def snapshot_live():
    entries = read(ROOT / "instance/prepared-questions.json")
    stats = read(ROOT / "instance/photo-pool-stats.json")
    candidates_path = ROOT / "instance/photo-candidates.json"
    candidates = read(candidates_path) if candidates_path.exists() else {"cities": {}}
    events = sum((Counter(row.get("events", {})) for row in stats["cities"].values()), Counter())
    return {"observed_at": stamp(), "stats_at": stamp(stats["updated_at"]),
            "richness": richness(entries, time.time()), "events": dict(events),
            "candidates": sum(len(row["photos"]) for row in candidates["cities"].values()),
            "candidate_cities": len(candidates["cities"]),
            "recorded_errors": dict(Counter(row.get("last_error") for row in stats["cities"].values()
                                             if row.get("last_error")))}, entries, candidates


class VirtualClock:
    def __init__(self, base, duration):
        self.base, self.elapsed, self.duration = base, 0.0, duration
        self.stopped = Event()

    def time(self):
        return self.base + self.elapsed

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed = min(self.duration, self.elapsed + max(0, seconds))
        if self.elapsed >= self.duration:
            self.stopped.set()

    def wait(self, seconds):
        self.sleep(seconds)
        return self.stopped.is_set()


def supply_replay(initial, candidates, *, continuous, seed, duration):
    clock = VirtualClock(time.time(), duration)
    names = set(candidates["cities"])
    cities = [city for city in provider.CITIES if city[0] in names]
    calls = []
    patches = []
    for name in ("_photo_cache", "_credit_cache", "_prepared_questions", "_photo_retry_after",
                 "_thumbnail_retry_after", "_api_retry_after"):
        patches.append(patch.object(provider, name, {}))
    patches += [patch.object(provider, "_candidate_generation", 0),
                patch.object(provider, "_commons_next_request_at", 0),
                patch.object(provider, "time", clock), patch.object(dynamic_pool, "time", clock)]
    def api_response(request, **kwargs):
        from urllib.parse import parse_qs, urlsplit
        params = parse_qs(urlsplit(request.full_url).query)
        title = params["titles"][0]
        calls.append({"at": clock.elapsed, "title": title})
        clock.sleep(.5)
        # Dummy delivery URLs are never fetched; identity is the real saved file title.
        return StringIO(json.dumps({"query": {"pages": [{"imageinfo": [{
            "thumburl": "https://example.invalid/" + provider._photo_id(title),
            "descriptionurl": "https://commons.wikimedia.org/wiki/" + title,
            "extmetadata": {"Artist": {"value": "benchmark fixture"},
                            "LicenseShortName": {"value": "benchmark fixture"}},
        }]}]}}))
    from contextlib import ExitStack
    with ExitStack() as stack:
        for replacement in patches:
            stack.enter_context(replacement)
        stack.enter_context(patch.object(provider, "urlopen", side_effect=api_response))
        if not continuous:
            # Previous API calls had reactive backoff but no shared pacing lock.
            stack.enter_context(patch.object(provider, "_api_get", side_effect=
                lambda params, endpoint=provider.COMMONS_API: provider._request_api(params, endpoint)))
        provider.restore_prepared_questions(initial)
        provider.restore_candidate_photos(candidates)
        # No discovery is attempted for unknown cities. These are the only tested
        # candidate populations, not a claim that the other cities have no candidates.
        stack.enter_context(patch.object(provider, "_fetch_photos",
            side_effect=lambda city: [p.copy() for p in candidates["cities"][city[0]]["photos"]]))
        pool = dynamic_pool.DynamicQuestionPool(OUT / "unused.json", cities=cities)
        pool.stop_event = clock.stopped
        pool.pause = clock.wait
        for method in ("_save", "_save_statistics", "_save_candidates"):
            stack.enter_context(patch.object(pool, method))
        random.seed(seed)
        if continuous:
            pool._run()
        else:
            # The old worker finished one 24-attempt batch; no browser request
            # arrives during this idle-window experiment to trigger another batch.
            pool._refill()
            clock.sleep(duration - clock.elapsed)
        result = provider.prepared_question_snapshot()
    return result, {"seed": seed, "api_calls": len(calls), "simulated_seconds": clock.elapsed,
                    "evaluated_at_epoch": clock.time(),
                    "richness": richness(result, clock.time())}


def selection_replay(entries, *, runs, questions, evaluation_time=None):
    rows = []
    for seed in range(runs):
        with patch.object(provider, "_prepared_questions", {}), \
             patch.object(provider, "time", VirtualClock(evaluation_time or time.time(), 0)):
            provider.restore_prepared_questions(entries)
            random.seed(seed)
            rotation = PhotoRotation()
            recent_images, recent_cities, displays = [], [], []
            misses = 0
            for attempt in range(questions):
                question = provider.get_cached_question(recent_images, recent_cities,
                                                         seen_images=rotation.seen("benchmark"))
                if question is None:
                    misses += 1
                    continue
                image_id, city = question["image_id"], question["answer"]
                displays.append((image_id, city))
                recent_images = (recent_images + [image_id])[-100:]
                recent_cities = (recent_cities + [city])[-20:]
                rotation.record("benchmark", city, image_id)
            images = [image for image, _ in displays]
            unique = len(set(images))
            windows = {}
            for window in (50, 100, 200):
                comparable = len(images) - window
                windows[str(window)] = (sum(images[i] in images[i-window:i]
                                           for i in range(window, len(images))) / comparable
                                        if comparable > 0 else None)
            rows.append({"seed": seed, "displayed": len(images), "stock_misses": misses,
                         "unique_photos": unique,
                         "overall_repeat_fraction": (len(images) - unique) / len(images) if images else None,
                         "window_repeat_fractions": windows,
                         "displayed_cities": len({city for _, city in displays})})
    def aggregate(values):
        return {"mean": statistics.mean(values), "min": min(values), "max": max(values)}
    return {"runs": runs, "questions_per_run": questions,
            "overall_repeat_fraction": aggregate([r["overall_repeat_fraction"] for r in rows]),
            "unique_photos": aggregate([r["unique_photos"] for r in rows]),
            "stock_miss_fraction": aggregate([r["stock_misses"] / questions for r in rows]),
            "window_repeat_fractions": {str(w): aggregate([r["window_repeat_fractions"][str(w)] for r in rows])
                                        for w in (50, 100, 200)},
            "rows": rows}


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitor-seconds", type=int, default=120)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--questions", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    OUT = args.output.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
               for name in ("city_provider.py", "dynamic_pool.py", "app.py", "photo_rules.py", "photo_sources.py")}
    observed, initial, candidates = snapshot_live()
    write("input-prepared.json", initial)
    write("input-candidates.json", candidates)
    monitoring = [observed]
    print("LIVE START", observed["observed_at"], observed["richness"], flush=True)
    before_rows, after_rows = [], []
    for seed in range(5):
        before, before_row = supply_replay(initial, candidates, continuous=False, seed=seed, duration=600)
        after, after_row = supply_replay(initial, candidates, continuous=True, seed=seed, duration=600)
        assert before_row["api_calls"] > 0, "Baseline must exercise preparation, not silently fail"
        assert len(before) - len(initial) == before_row["api_calls"]
        assert len(after) - len(initial) == after_row["api_calls"]
        before_rows.append(before_row)
        after_rows.append(after_row)
        if seed == 0:
            write("controlled-before-inventory.json", before)
            write("controlled-after-inventory.json", after)
            representative_before, representative_after = before, after
            before_time, after_time = before_row["evaluated_at_epoch"], after_row["evaluated_at_epoch"]
    print("CONTROLLED SUPPLY", {"before_photos": [r["richness"]["photos"] for r in before_rows],
                                 "after_photos": [r["richness"]["photos"] for r in after_rows]}, flush=True)
    repeats = {
        "live_inventory_snapshot": selection_replay(initial, runs=args.runs, questions=args.questions),
        "controlled_before": selection_replay(representative_before, runs=args.runs, questions=args.questions,
                                                evaluation_time=before_time),
        "controlled_after": selection_replay(representative_after, runs=args.runs, questions=args.questions,
                                               evaluation_time=after_time),
    }
    print("SELECTION REPLAY", {name: {k: v for k, v in result.items() if k != "rows"}
                               for name, result in repeats.items()}, flush=True)
    started = time.monotonic()
    while time.monotonic() - started < args.monitor_seconds:
        time.sleep(min(20, args.monitor_seconds - (time.monotonic() - started)))
        observed, _, _ = snapshot_live()
        monitoring.append(observed)
        print("LIVE SAMPLE", observed["observed_at"], observed["richness"]["photos"], flush=True)
    end_snapshot, end_entries, _ = snapshot_live()
    write("live-end-prepared.json", end_entries)
    result = {
        "scope": "Last inventory scheduling/persistence change; filtering and target 24 are held fixed.",
        "historical_before_snapshot": {"at": "2026-10-06T16:39:12.021669+08:00",
            "photos": 205, "cities": 100, "cities_with_at_most_2": 77,
            "source": "Recorded read-only observation in this chat; exact pre-change image identities unavailable."},
        "live_observations": monitoring, "live_end": end_snapshot,
        "controlled_supply": {"before": before_rows, "after": after_rows,
            "known_candidate_cities": sorted(candidates["cities"]),
            "candidate_photos": sum(len(v["photos"]) for v in candidates["cities"].values()),
            "conditions": "Same real saved inventory/candidate inputs. Ten simulated idle minutes, no page requests, 0.5 seconds per successful metadata response, no 429. Before: one batch; after: continuous production worker. Unknown candidate cities not replenished. These are controlled upper-bound behavior, not live network forecasts."},
        "selection_replays": repeats,
        "limitations": ["No external API/image downloads or real-browser sessions performed.",
            "Selection runs use actual production cache selector and rotation with 100-image/20-city history; static snapshots do not include replenishment while playing.",
            "Overall repetition means all previously displayed IDs in the 1000-question run; window repetition is separately defined.",
            "Old/new live player logs and exact old inventory IDs are unavailable; live pre/post repetition improvement cannot be measured.",
            "Stats counters reset across process reloads; pre/post failure counts must not be treated as comparable rates."],
        "sources_unchanged": sources == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
    }
    write("summary.json", result)
    print("SAVED", OUT / "summary.json", flush=True)


if __name__ == "__main__":
    main()
