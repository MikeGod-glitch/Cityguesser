from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from flask import Flask, redirect, render_template, request, session, url_for
from threading import Lock
from urllib.parse import quote
from pathlib import Path
import os
import random
import secrets
import time

from city_provider import CITIES, get_city_intro, get_random_question
from city_choices import CITY_PROFILES, generate_choices

app = Flask(__name__)
app.secret_key = os.environ.get("CITY_GUESSER_SECRET", "city-game-development-secret")

PREFETCH_WAIT_SECONDS = 0.3
PREFETCH_TTL_SECONDS = 30 * 60
CHALLENGE_LENGTH = 10
RECENT_HISTORY_LENGTH = 20
GAME_MODES = {"challenge", "endless"}
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
    city["image_id"] = f"local:{city['answer']}"
    city["is_dynamic"] = False
    return city


def remember_question(city):
    city = city.copy()
    city["question_id"] = secrets.token_urlsafe(8)
    if get_answer_mode() == "choice":
        city["choices"] = generate_choices(city["answer"])
    recent_images = list(session.get("recent_images", []))
    if city.get("image_id"):
        recent_images.append(city["image_id"])
        session["recent_images"] = recent_images[-RECENT_HISTORY_LENGTH:]
    recent_cities = list(session.get("recent_cities", []))
    recent_cities.append(city["answer"])
    session["recent_cities"] = recent_cities[-RECENT_HISTORY_LENGTH:]
    session["current_question"] = city
    session["question_resolved"] = False
    session.pop("question_result", None)
    session.pop("question_points", None)
    session.pop("question_revealed", None)
    session.pop("selected_choice", None)
    return city


def get_new_question():
    recent_images = session.get("recent_images", [])
    recent_cities = session.get("recent_cities", [])
    city = get_random_question(recent_images, recent_cities) or get_local_question()
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
    return get_game_mode() == "challenge" and stats["answered"] >= CHALLENGE_LENGTH


def get_question_number(stats=None):
    stats = stats or get_game_stats()
    number = stats["answered"] if session.get("question_resolved") else stats["answered"] + 1
    if get_game_mode() == "challenge":
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
    }


def record_answer(is_correct):
    stats = get_game_stats().copy()
    stats["answered"] += 1
    points = 0
    if is_correct:
        points = 100 + min(stats["streak"] * 20, 100)
        stats["streak"] += 1
        stats["best_streak"] = max(stats["best_streak"], stats["streak"])
        stats["score"] += points
        stats["correct"] += 1
    else:
        stats["streak"] = 0
    session["game_stats"] = stats
    return points


def save_question_outcome(result=None, points=0, revealed=False):
    session["question_resolved"] = True
    session["question_result"] = result
    session["question_points"] = points
    session["question_revealed"] = revealed


def start_question_prefetch():
    if is_challenge_complete() or (
        get_game_mode() == "challenge" and get_question_number() >= CHALLENGE_LENGTH
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
                if future.result() is not None:
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

    if consume or city is None:
        with _prefetch_lock:
            if _prefetches.get(player_id) == entry:
                _prefetches.pop(player_id, None)
    return city


def get_next_question():
    city = get_prefetched_question(PREFETCH_WAIT_SECONDS, consume=True)
    city = remember_question(city or get_local_question())
    start_question_prefetch()
    return city


def start_new_game(mode="challenge", answer_mode=None):
    mode = mode if mode in GAME_MODES else "challenge"
    answer_mode = get_answer_mode() if answer_mode is None else answer_mode
    answer_mode = answer_mode if answer_mode in ANSWER_MODES else "text"
    player_id = session.get("player_id")
    if player_id:
        with _prefetch_lock:
            entry = _prefetches.pop(player_id, None)
        if entry:
            entry[0].cancel()
    recent_images = list(session.get("recent_images", []))[-RECENT_HISTORY_LENGTH:]
    recent_cities = list(session.get("recent_cities", []))[-RECENT_HISTORY_LENGTH:]
    session.clear()
    session["game_mode"] = mode
    session["answer_mode"] = answer_mode
    if recent_images:
        session["recent_images"] = recent_images
    if recent_cities:
        session["recent_cities"] = recent_cities


@app.before_request
def ensure_game_mode():
    if session.get("game_mode") not in GAME_MODES:
        start_new_game("challenge")


def get_revealed_answer(city):
    chinese_name = next(
        (
            alias for alias in city.get("aliases", [])
            if any("\u4e00" <= char <= "\u9fff" for char in alias)
        ),
        None,
    )
    return f"{chinese_name} / {city['answer']}" if chinese_name else city["answer"]


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
    round_points=None, game_summary=False,
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
    return render_question(city, result, preload_url, round_points=points)


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
    return render_question(get_next_question())


@app.route("/results")
def results():
    if not is_challenge_complete():
        return redirect(url_for("home"))
    city = get_current_question()
    if not city:
        return redirect(url_for("home"))
    return render_question(city, game_summary=True)


@app.route("/reset", methods=["POST"])
def reset_game():
    start_new_game(
        request.form.get("mode", get_game_mode()),
        request.form.get("answer_mode"),
    )
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
