from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from flask import Flask, render_template, request, session
from threading import Lock
from urllib.parse import quote
from pathlib import Path
import os
import random
import secrets
import time

from city_provider import CITIES, get_city_intro, get_random_question

app = Flask(__name__)
app.secret_key = os.environ.get("CITY_GUESSER_SECRET", "city-game-development-secret")

PREFETCH_WAIT_SECONDS = 0.3
PREFETCH_TTL_SECONDS = 30 * 60
_prefetch_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="city-question")
_prefetches = {}
_prefetch_lock = Lock()

# Wikimedia Commons file names. See static/images/SOURCES.md for credits.
commons = {
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
    {"image": "Chicago.png", "answer": "Chicago"},
    {"image": "London.png", "answer": "London"},
    {"image": "Tokyo.png", "answer": "Tokyo"},
]
cities += [
    {"image": f"{name.replace(' ', '')}.svg", "answer": name, "commons": filename}
    for name, filename in commons.items()
]
city_aliases = {name: aliases for name, aliases, _lat, _lon in CITIES}


def get_local_question():
    if "remaining" not in session or not session["remaining"]:
        session["remaining"] = list(range(len(cities)))

    remaining = session["remaining"]
    idx = random.choice(remaining)
    remaining.remove(idx)
    session["remaining"] = remaining
    city = cities[idx].copy()
    city["aliases"] = city_aliases.get(city["answer"], [])
    city["is_dynamic"] = False
    return city


def remember_question(city):
    recent_images = list(session.get("recent_images", []))
    if city.get("image_id"):
        recent_images.append(city["image_id"])
        session["recent_images"] = recent_images[-10:]
    session["current_question"] = city
    return city


def get_new_question():
    recent_images = session.get("recent_images", [])
    city = get_random_question(recent_images) or get_local_question()
    return remember_question(city)


def get_player_id():
    if "player_id" not in session:
        session["player_id"] = secrets.token_urlsafe(12)
    return session["player_id"]


def start_question_prefetch():
    player_id = get_player_id()
    recent_images = tuple(session.get("recent_images", []))
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

        future = _prefetch_executor.submit(get_random_question, recent_images)
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


def get_revealed_answer(city):
    chinese_name = next(
        (
            alias for alias in city.get("aliases", [])
            if any("\u4e00" <= char <= "\u9fff" for char in alias)
        ),
        None,
    )
    return f"{chinese_name} / {city['answer']}" if chinese_name else city["answer"]


def render_question(
    city, result=None, preload_url=None, revealed_answer=None, city_intro=None,
):
    if city.get("is_dynamic"):
        return render_template(
            "index.html", image_url=city["image_url"], fallback_url=None,
            source_url=city["source_url"], credit=city["credit"], result=result,
            preload_url=preload_url,
            revealed_answer=revealed_answer, city_intro=city_intro,
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
        source_url=source_url, credit=credit, result=result,
        preload_url=preload_url,
        revealed_answer=revealed_answer, city_intro=city_intro,
    )


@app.route("/")
def home():
    city = session.get("current_question") or get_new_question()
    start_question_prefetch()
    return render_question(city)


@app.route("/check", methods=["POST"])
def check():
    city = session.get("current_question")
    if not city:
        city = get_new_question()
        start_question_prefetch()
        return render_question(city)
    guess = request.form["guess"]
    accepted_answers = [city["answer"], *city.get("aliases", [])]
    if guess.strip().casefold() in {answer.casefold() for answer in accepted_answers}:
        result = "✅ Correct!"
    else:
        result = f"❌ Wrong! Answer: {city['answer']}"
    start_question_prefetch()
    next_city = get_prefetched_question()
    preload_url = next_city.get("image_url") if next_city else None
    return render_question(city, result, preload_url)


@app.route("/reveal", methods=["POST"])
def reveal_answer():
    city = session.get("current_question")
    if not city:
        city = get_new_question()
        start_question_prefetch()
        return render_question(city)

    start_question_prefetch()
    intro = get_city_intro(city)
    next_city = get_prefetched_question()
    preload_url = next_city.get("image_url") if next_city else None
    return render_question(
        city,
        preload_url=preload_url,
        revealed_answer=get_revealed_answer(city),
        city_intro=intro,
    )


@app.route("/next")
def next_question():
    return render_question(get_next_question())


if __name__ == "__main__":
    app.run(debug=True)
