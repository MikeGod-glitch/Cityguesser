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


def photo_score(city, title, categories, width, height):
    """Score city-identifying scenes; return zero for irrelevant subjects."""
    name, aliases, _lat, _lon = city
    title_text = title.casefold()
    category_text = " ".join(categories).casefold()
    all_text = f"{title_text} {category_text}"

    if contains_any(title_text, _blocked_title_words):
        return 0
    if contains_any(all_text, _unrelated_words):
        return 0

    has_urban_context = contains_any(all_text, _urban_words)
    has_nature_subject = contains_any(all_text, _nature_words)
    if not has_urban_context:
        return 0
    if has_nature_subject and not contains_any(title_text, _urban_words):
        return 0

    city_names = [name, *aliases]
    has_city_name = any(city_name.casefold() in all_text for city_name in city_names)
    score = 3
    if has_city_name:
        score += 3
    if contains_any(category_text, _urban_words):
        score += 2
    if contains_any(category_text, _quality_words):
        score += 2
    if width >= 1600 and height >= 900:
        score += 1
    return score
