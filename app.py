from flask import Flask, render_template, request, session
from urllib.parse import quote
from pathlib import Path
import random

app = Flask(__name__)
app.secret_key = "city_game_secret"

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


def get_new_question():
    if "remaining" not in session or not session["remaining"]:
        session["remaining"] = list(range(len(cities)))

    remaining = session["remaining"]
    idx = random.choice(remaining)
    remaining.remove(idx)
    session["remaining"] = remaining
    session["current"] = idx
    return cities[idx]


def render_question(city, result=None):
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
    )


@app.route("/")
def home():
    city = get_new_question() if "current" not in session else cities[session["current"]]
    return render_question(city)


@app.route("/check", methods=["POST"])
def check():
    city = cities[session["current"]]
    guess = request.form["guess"]
    if guess.strip().casefold() == city["answer"].casefold():
        result = "✅ Correct!"
    else:
        result = f"❌ Wrong! Answer: {city['answer']}"
    return render_question(city, result)


@app.route("/next")
def next_question():
    return render_question(get_new_question())


if __name__ == "__main__":
    app.run(debug=True)
