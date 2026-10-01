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
ZH_WIKIPEDIA_API = "https://zh.wikipedia.org/w/api.php"
EN_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "CityGuesser/1.0 (educational city photo game)"
CACHE_TTL_SECONDS = 6 * 60 * 60
CREDIT_CACHE_TTL_SECONDS = 24 * 60 * 60
INTRO_CACHE_TTL_SECONDS = 24 * 60 * 60
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

    # Curated expansion: globally recognizable cities with distinctive
    # architecture, culture, or urban scenery. This is intentionally a game
    # catalog rather than a population ranking.
    # Asia
    ("Delhi", ["德里", "新德里", "New Delhi", "Dilli"], 28.6139, 77.2090),
    ("Agra", ["阿格拉"], 27.1767, 78.0081),
    ("Jaipur", ["斋浦尔", "斋普尔"], 26.9124, 75.7873),
    ("Varanasi", ["瓦拉纳西", "Benares", "Banaras", "Kashi"], 25.3176, 82.9739),
    ("Kolkata", ["加尔各答", "Calcutta"], 22.5726, 88.3639),
    ("Bengaluru", ["班加罗尔", "Bangalore"], 12.9716, 77.5946),
    ("Kuala Lumpur", ["吉隆坡", "KL"], 3.1390, 101.6869),
    ("Jakarta", ["雅加达", "Djakarta"], -6.2088, 106.8456),
    ("Hanoi", ["河内", "Hà Nội"], 21.0278, 105.8342),
    ("Ho Chi Minh City", ["胡志明市", "西贡", "Saigon", "Sài Gòn"], 10.8231, 106.6297),
    ("Manila", ["马尼拉", "Maynila"], 14.5995, 120.9842),
    ("Macau", ["澳门", "澳门特别行政区", "Macao"], 22.1987, 113.5439),
    ("Xi'an", ["西安", "西安市", "Xian", "Xi’an"], 34.3416, 108.9398),
    ("Chengdu", ["成都", "成都市"], 30.5728, 104.0668),
    ("Busan", ["釜山", "釜山广域市", "Pusan"], 35.1796, 129.0756),

    # Middle East and Africa
    ("Jerusalem", ["耶路撒冷", "Yerushalayim", "Al-Quds"], 31.7683, 35.2137),
    ("Abu Dhabi", ["阿布扎比", "Abu Zabi"], 24.4539, 54.3773),
    ("Doha", ["多哈", "Ad-Dawhah"], 25.2854, 51.5310),
    ("Marrakech", ["马拉喀什", "Marrakesh"], 31.6295, -7.9811),
    ("Casablanca", ["卡萨布兰卡", "Dar al-Bayda"], 33.5731, -7.5898),
    ("Alexandria", ["亚历山大", "Al-Iskandariyya"], 31.2001, 29.9187),
    ("Nairobi", ["内罗毕"], -1.2921, 36.8219),
    ("Johannesburg", ["约翰内斯堡", "Joburg", "Jozi"], -26.2041, 28.0473),
    ("Lagos", ["拉各斯", "Eko"], 6.5244, 3.3792),
    ("Zanzibar City", ["桑给巴尔市", "Zanzibar", "Stone Town"], -6.1659, 39.2026),

    # Europe
    ("Munich", ["慕尼黑", "München"], 48.1351, 11.5820),
    ("Warsaw", ["华沙", "Warszawa"], 52.2297, 21.0122),
    ("Krakow", ["克拉科夫", "Kraków"], 50.0647, 19.9450),
    ("Dubrovnik", ["杜布罗夫尼克", "Ragusa"], 42.6507, 18.0944),
    ("Helsinki", ["赫尔辛基", "Helsingfors"], 60.1699, 24.9384),
    ("Oslo", ["奥斯陆", "Christiania", "Kristiania"], 59.9139, 10.7522),
    ("Reykjavik", ["雷克雅未克", "Reykjavík"], 64.1466, -21.9426),
    ("Zurich", ["苏黎世", "Zürich"], 47.3769, 8.5417),
    ("Porto", ["波尔图", "Oporto"], 41.1579, -8.6291),
    ("Seville", ["塞维利亚", "Sevilla"], 37.3891, -5.9845),

    # North and Central America
    ("Washington, D.C.", ["华盛顿", "华盛顿特区", "Washington DC", "Washington D.C.", "DC", "D.C."], 38.9072, -77.0369),
    ("Miami", ["迈阿密"], 25.7617, -80.1918),
    ("Las Vegas", ["拉斯维加斯", "Vegas"], 36.1699, -115.1398),
    ("New Orleans", ["新奥尔良", "NOLA"], 29.9511, -90.0715),
    ("Seattle", ["西雅图"], 47.6062, -122.3321),
    ("Havana", ["哈瓦那", "La Habana"], 23.1136, -82.3666),
    ("Panama City", ["巴拿马城", "巴拿马市", "Ciudad de Panamá"], 8.9824, -79.5199),
    ("Quebec City", ["魁北克市", "Québec", "Ville de Québec"], 46.8139, -71.2080),

    # South America
    ("Sao Paulo", ["圣保罗", "São Paulo"], -23.5505, -46.6333),
    ("Lima", ["利马"], -12.0464, -77.0428),
    ("Bogota", ["波哥大", "Bogotá"], 4.7110, -74.0721),
    ("Santiago", ["圣地亚哥", "Santiago de Chile"], -33.4489, -70.6693),
    ("Cusco", ["库斯科", "Cuzco", "Qosqo"], -13.5319, -71.9675),
    ("Cartagena", ["卡塔赫纳", "Cartagena de Indias"], 10.3910, -75.4794),

    # Oceania
    ("Brisbane", ["布里斯班"], -27.4698, 153.0251),
]

_photo_cache = {}
_credit_cache = {}
_intro_cache = {}
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


def _api_get(params, endpoint=COMMONS_API):
    query = urlencode({"format": "json", "formatversion": 2, **params})
    request = Request(
        f"{endpoint}?{query}",
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


def _fetch_city_intro(title, endpoint):
    data = _api_get({
        "action": "query",
        "titles": title,
        "redirects": 1,
        "prop": "extracts|info",
        "exintro": 1,
        "explaintext": 1,
        "exchars": 360,
        "inprop": "url",
    }, endpoint=endpoint)
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None
    text = " ".join(pages[0].get("extract", "").split())
    if not text:
        return None
    return {
        "text": text,
        "source_url": pages[0].get("fullurl"),
    }


def get_city_intro(city):
    """Return a cached Wikipedia introduction for a question's city."""
    cache_key = city["answer"]
    with _cache_lock:
        cached = _intro_cache.get(cache_key)
    if cached and cached["expires_at"] > time.time():
        return cached["intro"]

    chinese_title = next(
        (
            alias for alias in city.get("aliases", [])
            if any("\u4e00" <= char <= "\u9fff" for char in alias)
        ),
        None,
    )
    sources = []
    if chinese_title:
        sources.append((chinese_title, ZH_WIKIPEDIA_API))
    sources.append((city["answer"], EN_WIKIPEDIA_API))

    for title, endpoint in sources:
        try:
            intro = _fetch_city_intro(title, endpoint)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if intro:
            with _cache_lock:
                _intro_cache[cache_key] = {
                    "expires_at": time.time() + INTRO_CACHE_TTL_SECONDS,
                    "intro": intro,
                }
            return intro

    return {
        "text": "城市介绍暂时无法加载。",
        "source_url": None,
    }


def get_random_question(excluded_images=(), excluded_cities=()):
    """Return a Commons-backed question, or None when the API is unavailable."""
    excluded = set(excluded_images)
    excluded_names = set(excluded_cities)
    available_cities = [city for city in CITIES if city[0] not in excluded_names]
    if not available_cities:
        available_cities = CITIES
    for city in random.sample(available_cities, k=min(2, len(available_cities))):
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
