"""Pure photo identity and metadata scoring rules; no network or cache state."""

import hashlib
import re


_blocked_title_words = {
    "flag", "map", "logo", "coat of arms", "locator", "diagram",
    "icon", "symbol", "route map", "district map", "seal",
}


_urban_words = {
    "street", "streetscape", "road", "avenue", "boulevard", "square",
    "plaza", "cityscape", "urban", "downtown", "neighbourhood",
    "neighborhood", "district", "buildings", "architecture", "skyline",
    "waterfront", "canal", "harbour", "harbor", "tram", "metro",
    "railway station", "train station", "traffic", "bridge", "tower",
    "palace", "castle", "cathedral", "church", "mosque", "temple",
    "monument", "opera house", "town hall", "city hall", "skyscraper",
    "panorama", "panoramic", "city view", "views of", "aerial view",
    "aerial photograph", "city centre", "city center", "old town",
    "market", "bazaar", "residential", "housing", "apartment", "facade",
    "promenade", "esplanade", "quay", "pier", "port", "station",
    "museum", "hotel", "university", "campus", "stadium", "public art",
    "mural", "statue", "sculpture", "rue", "strasse", "straße", "calle",
    "piazza", "avenida", "rua",
}


_unrelated_words = {
    "interior", "indoor", "inside", "inside of", "reception", "lobby",
    "hotel room", "bedroom",
    "bathroom", "kitchen", "corridor", "ceiling", "furniture", "menu",
    "dish", "food", "museum exhibit", "museum collection", "exhibition",
    "artwork",
    "manuscript", "portrait", "selfie", "passport photo", "close-up",
    "closeup", "macro photograph", "plaque", "inscription", "door detail",
    "window detail",
}


_nature_words = {
    "flower", "flora", "plant", "botanical", "tree", "garden", "grass",
    "forest", "bird", "animal", "insect", "butterfly", "mushroom",
}


_quality_words = {"quality image", "featured picture", "valued image"}
_green_space_words = {"park", "garden", "botanical garden", "urban green space"}


def photo_id(title):
    """Return a compact stable id suitable for Flask's cookie session."""
    return hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]


def photo_family_key(title):
    """Group filenames that differ mainly by counters, dates, or punctuation."""
    title = re.sub(r"^file:|\.[a-z0-9]{2,5}$", "", title.casefold())
    title = re.sub(r"\([^)]*\)|\[[^]]*\]", " ", title)
    title = re.sub(r"\b\d+\b", " ", title)
    words = re.findall(r"[^\W\d_]+", title, flags=re.UNICODE)
    return " ".join(words)


def contains_any(text, words):
    return any(
        re.search(rf"(?<!\w){re.escape(word)}(?:s|es)?(?!\w)", text)
        for word in words
    )


def photo_assessment(city, title, categories, width, height):
    """Favor varied city scenes; reject explicit non-scene subjects, not noisy tags."""
    name, aliases, _lat, _lon = city
    title_text = title.casefold()
    category_text = " ".join(categories).casefold()
    all_text = f"{title_text} {category_text}"

    if contains_any(title_text, _blocked_title_words):
        return 0, "non_photo_subject"
    if contains_any(title_text, _unrelated_words):
        return 0, "unsuitable_title"

    has_city_name = any(label.casefold() in all_text for label in [name, *aliases])
    title_urban = contains_any(title_text, _urban_words)
    category_urban = contains_any(category_text, _urban_words)
    has_urban_context = contains_any(all_text, _urban_words)
    city_green_space = has_city_name and contains_any(all_text, _green_space_words)
    if not has_urban_context and not city_green_space:
        return 0, "no_city_scene"
    # Explicit plant/animal subjects remain out; incidental nature categories do
    # not disqualify a city park, building, or streetscape.
    if contains_any(title_text, _nature_words - {"garden"}) and not title_urban:
        return 0, "nature_subject"
    if contains_any(category_text, _unrelated_words) and not (
        title_urban or (city_green_space and contains_any(title_text, _green_space_words))
    ):
        return 0, "unsuitable_categories"

    score = 3
    if has_city_name:
        score += 3
    if category_urban or city_green_space:
        score += 2
    if contains_any(category_text, _quality_words):
        score += 2
    if width >= 1600 and height >= 900:
        score += 1
    return score, None


def photo_score(city, title, categories, width, height):
    return photo_assessment(city, title, categories, width, height)[0]


def photo_variant_key(title, categories, width, height):
    """Distinguish explicit view/time cues without treating counters as variety."""
    text = (title + " " + " ".join(categories)).casefold()
    cues = {"north", "south", "east", "west", "front", "rear", "aerial",
            "night", "day", "dawn", "dusk", "sunrise", "sunset", "morning",
            "evening", "winter", "summer", "spring", "autumn", "snow"}
    views = tuple(sorted(word for word in cues if contains_any(text, {word})))
    years = tuple(sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", title))))
    ratio = width / max(height, 1)
    shape = "panoramic" if ratio >= 2 else "portrait" if ratio < .85 else "landscape" if ratio > 1.2 else "square"
    return views, years, shape
