"""Build lightweight, dynamic city questions from Wikimedia Commons."""

from html import unescape
import json
import random
import re
from threading import Lock
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from city_catalog import CITIES
from photo_rules import photo_id as _photo_id, photo_family_key as _photo_family_key, photo_score as _photo_score


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
EN_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "CityGuesser/1.0 (educational city photo game)"
CACHE_TTL_SECONDS = 6 * 60 * 60
CREDIT_CACHE_TTL_SECONDS = 24 * 60 * 60
INTRO_CACHE_TTL_SECONDS = 24 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 4
MIN_PHOTO_SCORE = 5
MIN_PHOTO_PIXELS = 300_000
SUPPORTED_PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp"}
PHOTO_CANDIDATE_LIMIT = 100
DYNAMIC_CITY_ATTEMPTS = 4
PHOTO_PREPARE_ATTEMPTS = 3
EMPTY_CACHE_TTL_SECONDS = 5 * 60
FAILURE_RETRY_SECONDS = 30
STALE_CACHE_TTL_SECONDS = 24 * 60 * 60

_photo_cache = {}
_credit_cache = {}
_intro_cache = {}
_prepared_questions = {}
_photo_retry_after = {}
_thumbnail_retry_after = {}
_photo_locks = {}
_api_retry_after = {}
_cache_lock = Lock()
_html_tag = re.compile(r"<[^>]+>")


class CommonsApiError(OSError):
    def __init__(self, code, info):
        self.code = code
        super().__init__(f"{code}: {info}")


def _check_api_data(data):
    if not isinstance(data, dict):
        raise ValueError("API response is not an object")
    if data.get("error"):
        error = data["error"]
        raise CommonsApiError(error.get("code", "unknown"), error.get("info", "API error"))
    return data


def _retry_seconds(value):
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 60


def _api_get(params, endpoint=COMMONS_API):
    with _cache_lock:
        if _api_retry_after.get(endpoint, 0) > time.time():
            raise CommonsApiError("backoff", "API retry period has not elapsed")
    query = urlencode({"format": "json", "formatversion": 2, **params})
    request = Request(
        f"{endpoint}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            data = json.load(response)
        return _check_api_data(data)
    except HTTPError as exc:
        if exc.code in {429, 503}:
            value = exc.headers.get("Retry-After") if exc.headers else None
            with _cache_lock:
                _api_retry_after[endpoint] = max(_api_retry_after.get(endpoint, 0), time.time() + _retry_seconds(value))
        raise
    except CommonsApiError as exc:
        if exc.code in {"ratelimited", "maxlag"}:
            with _cache_lock:
                _api_retry_after[endpoint] = time.time() + FAILURE_RETRY_SECONDS
        raise


def _clean_metadata(value, default="Unknown"):
    if isinstance(value, dict):
        value = value.get("value", "")
    text = _html_tag.sub("", unescape(str(value or ""))).strip()
    return (text or default)[:240]


def _fetch_photos(city):
    # Single flight per city: concurrent players do not rebuild the same pool.
    with _cache_lock:
        city_lock = _photo_locks.setdefault(city[0], Lock())
    with city_lock:
        return _fetch_city_photos(city)


def _fetch_city_photos(city):
    name = city[0]
    with _cache_lock:
        cached = _photo_cache.get(name)
    if cached and cached["expires_at"] > time.time():
        return cached["photos"]
    stale = cached and cached.get("photos") and cached.get("stale_until", cached["expires_at"]) > time.time()
    with _cache_lock:
        retrying = _photo_retry_after.get(name, 0) > time.time()
    if retrying:
        if stale:
            return cached["photos"]
        raise CommonsApiError("backoff", "City photo refresh is waiting to retry")

    try:
        return _refresh_photos(city, cached if stale else None)
    except (OSError, ValueError, KeyError):
        with _cache_lock:
            _photo_retry_after[name] = time.time() + FAILURE_RETRY_SECONDS
        if stale:
            return cached["photos"]
        raise


def _select_photo_candidates(city, pages):
    """Filter metadata and retain the best file in each filename family."""
    photo_families = {}
    for page in pages:
        title = page.get("title", "")
        info = (page.get("imageinfo") or [{}])[0]
        width = info.get("width", 0)
        height = info.get("height", 0)
        if info.get("mime") not in SUPPORTED_PHOTO_MIMES:
            continue
        if width * height < MIN_PHOTO_PIXELS:
            continue
        if not info.get("url") and not info.get("thumburl"):
            continue
        categories = [category.get("title", "") for category in page.get("categories", [])]
        score = _photo_score(city, title, categories, width, height)
        if score < MIN_PHOTO_SCORE:
            continue
        photo = {
            "title": title,
            # Do not render a potentially huge original. Prepare a thumbnail only
            # after MIME/size/quality filtering, in a request for this file alone.
            "image_url": info.get("thumburl"),
            "source_url": info.get("descriptionurl", ""),
            "_quality": (score, width * height),
        }
        family = _photo_family_key(title)
        existing = photo_families.get(family)
        if not existing or photo["_quality"] > existing["_quality"]:
            photo_families[family] = photo

    photos = []
    for photo in photo_families.values():
        photo.pop("_quality", None)
        photos.append(photo)
    return photos


def _refresh_photos(city, stale_cache=None):
    name, _aliases, lat, lon = city
    data = _api_get({
        "action": "query",
        "generator": "geosearch",
        "ggsprimary": "all",
        "ggsnamespace": 6,
        "ggsradius": 5000,
        "ggslimit": PHOTO_CANDIDATE_LIMIT,
        "ggscoord": f"{lat}|{lon}",
        "prop": "categories|imageinfo",
        "cllimit": "max",
        "clshow": "!hidden",
        "iiprop": "url|size|mime",
    })
    _check_api_data(data)
    photos = _select_photo_candidates(city, data.get("query", {}).get("pages", []))

    if not photos and stale_cache:
        with _cache_lock:
            _photo_retry_after[name] = time.time() + EMPTY_CACHE_TTL_SECONDS
        return stale_cache["photos"]
    now = time.time()
    with _cache_lock:
        _photo_cache[name] = {
            "expires_at": now + (CACHE_TTL_SECONDS if photos else EMPTY_CACHE_TTL_SECONDS),
            "stale_until": now + STALE_CACHE_TTL_SECONDS,
            "photos": photos,
        }
        _photo_retry_after.pop(name, None)
    return photos


def _add_credit(photo):
    with _cache_lock:
        cached = _credit_cache.get(photo["title"])
    if cached and cached["expires_at"] > time.time() and (photo.get("image_url") or cached["metadata"].get("image_url")):
        photo.update(cached["metadata"])
        return photo

    data = _api_get({
        "action": "query",
        "titles": photo["title"],
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": 1280,
        "iiextmetadatafilter": "Artist|Credit|LicenseShortName|UsageTerms",
        "iiextmetadatalanguage": "en",
    })
    _check_api_data(data)
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        raise CommonsApiError("missing_image", "Image information is unavailable")
    info = (pages[0].get("imageinfo") or [{}])[0]
    metadata = info.get("extmetadata", {})
    credit = {
        "image_url": info.get("thumburl") or photo.get("image_url"),
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
    if not credit["image_url"]:
        raise CommonsApiError("missing_thumbnail", "No usable thumbnail was returned")
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
    """Return a cached English Wikipedia introduction for a question's city."""
    cache_key = ("en", city["answer"])
    with _cache_lock:
        cached = _intro_cache.get(cache_key)
    if cached and cached["expires_at"] > time.time():
        return cached["intro"]

    try:
        intro = _fetch_city_intro(city["answer"], EN_WIKIPEDIA_API)
    except (OSError, ValueError, KeyError):
        intro = None
    if intro:
        with _cache_lock:
            _intro_cache[cache_key] = {
                "expires_at": time.time() + INTRO_CACHE_TTL_SECONDS,
                "intro": intro,
            }
        return intro

    return {
        "text": "City information is temporarily unavailable.",
        "source_url": None,
    }


def _prepare_city_question(city, excluded):
    """Try a bounded number of files, isolating thumbnail failures within this city."""
    photos = [
        photo.copy() for photo in _fetch_photos(city)
        if photo["title"] not in excluded and _photo_id(photo["title"]) not in excluded
    ]
    with _cache_lock:
        photos = [photo for photo in photos if _thumbnail_retry_after.get(photo["title"], 0) <= time.time()]
    for _ in range(min(PHOTO_PREPARE_ATTEMPTS, len(photos))):
        photo = random.choice(photos)
        photos.remove(photo)
        try:
            photo = _add_credit(photo)
            if not photo.get("image_url"):
                continue
        except CommonsApiError as exc:
            if exc.code not in {"urlparamnormal", "missing_image", "missing_thumbnail"}:
                raise
            with _cache_lock:
                _thumbnail_retry_after[photo["title"]] = time.time() + EMPTY_CACHE_TTL_SECONDS
            continue
        name, aliases, _lat, _lon = city
        question = {
            "answer": name,
            "aliases": aliases,
            "image_url": photo["image_url"],
            "source_url": photo["source_url"],
            "credit": [photo.get("author", "Wikimedia contributor"), photo.get("license", "See source for license")],
            "image_id": _photo_id(photo["title"]),
            "is_dynamic": True,
        }
        with _cache_lock:
            _prepared_questions.setdefault(name, {})[question["image_id"]] = {
                "question": question.copy(), "expires_at": time.time() + CACHE_TTL_SECONDS,
            }
        return question
    return None


def get_random_question(excluded_images=(), excluded_cities=()):
    """Return the first prepared question from randomly ordered candidate cities."""
    excluded = set(excluded_images)
    excluded_names = set(excluded_cities)
    available_cities = [city for city in CITIES if city[0] not in excluded_names] or CITIES
    for city in random.sample(available_cities, k=min(DYNAMIC_CITY_ATTEMPTS, len(available_cities))):
        try:
            question = _prepare_city_question(city, excluded)
            if question is not None:
                return question
        except (OSError, ValueError, KeyError):
            continue
    return get_cached_question(excluded_images, excluded_cities)


def get_cached_question(excluded_images=(), excluded_cities=()):
    """Pick an already prepared photo without any network calls or history relaxation."""
    excluded = set(excluded_images)
    excluded_names = set(excluded_cities)
    now = time.time()
    candidates = {}
    with _cache_lock:
        for name, photos in _prepared_questions.items():
            for image_id in list(photos):
                if photos[image_id]["expires_at"] <= now:
                    photos.pop(image_id)
            if name not in excluded_names:
                eligible = [entry["question"] for image_id, entry in photos.items() if image_id not in excluded]
                if eligible:
                    candidates[name] = eligible
        if not candidates:
            return None
        return random.choice(candidates[random.choice(list(candidates))]).copy()
