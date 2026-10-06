from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from flask import Flask, abort, redirect, render_template, request, session, url_for
from threading import Lock
from urllib.parse import quote
from pathlib import Path
import os
import json
import random
import secrets
import time

from city_provider import CITIES, get_city_intro, get_random_question, get_cached_question, _photo_id
from city_choices import CITY_PROFILES, generate_choices
from game_features import question_hints, today
import daily_progress

app = Flask(__name__)
app.secret_key = os.environ.get("CITY_GUESSER_SECRET", "city-game-development-secret")

PREFETCH_WAIT_SECONDS = 0.3
PREFETCH_TTL_SECONDS = 30 * 60
CHALLENGE_LENGTH = 10
RECENT_HISTORY_LENGTH = 20
RECENT_IMAGE_HISTORY_LENGTH = 100
GAME_MODES = {"challenge", "endless", "daily"}
ANSWER_MODES = {"text", "choice"}
_prefetch_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="city-question")
_prefetches = {}
_prefetch_lock = Lock()

# Wikimedia Commons file names. See static/images/SOURCES.md for credits.
commons = {
    "Chicago": "Chicago Skyline - Dusk.JPG",
    "London": "London Skyline from London Bridge at dusk.jpg",
    "Tokyo": "Tokyo Skyline.jpg",
    "Paris": "The Eiffel Tower in Paris.jpg",
    "New York": "NYC skyline empire.jpg",
    "San Francisco": "GoldenGateBridge.jpg",
    "Rome": "Colosseum romanum.jpg",
    "Venice": "Rialto Bridge, Venice Italy.jpg",
    "Barcelona": "Panoramic View of the Basílica de la Sagrada Família.jpg",
    "Berlin": "The Brandenburg Gate.jpg",
    "Amsterdam": "Canal houses and Oude Kerk at blue hour with water reflection in Damrak Amsterdam Netherlands.jpg",
    "Sydney": "The Sydney Opera House. Australia.jpg",
    "Singapore": "Singapore Marina Bay Sands Skyline.jpg",
    "Hong Kong": "Victoria Harbour skyline, Hong Kong (2008).jpg",
    "Dubai": "Burj Khalifa Image.jpg",
    "Shanghai": "ShanghaiPearlTower.jpg",
    "Beijing": "A panoramic view of the Forbidden City.jpg",
    "Toronto": "CN Tower Toronto. (48938595411).jpg",
    "Rio de Janeiro": "Christ-Redeemer-Rio-de-Janeiro.jpg",
    "Istanbul": "Exterior of Hagia Sophia-.jpg",
}

credits = {
    "Chicago": ("Alanthebox", "CC0 1.0"),
    "London": ("Donnchadh H", "CC BY 2.0"),
    "Tokyo": ("Ningyou", "Public domain"),
    "Paris": ("Jeong seolah", "CC0 1.0"),
    "New York": ("Matthew Wiebe", "CC0 1.0"),
    "San Francisco": ("Peter Craig", "Public domain"),
    "Rome": ("maiterozas", "CC0 1.0"),
    "Venice": ("Peter Glyn", "CC0 1.0"),
    "Barcelona": ("Karanchawla30", "CC BY-SA 4.0"),
    "Berlin": ("Nirmal Dulal", "CC BY-SA 4.0"),
    "Amsterdam": ("Basile Morin", "CC BY-SA 4.0"),
    "Sydney": ("Bernard Spragg. NZ", "CC0 1.0"),
    "Singapore": ("Aerosecure hub official", "CC BY-SA 4.0"),
    "Hong Kong": ("ImMrDrake", "CC BY 3.0"),
    "Dubai": ("Meandmybrix", "CC0 1.0"),
    "Shanghai": ("Robpics69", "Public domain"),
    "Beijing": ("Wuhuanqi", "CC BY-SA 4.0"),
    "Toronto": ("Bernard Spragg. NZ", "CC0 1.0"),
    "Rio de Janeiro": ("acediscovery", "CC BY 4.0"),
    "Istanbul": ("Yair Haklai", "CC BY-SA 4.0"),
}

cities = [
    {"image": f"{name.replace(' ', '')}.svg", "answer": name, "commons": filename}
    for name, filename in commons.items()
]
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


def get_local_question():
    if "remaining" not in session or not session["remaining"]:
        session["remaining"] = list(range(len(cities)))

    recent_cities = set(session.get("recent_cities", []))
    remaining = list(session["remaining"])
    eligible = [idx for idx in remaining if cities[idx]["answer"] not in recent_cities]
    if not eligible:
        eligible = remaining
    idx = random.choice(eligible)
    remaining.remove(idx)
    session["remaining"] = remaining
    city = cities[idx].copy()
    city["aliases"] = city_aliases.get(city["answer"], [])
    city["image_id"] = _photo_id("File:" + city["commons"])
    city["is_dynamic"] = False
    return city


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
    session["question_resolved"] = False
    session.pop("question_result", None)
    session.pop("question_points", None)
    session.pop("question_revealed", None)
    session.pop("selected_choice", None)
    session.pop("hint_level", None)
    return city


def get_new_question():
    recent_images = session.get("recent_images", [])
    recent_cities = session.get("recent_cities", [])
    city = (get_cached_question(recent_images, recent_cities)
            or get_random_question(recent_images, recent_cities) or get_local_question())
    return remember_question(city)


def get_player_id():
    if "player_id" not in session:
        session["player_id"] = secrets.token_urlsafe(12)
    return session["player_id"]


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
    ):
        return
    player_id = get_player_id()
    recent_images = tuple(session.get("recent_images", []))
    recent_cities = tuple(session.get("recent_cities", []))
    now = time.monotonic()

    with _prefetch_lock:
        for stale_id, (future, created_at) in list(_prefetches.items()):
            if now - created_at > PREFETCH_TTL_SECONDS:
                future.cancel()
                _prefetches.pop(stale_id, None)

        existing = _prefetches.get(player_id)
        if existing:
            future = existing[0]
            if not future.done():
                return
            try:
                city = future.result()
                if city is not None and not question_conflicts_with_history(city):
                    return
            except Exception:
                pass
            _prefetches.pop(player_id, None)

        future = _prefetch_executor.submit(get_random_question, recent_images, recent_cities)
        _prefetches[player_id] = (future, now)


def get_prefetched_question(wait_seconds=0, consume=False):
    player_id = get_player_id()
    with _prefetch_lock:
        entry = _prefetches.get(player_id)
    if not entry:
        return None

    future = entry[0]
    try:
        city = future.result(timeout=wait_seconds)
    except FutureTimeout:
        return None
    except Exception:
        city = None

    if city is not None and question_conflicts_with_history(city):
        city = None

    if consume or city is None:
        with _prefetch_lock:
            if _prefetches.get(player_id) == entry:
                _prefetches.pop(player_id, None)
    return city


def question_conflicts_with_history(city):
    return (city["answer"] in session.get("recent_cities", [])
            or city.get("image_id") in session.get("recent_images", []))


def get_next_question():
    # Use prepared photos before waiting or falling back to the fixed local pool.
    city = get_prefetched_question(consume=True)
    if city is None:
        city = get_cached_question(session.get("recent_images", []), session.get("recent_cities", []))
    if city is None:
        start_question_prefetch()
        city = get_prefetched_question(PREFETCH_WAIT_SECONDS, consume=True)
    if city is None:
        city = get_cached_question(session.get("recent_images", []), session.get("recent_cities", []))
    city = remember_question(city or get_local_question())
    start_question_prefetch()
    return city


def start_new_game(mode="challenge", answer_mode=None):
    mode = mode if mode in GAME_MODES else "challenge"
    answer_mode = get_answer_mode() if answer_mode is None else answer_mode
    answer_mode = answer_mode if answer_mode in ANSWER_MODES else "text"
    if get_game_mode() == "daily" and session.get("daily_date"):
        get_game_stats()
        daily_progress.capture(session, app.secret_key)
    runs = dict(session.get("daily_runs", {}))
    day = today()
    saved = None
    if mode == "daily":
        day = request.form.get("daily_date", day)
        if day > today():
            abort(400, description="Daily challenge date is unavailable.")
        for token in (runs.get(day), request.form.get("daily_token")):
            candidate = daily_progress.read(token, app.secret_key) if token else None
            if candidate and candidate["daily_date"] == day and (
                    saved is None or candidate["daily_run_id"] != saved["daily_run_id"]
                    or daily_progress.position(candidate) > daily_progress.position(saved)):
                saved = candidate
        if saved is None and day != today():
            abort(400, description="No saved progress exists for this daily challenge.")
        # Older browser snapshots stored an index into the former daily manifest.
        if saved and saved.get("question_id") and not saved.get("current_question"):
            path = Path(app.config.get("DAILY_DIRECTORY", Path(app.instance_path) / "daily")) / f"{day}.json"
            if not path.is_file():
                abort(409, description="The original daily question file is unavailable; saved progress has not been reset.")
            saved["current_question"] = json.loads(path.read_text(encoding="utf-8"))[saved["question_index"]]
            saved["current_question"]["question_id"] = saved["question_id"]
    player_id = session.get("player_id")
    if player_id:
        with _prefetch_lock:
            entry = _prefetches.pop(player_id, None)
        if entry:
            entry[0].cancel()
    recent_images = list(session.get("recent_images", []))[-RECENT_IMAGE_HISTORY_LENGTH:]
    recent_cities = list(session.get("recent_cities", []))[-RECENT_HISTORY_LENGTH:]
    if saved:
        # Keep this daily run's seen questions even after playing another mode.
        for key, history in (("recent_images", recent_images), ("recent_cities", recent_cities)):
            history.extend(value for value in saved.get(key, []) if value not in history)
    session.clear()
    session["game_mode"] = mode
    session["answer_mode"] = answer_mode
    if runs:
        session["daily_runs"] = runs
    if mode == "daily":
        session["daily_date"] = day
        if saved:
            session.update({key: saved[key] for key in daily_progress.FIELDS if key in saved})
        get_game_stats()
    if recent_images:
        session["recent_images"] = recent_images[-RECENT_IMAGE_HISTORY_LENGTH:]
    if recent_cities:
        session["recent_cities"] = recent_cities[-RECENT_HISTORY_LENGTH:]
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


def get_revealed_answer(city):
    return city["answer"]


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
    if city.get("is_dynamic"):
        return render_template(
            "index.html", image_url=city["image_url"], fallback_url=None,
            source_url=city["source_url"], credit=city["credit"], **context,
        )

    fallback_url = f"/static/images/{city['image']}"
    source_url = None
    credit = None
    image_url = fallback_url
    if "commons" in city:
        filename = city["commons"]
        source_url = f"https://commons.wikimedia.org/wiki/File:{quote(filename)}"
        credit = credits[city["answer"]]
        local_photo = Path(app.static_folder) / "images" / f"{city['answer'].replace(' ', '')}.jpg"
        if local_photo.is_file():
            image_url = f"/static/images/{local_photo.name}"
        else:
            image_url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename)}?width=1280"
    return render_template(
        "index.html", image_url=image_url, fallback_url=fallback_url,
        source_url=source_url, credit=credit, **context,
    )


def render_saved_outcome(city, preload_url=None):
    if session.get("question_revealed"):
        return render_question(
            city,
            preload_url=preload_url,
            revealed_answer=get_revealed_answer(city),
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
    start_question_prefetch()
    if session.get("question_resolved"):
        return render_saved_outcome(city)
    return render_question(city)


@app.route("/check", methods=["POST"])
def check():
    city = get_current_question()
    if not city:
        city = get_new_question()
        start_question_prefetch()
        return render_question(city)
    if request.form.get("question_id") != city["question_id"]:
        return render_saved_outcome(city) if session.get("question_resolved") else render_question(city)
    if session.get("question_resolved"):
        return render_saved_outcome(city)
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
    if is_correct:
        result = "✅ Correct!"
    else:
        result = f"❌ Wrong! Answer: {city['answer']}"
    save_question_outcome(result=result, points=points)
    if not is_challenge_complete():
        start_question_prefetch()
    next_city = get_prefetched_question() if not is_challenge_complete() else None
    preload_url = next_city.get("image_url") if next_city else None
    sound_events = [{"type": "correct" if is_correct else "wrong",
                     "id": f"answer:{city['question_id']}"}]
    if is_correct and get_game_stats()["streak"] in {3, 5, 10}:
        sound_events.append({"type": "streak", "id": f"streak:{city['question_id']}"})
    return render_question(city, result, preload_url, round_points=points,
                           sound_events=sound_events)


@app.route("/reveal", methods=["POST"])
def reveal_answer():
    city = get_current_question()
    if not city:
        city = get_new_question()
        start_question_prefetch()
        return render_question(city)
    if request.form.get("question_id") != city["question_id"]:
        return render_saved_outcome(city) if session.get("question_resolved") else render_question(city)
    if session.get("question_resolved"):
        return render_saved_outcome(city)

    record_answer(False)
    save_question_outcome(revealed=True)
    if not is_challenge_complete():
        start_question_prefetch()
    intro = get_city_intro(city)
    next_city = get_prefetched_question() if not is_challenge_complete() else None
    preload_url = next_city.get("image_url") if next_city else None
    return render_question(
        city,
        preload_url=preload_url,
        revealed_answer=get_revealed_answer(city),
        city_intro=intro,
        round_points=0,
    )


@app.route("/next")
def next_question():
    if is_challenge_complete():
        return redirect(url_for("results"))
    if get_current_question() and not session.get("question_resolved"):
        return redirect(url_for("play"))
    return render_question(get_next_question())


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
