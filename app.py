from pathlib import Path
from functools import partial
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for

from city_catalog import CITIES, CITY_PROFILES
from local_photos import COMMONS as commons, question_image_context
from city_provider import get_city_intro, get_random_question, get_cached_question as _get_cached_question
from city_provider import has_unseen_cached, effective_city_exclusions, set_local_image_checker
from city_provider import prepared_question_snapshot
from image_cache import ThumbnailCache
from photo_rules import photo_id as _photo_id
from city_choices import generate_choices
from game_features import question_hints, today
from question_prefetch import QuestionPrefetch
from dynamic_pool import DynamicQuestionPool
from photo_rotation import PhotoRotation
import photo_statistics
import daily_progress
from photo_feedback import FEEDBACK_REASONS, record_feedback

app = Flask(__name__)
app.secret_key = os.environ.get("CITY_GUESSER_SECRET", "city-game-development-secret")
app.config["PHOTO_FEEDBACK_PATH"] = Path(app.instance_path) / "photo-feedback.sqlite3"

PREFETCH_WAIT_SECONDS = 0.3
PREFETCH_TTL_SECONDS = 30 * 60
CHALLENGE_LENGTH = 10
RECENT_HISTORY_LENGTH = 20
RECENT_IMAGE_HISTORY_LENGTH = 100
GAME_MODES = {"challenge", "endless", "daily"}
ANSWER_MODES = {"text", "choice"}
question_prefetch = QuestionPrefetch(ttl_seconds=PREFETCH_TTL_SECONDS)
thumbnail_cache = ThumbnailCache(Path(app.instance_path) / "thumbnails", ttl_seconds=30 * 24 * 3600)
set_local_image_checker(lambda question: bool(
    not app.testing and thumbnail_cache.find(thumbnail_cache.key(question.get("image_url", "")))
))


def prepare_question_image(question):
    if not app.testing:
        try:
            thumbnail_cache.enqueue(question)
        except Exception:
            app.logger.debug("Could not schedule thumbnail cache", exc_info=True)


def fetch_question_with_image(recent_images, recent_cities, *, seen_images=()):
    question = get_random_question(recent_images, recent_cities, seen_images=seen_images)
    if question:
        prepare_question_image(question)
    return question


dynamic_pool = DynamicQuestionPool(Path(app.instance_path) / "prepared-questions.json",
                                  prepare_image=prepare_question_image)
question_rotation = PhotoRotation()
thumbnail_warm_at = 0


@app.before_request
def replenish_dynamic_questions():
    global thumbnail_warm_at
    if not app.testing and request.endpoint not in {"static", "thumbnail", "prefetch_image", "photo_feedback"}:
        dynamic_pool.start()
        if time.monotonic() >= thumbnail_warm_at:
            thumbnail_warm_at = time.monotonic() + 30
            for entry in prepared_question_snapshot():
                prepare_question_image(entry["question"])

city_aliases = {name: aliases for name, aliases, _lat, _lon in CITIES}
city_coordinates = {name: (lat, lon) for name, _aliases, lat, lon in CITIES}
city_flag_lookup = {
    label.casefold(): {
        "name": name,
        "country": CITY_PROFILES[name]["country"],
        "flag_url": f"/static/flags/{CITY_PROFILES[name]['flag']}.svg",
    }
    for name, aliases, _lat, _lon in CITIES
    for label in [name, *aliases]
}


def remember_question(city):
    city = city.copy()
    city["question_id"] = secrets.token_urlsafe(8)
    if get_answer_mode() == "choice":
        city["choices"] = city.get("choices") or generate_choices(city["answer"])
    recent_images = list(session.get("recent_images", []))
    if city.get("image_id"):
        recent_images.append(city["image_id"])
        session["recent_images"] = recent_images[-RECENT_IMAGE_HISTORY_LENGTH:]
    recent_cities = list(session.get("recent_cities", []))
    recent_cities.append(city["answer"])
    session["recent_cities"] = recent_cities[-RECENT_HISTORY_LENGTH:]
    session["current_question"] = city
    question_rotation.record(get_player_id(), city["answer"], city.get("image_id"))
    photo_statistics.record(city["answer"], "display", image_id=city.get("image_id"))
    session["question_resolved"] = False
    for key in ("question_result", "question_points", "question_revealed", "selected_choice", "hint_level"):
        session.pop(key, None)
    return city


def get_new_question():
    return get_next_question()


def get_player_id():
    if "player_id" not in session:
        session["player_id"] = secrets.token_urlsafe(12)
    return session["player_id"]


def get_cached_question(excluded_images=(), excluded_cities=()):
    return _get_cached_question(
        excluded_images, excluded_cities, seen_images=question_rotation.seen(get_player_id()),
        relax_cities=True,
    )


def get_current_question():
    city = session.get("current_question")
    if city and "question_id" not in city:
        city = city.copy()
        city["question_id"] = secrets.token_urlsafe(8)
        session["current_question"] = city
        session["question_resolved"] = False
    return city


def get_game_stats():
    defaults = {
        "score": 0,
        "streak": 0,
        "best_streak": 0,
        "answered": 0,
        "correct": 0,
        "assisted": 0,
    }
    stats = {**defaults, **session.get("game_stats", {})}
    if stats != session.get("game_stats"):
        session["game_stats"] = stats
    return stats


def get_game_mode():
    mode = session.get("game_mode", "challenge")
    return mode if mode in GAME_MODES else "challenge"


def get_answer_mode():
    mode = session.get("answer_mode", "text")
    return mode if mode in ANSWER_MODES else "text"


def is_challenge_complete(stats=None):
    stats = stats or get_game_stats()
    return get_game_mode() != "endless" and stats["answered"] >= CHALLENGE_LENGTH


def get_question_number(stats=None):
    stats = stats or get_game_stats()
    number = stats["answered"] if session.get("question_resolved") else stats["answered"] + 1
    if get_game_mode() != "endless":
        return min(max(number, 1), CHALLENGE_LENGTH)
    return max(number, 1)


def get_game_summary(stats=None):
    stats = stats or get_game_stats()
    answered = stats["answered"]
    accuracy = round(stats["correct"] * 100 / answered) if answered else 0
    return {
        "score": stats["score"],
        "correct": stats["correct"],
        "answered": answered,
        "accuracy": accuracy,
        "best_streak": stats["best_streak"],
        "assisted": stats["assisted"],
        "unassisted": stats["correct"] - stats["assisted"],
    }


def record_answer(is_correct):
    if get_answer_mode() == "text":
        level = max(session.get("hint_level", 0), min(2, max(0, request.form.get("hint_level", 0, type=int))))
        if level:
            session["hint_level"] = level
    stats = get_game_stats().copy()
    stats["answered"] += 1
    points = 0
    if is_correct:
        points = 100 + min(stats["streak"] * 20, 100)
        stats["streak"] += 1
        stats["best_streak"] = max(stats["best_streak"], stats["streak"])
        stats["score"] += points
        stats["correct"] += 1
        stats["assisted"] += bool(session.get("hint_level"))
    else:
        stats["streak"] = 0
    session["game_stats"] = stats
    return points


def save_question_outcome(result=None, points=0, revealed=False):
    session["question_resolved"] = True
    session["question_result"] = result
    session["question_points"] = points
    session["question_revealed"] = revealed
    if is_challenge_complete() and "completion" not in session:
        session["completion"] = {
            **get_game_summary(), "mode": get_game_mode(), "answer_mode": get_answer_mode(),
            "run_id": session.get("daily_run_id") or secrets.token_urlsafe(12),
            "date": session.get("daily_date", today()),
        }


def start_question_prefetch():
    if is_challenge_complete() or (
        get_game_mode() != "endless" and get_question_number() >= CHALLENGE_LENGTH
        and get_current_question() and not session.get("question_resolved")
    ):
        return
    question_prefetch.start(
        get_player_id(), partial(fetch_question_with_image, seen_images=question_rotation.seen(get_player_id())),
        tuple(session.get("recent_images", [])), tuple(session.get("recent_cities", [])),
        question_conflicts_with_history,
    )


def get_prefetched_question(wait_seconds=0, consume=False):
    return question_prefetch.get(
        get_player_id(), question_conflicts_with_history,
        wait_seconds=wait_seconds, consume=consume,
    )


def question_conflicts_with_history(city):
    recent_images = session.get("recent_images", [])
    if city["answer"] in effective_city_exclusions(recent_images, session.get("recent_cities", [])) or city.get("image_id") in recent_images:
        return True
    seen = question_rotation.seen(get_player_id())
    return (city.get("image_id") in seen
            and has_unseen_cached(city["answer"], recent_images, seen))


def get_next_question():
    # Keep pending work alive; an empty dynamic supply never creates a fixed question.
    city = get_prefetched_question(consume=True)
    if city is None:
        city = get_cached_question(session.get("recent_images", []), session.get("recent_cities", []))
    if city is None:
        start_question_prefetch()
        city = get_prefetched_question(PREFETCH_WAIT_SECONDS, consume=True)
    if city is None:
        city = get_cached_question(session.get("recent_images", []), session.get("recent_cities", []))
    if city is None:
        return None
    city = remember_question(city)
    start_question_prefetch()
    return city


def render_question_loading():
    retry_url = url_for("next_question" if get_current_question() else "play")
    return render_template(
        "question-loading.html", retry_url=retry_url,
        stats=get_game_stats(), game_mode=get_game_mode(),
        answer_mode=get_answer_mode(), daily_date=session.get("daily_date", today()),
    ), 503, {"Retry-After": "5", "Cache-Control": "no-store"}


def get_daily_resume(runs, day):
    """Validate the requested day and migrate legacy progress before clearing a session."""
    if day > today():
        abort(400, description="Daily challenge date is unavailable.")
    saved = daily_progress.latest_snapshot(
        day, (runs.get(day), request.form.get("daily_token")), app.secret_key,
    )
    if saved is None and day != today():
        abort(400, description="No saved progress exists for this daily challenge.")
    # Older browser snapshots stored an index into the former daily manifest.
    if saved and saved.get("question_id") and not saved.get("current_question"):
        directory = Path(app.config.get("DAILY_DIRECTORY", Path(app.instance_path) / "daily"))
        path = directory / f"{day}.json"
        if not path.is_file():
            abort(409, description="The original daily question file is unavailable; saved progress has not been reset.")
        saved["current_question"] = json.loads(path.read_text(encoding="utf-8"))[saved["question_index"]]
        saved["current_question"]["question_id"] = saved["question_id"]
    return saved


def preserved_history(saved=None):
    """Preserve bounded ordinary history and merge a resumed daily run's seen questions."""
    histories = {}
    for key, limit in (("recent_images", RECENT_IMAGE_HISTORY_LENGTH), ("recent_cities", RECENT_HISTORY_LENGTH)):
        history = list(session.get(key, []))[-limit:]
        if saved:
            history.extend(value for value in saved.get(key, []) if value not in history)
        if history:
            histories[key] = history[-limit:]
    return histories


def start_new_game(mode="challenge", answer_mode=None):
    mode = mode if mode in GAME_MODES else "challenge"
    answer_mode = get_answer_mode() if answer_mode is None else answer_mode
    answer_mode = answer_mode if answer_mode in ANSWER_MODES else "text"
    if get_game_mode() == "daily" and session.get("daily_date"):
        get_game_stats()
        daily_progress.capture(session, app.secret_key)
    runs = dict(session.get("daily_runs", {}))
    day = request.form.get("daily_date", today()) if mode == "daily" else today()
    saved = get_daily_resume(runs, day) if mode == "daily" else None
    player_id = session.get("player_id")
    if player_id:
        question_prefetch.cancel(player_id)
    histories = preserved_history(saved)
    session.clear()
    if player_id:
        session["player_id"] = player_id
    session["game_mode"] = mode
    session["answer_mode"] = answer_mode
    if runs:
        session["daily_runs"] = runs
    if mode == "daily":
        session["daily_date"] = day
        if saved:
            session.update({key: saved[key] for key in daily_progress.FIELDS if key in saved})
        get_game_stats()
    session.update(histories)
    if mode == "daily":
        daily_progress.capture(session, app.secret_key)


@app.before_request
def ensure_game_mode():
    if session.get("game_mode") not in GAME_MODES:
        start_new_game("challenge")
    # Preserve existing players' local-photo history when moving to file identities.
    history = session.get("recent_images", [])
    normalized = [
        _photo_id("File:" + commons[image_id[6:]])
        if isinstance(image_id, str) and image_id.startswith("local:") and image_id[6:] in commons
        else image_id
        for image_id in history
    ]
    if normalized != history:
        session["recent_images"] = normalized


@app.context_processor
def daily_run_context():
    if get_game_mode() == "daily" and session.get("daily_date"):
        get_game_stats()
        daily_progress.capture(session, app.secret_key)
    runs = [daily_progress.describe(token, app.secret_key) for token in session.get("daily_runs", {}).values()]
    for run in runs:
        if run:
            run["active"] = get_game_mode() == "daily" and run["date"] == session.get("daily_date")
    return {"daily_runs": [run for run in runs if run]}


def get_map_url(city):
    coordinates = city_coordinates.get(city["answer"])
    if not coordinates:
        return None
    latitude, longitude = coordinates
    return (
        "https://www.openstreetmap.org/"
        f"?mlat={latitude}&mlon={longitude}#map=11/{latitude}/{longitude}"
    )


def render_question(
    city, result=None, preload_url=None, revealed_answer=None, city_intro=None,
    round_points=None, game_summary=False, sound_events=None,
):
    stats = get_game_stats()
    context = {
        "result": result,
        "preload_url": preload_url,
        "revealed_answer": revealed_answer,
        "city_intro": city_intro,
        "round_points": round_points,
        "stats": stats,
        "question_id": city["question_id"],
        "game_mode": get_game_mode(),
        "answer_mode": get_answer_mode(),
        "city_flag_lookup": city_flag_lookup,
        "choices": [
            {"name": name, **CITY_PROFILES[name]}
            for name in city.get("choices", [])
        ],
        "choice_answer": city["answer"] if session.get("question_resolved") else None,
        "selected_choice": session.get("selected_choice"),
        "question_number": get_question_number(stats),
        "question_total": CHALLENGE_LENGTH,
        "challenge_complete": is_challenge_complete(stats),
        "game_summary": game_summary,
        "summary": get_game_summary(stats) if game_summary else None,
        "map_url": get_map_url(city),
        "hints": [clue.split(": ", 1)[-1] for clue in question_hints(city["answer"])] if get_answer_mode() == "text" else [],
        "hint_level": session.get("hint_level", 0),
        "question_resolved": session.get("question_resolved", False),
        "daily_date": session.get("daily_date", today()),
        "completion": session.get("completion"),
        "sound_events": sound_events or [],
    }
    image_context = question_image_context(city, app.static_folder)
    original_url = image_context["image_url"]
    image_context["image_url"] = browser_image_url(city, original_url)
    context["remote_image_url"] = original_url if image_context["image_url"] != original_url else None
    context["prefetch_image_url"] = (url_for("prefetch_image", question_id=city["question_id"])
                                      if not is_challenge_complete() else None)
    return render_template("index.html", **image_context, **context)


def browser_image_url(question, original_url=None):
    original_url = original_url or question.get("image_url")
    if question.get("is_dynamic") and original_url:
        key = thumbnail_cache.key(original_url)
        if thumbnail_cache.find(key):
            return url_for("thumbnail", key=key)
        prepare_question_image(question)
    return original_url


@app.route("/photos/<key>")
def thumbnail(key):
    path = thumbnail_cache.find(key)
    if path is None:
        abort(404)
    response = send_file(path, conditional=True, max_age=24 * 3600)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/photo-feedback", methods=["POST"])
def photo_feedback():
    # Read the existing session directly: reporting never creates or advances a question.
    city = session.get("current_question")
    question_id = request.form.get("question_id", "")
    reason = request.form.get("reason", "")
    if not city or not question_id or question_id != city.get("question_id") or not city.get("image_id"):
        response = jsonify(ok=False, message="This photo has changed. Please refresh and try again.")
        response.status_code = 400
    elif reason not in FEEDBACK_REASONS:
        response = jsonify(ok=False, message="Please choose a feedback reason.")
        response.status_code = 400
    else:
        # Store an opaque reporter identity, not the gameplay session or player ID.
        identity = session.get("player_id") or question_id
        secret = app.secret_key.encode("utf-8") if isinstance(app.secret_key, str) else app.secret_key
        reporter = hmac.new(secret, ("photo-feedback:" + identity).encode("utf-8"), hashlib.sha256).hexdigest()
        photo = city.copy()
        photo["image_url"] = city.get("image_url") or question_image_context(city, app.static_folder)["image_url"]
        try:
            record_feedback(app.config["PHOTO_FEEDBACK_PATH"], photo, reporter, reason)
        except (OSError, sqlite3.Error):
            app.logger.warning("Could not save photo feedback", exc_info=True)
            response = jsonify(ok=False, message="Feedback is temporarily unavailable. Please try again.")
            response.status_code = 503
        else:
            response = jsonify(ok=True, message="Feedback recorded. Thank you.")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/prefetch-image")
def prefetch_image():
    # Peek only: no drawing, consumption, new tasks, or history advancement.
    current = session.get("current_question")
    payload = {}
    complete = (session.get("game_mode") != "endless"
                and session.get("game_stats", {}).get("answered", 0) >= CHALLENGE_LENGTH)
    if (current and session.get("player_id")
            and request.args.get("question_id") == current.get("question_id") and not complete):
        following = get_prefetched_question()
        if following:
            payload["image_url"] = browser_image_url(following)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


def render_saved_outcome(city, preload_url=None):
    if session.get("question_revealed"):
        return render_question(
            city,
            preload_url=preload_url,
            revealed_answer=city["answer"],
            city_intro=get_city_intro(city),
            round_points=0,
        )
    return render_question(
        city,
        result=session.get("question_result"),
        preload_url=preload_url,
        round_points=session.get("question_points", 0),
    )


@app.route("/")
def home():
    has_game = bool(session.get("current_question"))
    complete = has_game and is_challenge_complete()
    return render_template(
        "home.html", city_count=len(CITIES), has_game=has_game,
        challenge_complete=complete,
        resume_url=url_for("results" if complete else "play"),
        game_mode=get_game_mode(), answer_mode=get_answer_mode(),
        stats=get_game_stats() if has_game else None,
        daily_date=today(),
    )


@app.route("/play")
def play():
    if is_challenge_complete():
        return redirect(url_for("results"))
    city = get_current_question() or get_new_question()
    if city is None:
        return render_question_loading()
    start_question_prefetch()
    if session.get("question_resolved"):
        return render_saved_outcome(city)
    return render_question(city)


@app.route("/check", methods=["POST"])
def check():
    city, response = submission_question()
    if response is not None:
        return response
    guess = request.form.get("guess", "")
    if get_answer_mode() == "choice":
        if guess not in city.get("choices", []):
            return render_question(city), 400
        session["selected_choice"] = guess
    accepted_answers = [city["answer"], *city.get("aliases", [])]
    is_correct = guess.strip().casefold() in {
        answer.casefold() for answer in accepted_answers
    }
    points = record_answer(is_correct)
    result = "✅ Correct!" if is_correct else f"❌ Wrong! Answer: {city['answer']}"
    save_question_outcome(result=result, points=points)
    if not is_challenge_complete():
        start_question_prefetch()
    next_city = get_prefetched_question() if not is_challenge_complete() else None
    preload_url = browser_image_url(next_city) if next_city else None
    sound_events = [{"type": "correct" if is_correct else "wrong",
                     "id": f"answer:{city['question_id']}"}]
    if is_correct and get_game_stats()["streak"] in {3, 5, 10}:
        sound_events.append({"type": "streak", "id": f"streak:{city['question_id']}"})
    return render_question(city, result, preload_url, round_points=points,
                           sound_events=sound_events)


def submission_question():
    """Share missing/stale/resolved submission handling for answers and reveals."""
    city = get_current_question()
    if not city:
        city = get_new_question()
        if city is None:
            return None, render_question_loading()
        start_question_prefetch()
        return city, render_question(city)
    if request.form.get("question_id") != city["question_id"] or session.get("question_resolved"):
        response = render_saved_outcome(city) if session.get("question_resolved") else render_question(city)
        return city, response
    return city, None


@app.route("/reveal", methods=["POST"])
def reveal_answer():
    if get_answer_mode() != "text":
        abort(405)
    city, response = submission_question()
    if response is not None:
        return response

    record_answer(False)
    save_question_outcome(revealed=True)
    if not is_challenge_complete():
        start_question_prefetch()
    intro = get_city_intro(city)
    next_city = get_prefetched_question() if not is_challenge_complete() else None
    preload_url = browser_image_url(next_city) if next_city else None
    return render_question(
        city,
        preload_url=preload_url,
        revealed_answer=city["answer"],
        city_intro=intro,
        round_points=0,
    )


@app.route("/next")
def next_question():
    if is_challenge_complete():
        return redirect(url_for("results"))
    if get_current_question() and not session.get("question_resolved"):
        return redirect(url_for("play"))
    city = get_next_question()
    return render_question(city) if city is not None else render_question_loading()


@app.route("/results")
def results():
    if not is_challenge_complete():
        return redirect(url_for("play"))
    city = get_current_question()
    if not city:
        return redirect(url_for("play"))
    completion = session.get("completion")
    sound_events = ([{"type": "levelComplete", "id": f"complete:{completion['run_id']}"}]
                    if completion else [])
    return render_question(city, game_summary=True, sound_events=sound_events)


@app.route("/reset", methods=["POST"])
def reset_game():
    start_new_game(
        request.form.get("mode", get_game_mode()),
        request.form.get("answer_mode"),
    )
    return redirect(url_for("play"))


if __name__ == "__main__":
    app.run(debug=True)
