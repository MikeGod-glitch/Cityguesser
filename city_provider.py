"""Build lightweight, dynamic city questions from Wikimedia Commons."""

from html import unescape
import json
import random
import re
from threading import Lock
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "CityGuesser/1.0 (educational city photo game)"
CACHE_TTL_SECONDS = 6 * 60 * 60
CREDIT_CACHE_TTL_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 4
MIN_PHOTO_SCORE = 5

# Keeping this list local makes the game predictable and easy to maintain, while
# Commons supplies many different photos for every city.
CITIES = [
    ("Amsterdam", ["阿姆斯特丹"], 52.3676, 4.9041),
    ("Athens", ["雅典"], 37.9838, 23.7275),
    ("Auckland", ["奥克兰"], -36.8509, 174.7645),
    ("Bangkok", ["曼谷"], 13.7563, 100.5018),
    ("Barcelona", ["巴塞罗那"], 41.3874, 2.1686),
    ("Beijing", ["北京", "北京市"], 39.9042, 116.4074),
    ("Berlin", ["柏林"], 52.5200, 13.4050),
    ("Boston", ["波士顿"], 42.3601, -71.0589),
    ("Brussels", ["布鲁塞尔"], 50.8503, 4.3517),
    ("Budapest", ["布达佩斯"], 47.4979, 19.0402),
    ("Buenos Aires", ["布宜诺斯艾利斯"], -34.6037, -58.3816),
    ("Cairo", ["开罗"], 30.0444, 31.2357),
    ("Cape Town", ["开普敦"], -33.9249, 18.4241),
    ("Chicago", ["芝加哥"], 41.8781, -87.6298),
    ("Copenhagen", ["哥本哈根"], 55.6761, 12.5683),
    ("Dubai", ["迪拜"], 25.2048, 55.2708),
    ("Dublin", ["都柏林"], 53.3498, -6.2603),
    ("Edinburgh", ["爱丁堡"], 55.9533, -3.1883),
    ("Florence", ["佛罗伦萨", "Firenze"], 43.7696, 11.2558),
    ("Hong Kong", ["香港", "香港特别行政区"], 22.3193, 114.1694),
    ("Istanbul", ["伊斯坦布尔"], 41.0082, 28.9784),
    ("Kyoto", ["京都", "京都市"], 35.0116, 135.7681),
    ("Lisbon", ["里斯本"], 38.7223, -9.1393),
    ("London", ["伦敦"], 51.5074, -0.1278),
    ("Los Angeles", ["洛杉矶", "LA", "L.A."], 34.0522, -118.2437),
    ("Madrid", ["马德里"], 40.4168, -3.7038),
    ("Melbourne", ["墨尔本"], -37.8136, 144.9631),
    ("Mexico City", ["墨西哥城", "Ciudad de México"], 19.4326, -99.1332),
    ("Milan", ["米兰", "Milano"], 45.4642, 9.1900),
    ("Montreal", ["蒙特利尔", "Montréal"], 45.5017, -73.5673),
    ("Moscow", ["莫斯科"], 55.7558, 37.6173),
    ("Mumbai", ["孟买", "Bombay"], 19.0760, 72.8777),
    ("New York", ["纽约", "纽约市", "New York City", "NYC"], 40.7128, -74.0060),
    ("Osaka", ["大阪", "大阪市"], 34.6937, 135.5023),
    ("Paris", ["巴黎"], 48.8566, 2.3522),
    ("Prague", ["布拉格", "Praha"], 50.0755, 14.4378),
    ("Rio de Janeiro", ["里约热内卢", "Rio"], -22.9068, -43.1729),
    ("Rome", ["罗马", "Roma"], 41.9028, 12.4964),
    ("San Francisco", ["旧金山", "圣弗朗西斯科"], 37.7749, -122.4194),
    ("Seoul", ["首尔", "汉城"], 37.5665, 126.9780),
    ("Shanghai", ["上海", "上海市"], 31.2304, 121.4737),
    ("Singapore", ["新加坡"], 1.3521, 103.8198),
    ("Stockholm", ["斯德哥尔摩"], 59.3293, 18.0686),
    ("Sydney", ["悉尼"], -33.8688, 151.2093),
    ("Taipei", ["台北", "台北市"], 25.0330, 121.5654),
    ("Tokyo", ["东京", "东京都"], 35.6762, 139.6503),
    ("Toronto", ["多伦多"], 43.6532, -79.3832),
    ("Vancouver", ["温哥华"], 49.2827, -123.1207),
    ("Venice", ["威尼斯", "Venezia"], 45.4408, 12.3155),
    ("Vienna", ["维也纳", "Wien"], 48.2082, 16.3738),
]

_photo_cache = {}
_credit_cache = {}
_cache_lock = Lock()
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
    "interior", "indoor", "inside of", "hotel room", "bedroom",
    "bathroom", "kitchen", "corridor", "ceiling", "furniture", "menu",
    "dish", "food", "museum exhibit", "museum collection", "artwork",
    "manuscript", "portrait", "selfie", "passport photo", "close-up",
    "closeup", "macro photograph", "plaque", "inscription", "door detail",
    "window detail",
}
_nature_words = {
    "flower", "flora", "plant", "botanical", "tree", "garden", "grass",
    "forest", "bird", "animal", "insect", "butterfly", "mushroom",
}
_quality_words = {"quality image", "featured picture", "valued image"}
_html_tag = re.compile(r"<[^>]+>")


def _api_get(params):
    query = urlencode({"format": "json", "formatversion": 2, **params})
    request = Request(
        f"{COMMONS_API}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _clean_metadata(value, default="Unknown"):
    if isinstance(value, dict):
        value = value.get("value", "")
    text = _html_tag.sub("", unescape(str(value or ""))).strip()
    return (text or default)[:240]


def _contains_any(text, words):
    return any(
        re.search(rf"(?<!\w){re.escape(word)}(?:s|es)?(?!\w)", text)
        for word in words
    )


def _photo_score(city, title, categories, width, height):
    """Score city-identifying scenes; return zero for irrelevant subjects."""
    name, aliases, _lat, _lon = city
    title_text = title.casefold()
    category_text = " ".join(categories).casefold()
    all_text = f"{title_text} {category_text}"

    if _contains_any(title_text, _blocked_title_words):
        return 0
    if _contains_any(all_text, _unrelated_words):
        return 0

    has_urban_context = _contains_any(all_text, _urban_words)
    has_nature_subject = _contains_any(all_text, _nature_words)
    if not has_urban_context:
        return 0
    if has_nature_subject and not _contains_any(title_text, _urban_words):
        return 0

    city_names = [name, *aliases]
    has_city_name = any(city_name.casefold() in all_text for city_name in city_names)
    score = 3
    if has_city_name:
        score += 3
    if _contains_any(category_text, _urban_words):
        score += 2
    if _contains_any(category_text, _quality_words):
        score += 2
    if width >= 1600 and height >= 900:
        score += 1
    return score


def _fetch_photos(city):
    name, _aliases, lat, lon = city
    with _cache_lock:
        cached = _photo_cache.get(name)
    if cached and cached["expires_at"] > time.time():
        return cached["photos"]

    data = _api_get({
        "action": "query",
        "generator": "geosearch",
        "ggsprimary": "all",
        "ggsnamespace": 6,
        "ggsradius": 5000,
        "ggslimit": 50,
        "ggscoord": f"{lat}|{lon}",
        "prop": "categories|imageinfo",
        "cllimit": "max",
        "clshow": "!hidden",
        "iiprop": "url|size|mime",
        "iiurlwidth": 1280,
    })

    photos = []
    for page in data.get("query", {}).get("pages", []):
        title = page.get("title", "")
        info = (page.get("imageinfo") or [{}])[0]
        width = info.get("width", 0)
        height = info.get("height", 0)
        if info.get("mime") not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        if width < 800 or height < 450 or width / max(height, 1) < 1.15:
            continue
        if not info.get("thumburl"):
            continue
        categories = [category.get("title", "") for category in page.get("categories", [])]
        score = _photo_score(city, title, categories, width, height)
        if score < MIN_PHOTO_SCORE:
            continue
        photos.append({
            "title": title,
            "image_url": info["thumburl"],
            "source_url": info.get("descriptionurl", ""),
        })

    with _cache_lock:
        _photo_cache[name] = {
            "expires_at": time.time() + CACHE_TTL_SECONDS,
            "photos": photos,
        }
    return photos


def _add_credit(photo):
    with _cache_lock:
        cached = _credit_cache.get(photo["title"])
    if cached and cached["expires_at"] > time.time():
        photo.update(cached["metadata"])
        return photo

    data = _api_get({
        "action": "query",
        "titles": photo["title"],
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiextmetadatafilter": "Artist|Credit|LicenseShortName|UsageTerms",
        "iiextmetadatalanguage": "en",
    })
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return photo
    info = (pages[0].get("imageinfo") or [{}])[0]
    metadata = info.get("extmetadata", {})
    credit = {
        "source_url": info.get("descriptionurl", photo["source_url"]),
        "author": _clean_metadata(
            metadata.get("Artist") or metadata.get("Credit"),
            "Wikimedia contributor",
        ),
        "license": _clean_metadata(
            metadata.get("LicenseShortName") or metadata.get("UsageTerms"),
            "See source for license",
        ),
    }
    photo.update(credit)
    with _cache_lock:
        _credit_cache[photo["title"]] = {
            "expires_at": time.time() + CREDIT_CACHE_TTL_SECONDS,
            "metadata": credit,
        }
    return photo


def get_random_question(excluded_images=()):
    """Return a Commons-backed question, or None when the API is unavailable."""
    excluded = set(excluded_images)
    for city in random.sample(CITIES, k=min(2, len(CITIES))):
        try:
            photos = [p.copy() for p in _fetch_photos(city) if p["title"] not in excluded]
            if not photos:
                continue
            photo = _add_credit(random.choice(photos))
            name, aliases, _lat, _lon = city
            return {
                "answer": name,
                "aliases": aliases,
                "image_url": photo["image_url"],
                "source_url": photo["source_url"],
                "credit": [photo.get("author", "Wikimedia contributor"), photo.get("license", "See source for license")],
                "image_id": photo["title"],
                "is_dynamic": True,
            }
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return None
