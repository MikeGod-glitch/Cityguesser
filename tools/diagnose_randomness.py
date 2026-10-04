"""Standalone diagnostics; production drawing and network settings stay unchanged.

Examples:
  python -B tools/diagnose_randomness.py offline --output diagnostics/offline
  python -B tools/diagnose_randomness.py survey --output diagnostics/survey
  python -B tools/diagnose_randomness.py live --questions 40 --think-seconds 2 --output diagnostics/live
  python -B tools/diagnose_randomness.py replay --snapshot diagnostics/survey/photos.json --output diagnostics/replay

Live measurements use Flask's test client, not a browser: image loading is UNKNOWN.
Survey credit checks sample one photo per city, not every photo in its pool.
All monkeypatches are scoped to this standalone process.
"""

import argparse
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
from threading import Event, Lock, local
import time
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as game
import city_provider as provider


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def quantiles(values):
    """Nearest rank sample quantiles, including failures if supplied by caller."""
    if not values:
        return {"n": 0, "p50": None, "p95": None}
    values = sorted(values)
    import math
    return {"n": len(values), "p50": values[math.ceil(.5 * len(values)) - 1],
            "p95": values[math.ceil(.95 * len(values)) - 1]}


def metrics(exposures, catalog=None):
    n = len(exposures)
    images = Counter(e["image_id"] for e in exposures)
    cities = Counter(e["city"] for e in exposures)
    result = {"n": n, "image_unique_rate": len(images) / n if n else None,
              "city_unique_rate": len(cities) / n if n else None,
              "effective_image_count": n*n / sum(v*v for v in images.values()) if n else None}
    for w in (10, 20, 50):
        for label, key in (("image", "image_id"), ("city", "city")):
            count = sum(any(exposures[t][key] == e[key] for e in exposures[t-w:t])
                        for t in range(w, n))
            result[f"{label}_repeat_rate_{w}"] = count/(n-w) if n > w else None
    names = catalog if catalog is not None else [c[0] for c in provider.CITIES]
    if n and names:
        # Unexpected cities must not silently disappear from the distance.
        keys = set(names) | set(cities)
        result["city_distribution_tv"] = .5 * sum(abs(cities[c]/n - (1/len(names) if c in names else 0)) for c in keys)
        result["city_coverage"] = len(set(cities) & set(names))/len(names)
    else:
        result["city_distribution_tv"] = result["city_coverage"] = None
    result["city_counts"] = dict(cities)
    return result


def question_identity(city):
    # A local Commons question and the same dynamically returned file share identity.
    if not city.get("is_dynamic") and city.get("commons"):
        return provider._photo_id("File:" + city["commons"])
    return city.get("image_id", "unknown")


def source_digest():
    return {name: hashlib.sha256((Path(game.__file__).parent/name).read_bytes()).hexdigest()
            for name in ("app.py", "city_provider.py", "city_choices.py")}


class Recorder:
    def __init__(self, interval=0):
        self.attempts, self.api_calls, self.tasks, self.exposures, self.waits = [], [], [], [], []
        self.ctx, self.lock, self.rate_lock = local(), Lock(), Lock()
        self.interval, self.last_call = interval, 0
        self.rate_limited = Event()

    def api(self, original, params, endpoint=provider.COMMONS_API):
        if self.rate_limited.is_set():
            raise RuntimeError("diagnostic_stopped_after_http_429")
        if self.interval:
            with self.rate_lock:
                delay = self.interval - (time.monotonic() - self.last_call)
                if delay > 0:
                    time.sleep(delay)
                if self.rate_limited.is_set():
                    raise RuntimeError("diagnostic_stopped_after_http_429")
                self.last_call = time.monotonic()
        start = time.monotonic()
        call = {"city": getattr(self.ctx, "city", None),
                "kind": "photos" if params.get("generator") == "geosearch" else "credit",
                "status": "success"}
        try:
            data = original(params, endpoint)
            call["returned_files"] = len(data.get("query", {}).get("pages", []))
            if "error" in data:
                call["api_error"] = data["error"].get("code", "unknown")
                call["api_error_info"] = data["error"].get("info", "")
                call["params"] = params
                call["status"] = "api_error_payload"
                self.ctx.api_error = data["error"]
            return data
        except Exception as exc:
            call.update(status="error", error_type=type(exc).__name__, error=str(exc))
            if isinstance(exc, HTTPError):
                call["http_status"] = exc.code
                call["retry_after"] = exc.headers.get("Retry-After") if exc.headers else None
                if exc.code == 429:
                    self.rate_limited.set()
            raise
        finally:
            call["seconds"] = time.monotonic()-start
            with self.lock:
                self.api_calls.append(call)

    def fetch(self, original, city):
        self.ctx.city = city[0]
        self.ctx.api_error = None
        cached = provider._photo_cache.get(city[0])
        rec = {"city": city[0], "cache_hit": bool(cached and cached["expires_at"] > time.time()),
               "phase": getattr(self.ctx, "phase", "live"), "status": "success"}
        start = time.monotonic()
        try:
            photos = original(city)
            rec["pool_size"] = len(photos)
            rec["remaining_after_exclusion"] = sum(provider._photo_id(p["title"]) not in getattr(self.ctx, "excluded", set()) for p in photos)
            if not photos:
                if self.ctx.api_error:
                    rec.update(status="api_error_payload", api_error=self.ctx.api_error)
                else:
                    rec["status"] = "empty_pool"
            elif not rec["remaining_after_exclusion"]:
                rec["status"] = "recent_history_exhausted"
            return photos
        except Exception as exc:
            rec.update(status="api_error_payload" if isinstance(exc, provider.CommonsApiError) and exc.code != "backoff" else "fetch_error",
                       error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            rec["seconds"] = time.monotonic()-start
            self.ctx.last_fetch_status = rec["status"]
            with self.lock:
                self.attempts.append(rec)

    def question(self, original, excluded_images=(), excluded_cities=()):
        self.ctx.excluded = set(excluded_images)
        start = time.monotonic()
        rec = {"excluded_city_count": len(excluded_cities)}
        try:
            q = original(excluded_images, excluded_cities)
            rec.update(status="success" if q else "no_question", city=q["answer"] if q else None)
            return q
        except Exception as exc:
            rec.update(status="exception", error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            rec["seconds"] = time.monotonic()-start
            with self.lock:
                self.tasks.append(rec)

    def patches(self):
        stack = ExitStack()
        api, fetch, question = provider._api_get, provider._fetch_photos, provider.get_random_question
        stack.enter_context(patch.object(provider, "_api_get", side_effect=lambda *a, **kw: self.api(api, *a, **kw)))
        stack.enter_context(patch.object(provider, "_fetch_photos", side_effect=lambda *a, **kw: self.fetch(fetch, *a, **kw)))
        stack.enter_context(patch.object(game, "get_random_question", side_effect=lambda *a, **kw: self.question(question, *a, **kw)))
        return stack

    def save(self, out):
        write_json(out/"trace.json", {"attempts": self.attempts, "api_calls": self.api_calls,
                                     "tasks": self.tasks, "exposures": self.exposures, "waits": self.waits})


def survey(args):
    recorder = Recorder(interval=args.interval)
    photos_by_city = json.loads(args.resume_snapshot.read_text(encoding="utf-8")) if args.resume_snapshot else {}
    resumed_count = len(photos_by_city)
    for name, photos in photos_by_city.items():
        provider._photo_cache[name] = {"photos":photos, "expires_at":time.time()+provider.CACHE_TTL_SECONDS}
    rows = []
    selected_catalog = [c for c in provider.CITIES if not args.cities or c[0] in args.cities]
    def worker(city, phase):
        recorder.ctx.phase = phase
        name = city[0]
        row = {"city": name, "phase": phase}
        if recorder.rate_limited.is_set():
            return {**row, "status":"not_attempted_after_rate_limit"}
        try:
            photos = provider._fetch_photos(city)
            status = "ready" if photos else getattr(recorder.ctx, "last_fetch_status", "empty_pool")
            row.update(status=status, pool_size=len(photos) if status != "api_error_payload" else None)
            if status == "api_error_payload":
                row["api_error"] = recorder.ctx.api_error
            if photos and not args.photo_only:
                # Deliberate deterministic probe: one file only, not a city-wide estimate.
                probe = dict(photos[0])
                recorder.ctx.city = name
                start = time.monotonic()
                try:
                    provider._add_credit(probe)
                    row["credit_probe"] = "success" if "author" in probe and "license" in probe else "missing_metadata"
                except Exception as exc:
                    row.update(credit_probe="error", credit_error=str(exc))
                row["credit_seconds"] = time.monotonic()-start
            with recorder.lock:
                if status != "api_error_payload":
                    photos_by_city[name] = photos
        except Exception as exc:
            row.update(status="fetch_error", error_type=type(exc).__name__, error=str(exc))
        return row

    with recorder.patches():
        for phase in ("cold", "retry_unready"):
            if recorder.rate_limited.is_set():
                break
            selected = [c for c in selected_catalog if c[0] not in photos_by_city] if phase == "cold" else [c for c in selected_catalog if c[0] in photos_by_city and not photos_by_city[c[0]]]
            if phase == "retry_unready":
                # Empty pools are cached for six hours in production. Bypass only for this diagnostic retry.
                for city in selected:
                    provider._photo_cache.pop(city[0], None)
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {executor.submit(worker, city, phase): city[0] for city in selected}
                for future in as_completed(futures):
                    row = future.result(); rows.append(row)
                    print(f"{phase}: {row['city']}: {row['status']} pool={row.get('pool_size', '-')} credit={row.get('credit_probe', '-')}", flush=True)
                    write_json(args.output/"survey-progress.json", rows)
                    write_json(args.output/"photos.json", photos_by_city)
        for city in provider.CITIES:
            if city[0] in photos_by_city:
                recorder.ctx.phase = "warm"
                provider._fetch_photos(city)
    cold = [r for r in rows if r["phase"] == "cold"]
    ready = [name for name, photos in photos_by_city.items() if photos]
    result = {"target_city_count": len(provider.CITIES), "cold_ready_city_count": sum(r["status"] == "ready" for r in cold),
              "observed_ready_city_count": len(ready), "observed_coverage_fraction": len(ready)/len(provider.CITIES),
              "unresolved_city_count":len(provider.CITIES)-len(photos_by_city),
              "stopped_on_rate_limit":recorder.rate_limited.is_set(),
              "resumed_city_count":resumed_count, "photo_only":args.photo_only,
              "total_photos": sum(len(p) for p in photos_by_city.values()),
              "pool_sizes": {c[0]: len(photos_by_city[c[0]]) if c[0] in photos_by_city else None for c in provider.CITIES},
              "cold_fetch_seconds": quantiles([r["seconds"] for r in recorder.attempts if r["phase"] == "cold"]),
              "warm_fetch_seconds": quantiles([r["seconds"] for r in recorder.attempts if r["phase"] == "warm"]),
              "api_outcomes": dict(Counter(str(c.get("http_status", c["status"])) for c in recorder.api_calls)),
              "rows": rows,
              "limitation": "One scan and at most one retry per city. Stop globally on HTTP429. Credit probe samples only one photo. Unresolved pool size is null, not zero. Observed coverage is not true availability. Browser image loading unmeasured."}
    write_json(args.output/"photos.json", photos_by_city)
    recorder.save(args.output)
    return result


def offline(args):
    # Reproduce the real get_next_question control flow without network or filesystem state.
    pending = Future()
    with game.app.test_request_context("/play"):
        game.session.update(game_mode="endless", player_id="diagnostic")
        game._prefetches["diagnostic"] = (pending, time.monotonic())
        idx = next(i for i,c in enumerate(game.cities) if c["answer"] == "Chicago")
        with patch.object(game, "start_question_prefetch"), patch.object(game.random, "choice", return_value=idx):
            start = time.monotonic(); first = game.get_next_question(); elapsed = time.monotonic()-start
            retained = "diagnostic" in game._prefetches
            pending.set_result({"answer":"Chicago", "aliases":[], "image_id":"diagnostic-chicago", "is_dynamic":True})
            # A corrected implementation rejects the late conflicting result;
            # any legitimate emergency local-history relaxation is not that bug.
            second = game.get_prefetched_question(consume=True)
        game._prefetches.pop("diagnostic", None)
    catalog = [(str(i), [], 0, 0) for i in range(4)]
    photo = {"title":"File:diagnostic.jpg", "image_url":"diagnostic", "source_url":"diagnostic"}
    cases = []
    for rates in ([.9]*4, [.9,.9,.3,.3]):
        random.seed(7291); counts = Counter()
        def fake_fetch(city):
            return [dict(photo)] if random.random() < rates[int(city[0])] else []
        with patch.object(provider, "CITIES", catalog), patch.object(provider, "_fetch_photos", side_effect=fake_fetch), patch.object(provider, "_add_credit", side_effect=lambda p:p):
            for _ in range(30000):
                q = provider.get_random_question(); counts[q["answer"] if q else "failed"] += 1
        n = 30000-counts["failed"]
        cases.append({"configured_success_rates":rates, "trials":30000, "all_attempts_failed":counts["failed"],
                      "displayed_shares":{str(i):counts[str(i)]/n for i in range(4)}})
    return {"prefetch": {"seconds":elapsed, "fallback_used":not first["is_dynamic"], "pending_retained":retained,
                         "late_result_repeats_city":second is not None and first["answer"] == second["answer"]},
            "city_selection":cases, "limitation":"Controlled reproduction, not measured player incidence."}


def live(args):
    recorder = Recorder()
    original_wait, original_remember = game.get_prefetched_question, game.remember_question
    reason = {"value":"first_question_or_no_prefetch"}
    def wait(wait_seconds=0, consume=False):
        start = time.monotonic()
        result = original_wait(wait_seconds, consume)
        if wait_seconds:
            entry = game._prefetches.get(game.get_player_id())
            state = "ready" if result else ("pending" if entry and not entry[0].done() else "failed_empty_or_missing")
            reason["value"] = state
            recorder.waits.append({"seconds":time.monotonic()-start, "state":state})
        return result
    def remember(city):
        previous = game.session.get("recent_cities", [])
        previous_images = game.session.get("recent_images", [])
        recorder.exposures.append({"city":city["answer"], "image_id":question_identity(city),
                                   "source":"dynamic" if city.get("is_dynamic") else "local",
                                   "fallback_reason":reason["value"] if not city.get("is_dynamic") else None,
                                   "city_history_conflict":city["answer"] in previous,
                                   "image_history_conflict":city.get("image_id") in previous_images})
        return original_remember(city)
    game.app.config.update(TESTING=True)
    client = game.app.test_client()
    step_times = []
    with ExitStack() as stack:
        stack.enter_context(recorder.patches())
        stack.enter_context(patch.object(game, "get_prefetched_question", side_effect=wait))
        stack.enter_context(patch.object(game, "remember_question", side_effect=remember))
        if args.fixture != "none":
            def fixture_fetch(city):
                if args.fixture == "unavailable":
                    raise OSError("controlled_unavailable_no_network")
                return [{"title":f"File:fixture-{city[0]}-{i}.jpg", "image_url":"fixture", "source_url":"fixture"} for i in range(20)]
            def fixture_credit(photo):
                time.sleep(args.fixture_delay)
                return photo
            stack.enter_context(patch.object(provider, "_fetch_photos", side_effect=fixture_fetch))
            stack.enter_context(patch.object(provider, "_add_credit", side_effect=fixture_credit))
        for t in range(args.questions):
            reason["value"] = "first_question_or_no_prefetch"
            start = time.monotonic()
            if t == 0 or (args.game_mode == "challenge" and t % 10 == 0):
                response = client.post("/reset", data={"mode":args.game_mode, "answer_mode":"text"}, follow_redirects=True)
            else:
                response = client.get("/next")
            if response.status_code != 200:
                raise RuntimeError(f"Question request failed with HTTP {response.status_code}")
            step_times.append(time.monotonic()-start)
            if args.think_seconds:
                time.sleep(args.think_seconds)
            with client.session_transaction() as session:
                q = dict(session["current_question"])
            response = client.post("/check", data={"question_id":q["question_id"], "guess":q["answer"]})
            if response.status_code != 200:
                raise RuntimeError(f"Answer request failed with HTTP {response.status_code}")
            if (t+1) % 10 == 0:
                print(f"live: {t+1}/{args.questions} questions; local={sum(e['source']=='local' for e in recorder.exposures)}", flush=True)
        # Drain the four-worker diagnostic process so traces include all task outcomes.
        game._prefetch_executor.shutdown(wait=True)
    recorder.save(args.output)
    local_questions = [e for e in recorder.exposures if e["source"] == "local"]
    return {"game_mode":args.game_mode, "think_seconds":args.think_seconds,
            "fixture":args.fixture, "fixture_delay":args.fixture_delay,
            "intended_display_metrics":metrics(recorder.exposures),
            "local_fraction":len(local_questions)/len(recorder.exposures),
            "pending_fallback_fraction":sum(e["fallback_reason"] == "pending" for e in local_questions)/len(recorder.exposures),
            "fallback_reasons":dict(Counter(e["fallback_reason"] for e in local_questions)),
            "dynamic_history_conflicts":sum(e["source"] == "dynamic" and (e["city_history_conflict"] or e["image_history_conflict"]) for e in recorder.exposures),
            "next_page_seconds":quantiles(step_times), "prefetch_task_seconds":quantiles([t["seconds"] for t in recorder.tasks]),
            "city_fetch_status":dict(Counter(a["status"] for a in recorder.attempts)),
            "api_outcomes":dict(Counter(str(c.get("http_status", c["status"])) for c in recorder.api_calls)),
            "limitation":"Scripted correct answers and fixed think time; intended server-side displays only. Fixture mode makes NO network calls; its values are controlled outcomes, not forecasts of real-player incidence."}


def replay(args):
    pools = json.loads(args.snapshot.read_text(encoding="utf-8"))
    missing = [c[0] for c in provider.CITIES if c[0] not in pools]
    if missing:
        raise ValueError(f"Incomplete snapshot: {len(missing)} cities unresolved. Refusing to turn unknown cities into empty pools.")
    cases = []
    for seed in range(args.runs):
        random.seed(seed); exposures = []; recent_i = []; recent_c = []
        with patch.object(provider, "_fetch_photos", side_effect=lambda c:pools.get(c[0], [])), patch.object(provider, "_add_credit", side_effect=lambda p:p):
            for _ in range(args.questions):
                q = provider.get_random_question(recent_i, recent_c)
                if q:
                    exposures.append({"city":q["answer"], "image_id":q["image_id"]})
                    recent_i = (recent_i+[q["image_id"]])[-20:]
                    recent_c = (recent_c+[q["answer"]])[-20:]
        row = metrics(exposures); row["draw_failures"] = args.questions-len(exposures); cases.append(row)
    numeric_keys = [k for k,v in cases[0].items() if isinstance(v,(int,float)) or v is None]
    aggregate = {k:quantiles([row[k] for row in cases if row[k] is not None]) for k in numeric_keys}
    return {"runs":args.runs, "attempted_draws_per_run":args.questions, "aggregate":aggregate, "runs_detail":cases,
            "limitation":"Snapshot replay isolates photo supply and recent exclusion. Credits assumed available; no network, timing, local fallback, or actual image-load behavior. Failed survey cities are not assumed permanently unavailable."}


def api_probe(args):
    """Read-only alternate query experiment for API-wide thumbnail errors."""
    recorder = Recorder(interval=args.interval)
    rows = []
    selected = [c for c in provider.CITIES if c[0] in (args.cities or ["Cairo", "Dublin"])]
    for city in selected:
        if recorder.rate_limited.is_set():
            break
        recorder.ctx.city = city[0]
        data = recorder.api(provider._api_get, {
            "action":"query", "generator":"geosearch", "ggsprimary":"all", "ggsnamespace":6,
            "ggsradius":5000, "ggslimit":100, "ggscoord":f"{city[2]}|{city[3]}",
            "prop":"categories|imageinfo", "cllimit":"max", "clshow":"!hidden", "iiprop":"url|size|mime",
        })
        pages = data.get("query", {}).get("pages", [])
        mime_counts = Counter()
        candidates = []
        for page in pages:
            info = (page.get("imageinfo") or [{}])[0]
            mime_counts[info.get("mime", "missing")] += 1
            if info.get("mime") not in {"image/jpeg", "image/png", "image/webp"}:
                continue
            width, height = info.get("width",0), info.get("height",0)
            if width*height < provider.MIN_PHOTO_PIXELS:
                continue
            categories = [c.get("title","") for c in page.get("categories",[])]
            if provider._photo_score(city, page.get("title",""), categories, width, height) >= provider.MIN_PHOTO_SCORE:
                candidates.append(page["title"])
        row = {"city":city[0], "metadata_query_error":data.get("error"), "returned_files":len(pages),
               "mime_counts":dict(mime_counts), "quality_candidates_before_family_dedup":len(candidates)}
        if candidates and not recorder.rate_limited.is_set():
            thumb = recorder.api(provider._api_get, {"action":"query", "titles":candidates[0],
                                  "prop":"imageinfo", "iiprop":"url|size|mime", "iiurlwidth":1280})
            info = ((thumb.get("query",{}).get("pages",[{}])[0]).get("imageinfo") or [{}])[0]
            row.update(single_supported_image=candidates[0], thumbnail_query_error=thumb.get("error"),
                       supported_thumbnail_available=bool(info.get("thumburl")))
        rows.append(row)
        print(f"api-probe: {city[0]}: files={len(pages)}, candidates={len(candidates)}, thumbnail={row.get('supported_thumbnail_available')}",flush=True)
    recorder.save(args.output)
    return {"rows":rows, "limitation":"Diagnostic alternate requests only; no production changes. Candidate count precedes family deduplication. One supported thumbnail checked per city, no browser loading measured."}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("offline","survey","live","replay","api_probe"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--interval", type=float, default=3)
    parser.add_argument("--questions", type=int, default=100)
    parser.add_argument("--think-seconds", type=float, default=2)
    parser.add_argument("--game-mode", choices=("endless","challenge"), default="endless")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--fixture", choices=("none","ready","unavailable"), default="none")
    parser.add_argument("--fixture-delay", type=float, default=.6)
    parser.add_argument("--resume-snapshot", type=Path)
    parser.add_argument("--photo-only", action="store_true")
    parser.add_argument("--cities", nargs="+")
    args = parser.parse_args()
    if args.questions < 1 or args.runs < 1 or args.think_seconds < 0 or args.interval < 0 or args.fixture_delay < 0 or not 1 <= args.workers <= 4:
        parser.error("Counts must be positive; durations nonnegative; workers between 1 and 4.")
    if args.mode == "replay" and not args.snapshot:
        parser.error("replay requires --snapshot")
    args.output.mkdir(parents=True, exist_ok=True)
    before = source_digest()
    result = globals()[args.mode](args)
    result["metadata"] = {"started_source_sha256":before, "production_sources_unchanged":before == source_digest(),
                          "finished_utc":datetime.now(timezone.utc).isoformat(), "mode":args.mode,
                          "photo_history_length":game.RECENT_HISTORY_LENGTH, "prefetch_wait_seconds":game.PREFETCH_WAIT_SECONDS}
    result["metadata"]["photo_history_length"] = getattr(game, "RECENT_IMAGE_HISTORY_LENGTH", game.RECENT_HISTORY_LENGTH)
    result["metadata"]["city_history_length"] = game.RECENT_HISTORY_LENGTH
    write_json(args.output/"summary.json", result)
    print(f"Saved: {args.output/'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
