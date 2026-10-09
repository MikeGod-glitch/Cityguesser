"""Build lightweight, dynamic city questions from Wikimedia Commons."""

from html import unescape
import hashlib
import json
from pathlib import Path
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
from photo_rules import photo_assessment, photo_variant_key
from photo_sources import collect_city_photos
import photo_statistics


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
EN_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "CityGuesser/1.0 (educational city photo game)"
CACHE_TTL_SECONDS = 6 * 60 * 60
PREPARED_QUESTION_TTL_SECONDS = 24 * 60 * 60
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
COMMONS_REQUEST_INTERVAL_SECONDS = 3
CANDIDATE_MAX_PER_CITY = 500
# Invalidate persisted decisions when discovery, filtering or city data changes.


# Selection and persistence changes do not alter the previous candidate filters.
COMPATIBLE_CANDIDATE_RULE_VERSIONS = {
    "16c00a14fdd800fb8e69e6257e207f9a92ba0601ae947c3b53fbaacda2a08725",
    "46112d2ac61e1fdadedfabb210c4cd53fb3969fedbac30c5cbffaed16cd853c4",
    "a559d35332caf3f4a339eab0af5177f8612ecdc0509f876fc218ee2f9a966372",
}

# Guard legacy-cache migration against future discovery/filter changes.
_filter_source = Path(__file__).read_text(encoding="utf-8")
_filter_source = (_filter_source[_filter_source.index("MIN_PHOTO_SCORE ="):
                                  _filter_source.index("# Invalidate persisted decisions")]
                  + _filter_source[_filter_source.rindex("def _select_photo_candidates("):
                                   _filter_source.rindex("def _refresh_photos(")])
CANDIDATE_FILTER_VERSION = hashlib.sha256(b"".join(
    Path(__file__).with_name(name).read_bytes().replace(b"\r\n", b"\n")
    for name in ("photo_rules.py", "photo_sources.py", "city_catalog.py")
) + _filter_source.encode()).hexdigest()
CANDIDATE_RULE_VERSION = CANDIDATE_FILTER_VERSION
COMPATIBLE_FILTER_VERSION = "55a006505327d1f42ab0e30f1dac0c95c4f9a75465bcbe1db12010cb7228465d"

_photo_cache = {}
_credit_cache = {}
_intro_cache = {}
_prepared_questions = {}
_photo_retry_after = {}
_thumbnail_retry_after = {}
_photo_locks = {}
_api_retry_after = {}
_cache_lock = Lock()
_commons_request_lock = Lock()
_commons_next_request_at = 0
_candidate_generation = 0
_local_image_checker = lambda question: False
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
    global _commons_next_request_at
    if endpoint != COMMONS_API:
        return _request_api(params, endpoint)
    # Hold through response/error handling so another worker cannot send before
    # Retry-After has been published. Never sleep while holding the cache lock.
    with _commons_request_lock:
        if commons_retry_delay() > 0:
            raise CommonsApiError("backoff", "API retry period has not elapsed")
        if _is_search_request(params) and commons_search_retry_delay() > 0:
            raise CommonsApiError("search_backoff", "Photo search is waiting to retry")
        delay = _commons_next_request_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _commons_next_request_at = time.monotonic() + COMMONS_REQUEST_INTERVAL_SECONDS
        return _request_api(params, endpoint)


def _request_api(params, endpoint):
    with _cache_lock:
        if _api_retry_after.get(endpoint, 0) > time.time():
            raise CommonsApiError("backoff", "API retry period has not elapsed")
        if (_is_search_request(params)
                and _api_retry_after.get((endpoint, "search"), 0) > time.time()):
            raise CommonsApiError("search_backoff", "Photo search is waiting to retry")
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
        if exc.code in {"ratelimited", "maxlag", "cirrussearch-too-busy-error"}:
            scope = (endpoint, "search") if exc.code == "cirrussearch-too-busy-error" else endpoint
            with _cache_lock:
                _api_retry_after[scope] = max(_api_retry_after.get(scope, 0),
                                            time.time() + FAILURE_RETRY_SECONDS)
        raise


def _clean_metadata(value, default="Unknown"):
    if isinstance(value, dict):
        value = value.get("value", "")
    text = _html_tag.sub("", unescape(str(value or ""))).strip()
    return (text or default)[:240]


def commons_retry_delay():
    with _cache_lock:
        return max(0, _api_retry_after.get(COMMONS_API, 0) - time.time())


def _is_search_request(params):
    return (params.get("generator") == "search" or params.get("action") == "opensearch"
            or "search" in str(params.get("list", "")).split("|"))


def commons_search_retry_delay():
    with _cache_lock:
        return max(0, _api_retry_after.get(COMMONS_API, 0) - time.time(),
                   _api_retry_after.get((COMMONS_API, "search"), 0) - time.time())


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


def _select_photo_candidates(city, pages, diagnostics=None):
    """Retain up to three distinguishable views per family, with exact-file dedup."""
    photo_families = {}
    rejected = {}
    identities = set()
    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1
    for page in pages:
        title = page.get("title", "")
        if not title or title in identities:
            reject("duplicate_or_missing_identity")
            continue
        identities.add(title)
        info = (page.get("imageinfo") or [{}])[0]
        width = info.get("width", 0)
        height = info.get("height", 0)
        if info.get("mime") not in SUPPORTED_PHOTO_MIMES:
            reject("unsupported_format")
            continue
        if width * height < MIN_PHOTO_PIXELS:
            reject("too_small")
            continue
        if not info.get("url") and not info.get("thumburl"):
            reject("missing_url")
            continue
        categories = [category.get("title", "") for category in page.get("categories", [])]
        score, reason = photo_assessment(city, title, categories, width, height)
        if score < MIN_PHOTO_SCORE:
            reject(reason or "below_score")
            continue
        photo = {
            "title": title,
            # Do not render a potentially huge original. Prepare a thumbnail only
            # after MIME/size/quality filtering, in a request for this file alone.
            "image_url": info.get("thumburl"),
            "source_url": info.get("descriptionurl", ""),
            "_quality": (score, width * height),
            "scene_key": _scene_key(city, title, categories),
        }
        family = _photo_family_key(title)
        variants = photo_families.setdefault(family, {})
        variant = (photo["scene_key"], photo_variant_key(title, categories, width, height))
        existing = variants.get(variant)
        if not existing or photo["_quality"] > existing["_quality"]:
            if existing:
                reject("same_family_view")
            variants[variant] = photo
        else:
            reject("same_family_view")

    photos = []
    for variants in photo_families.values():
        representatives = list(variants.values())
        # Quality is a floor and tie-breaker, not a weight in gameplay draws.
        if len(representatives) > 3:
            reject_count = len(representatives) - 3
            rejected["family_limit"] = rejected.get("family_limit", 0) + reject_count
            representatives = random.sample(representatives, 3)
        for photo in representatives:
            photo.pop("_quality", None)
            photos.append(photo)
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(filter_rejections=rejected, filename_families=len(photo_families))
    return photos


def _scene_key(city, title, categories):
    """Recognize named places from metadata, falling back to the filename family."""
    title_text = title.casefold()
    labels = []
    for category in categories:
        label = category.removeprefix("Category:").casefold()
        for name in [city[0], *city[1]]:
            label = label.replace(name.casefold(), " ")
        label = re.sub(r"[^\w\s]", " ", label)
        label = " ".join(label.split())
        if len(label) >= 4 and label in title_text and not re.search(
            r"\b(?:in|of|by|photograph|image|winter|summer|spring|autumn|night)\b", label
        ):
            labels.append(label)
    return max(labels, key=len) if labels else _photo_family_key(title)


def _refresh_photos(city, stale_cache=None):
    global _candidate_generation
    name = city[0]
    filtering = {}
    entries = prepared_question_snapshot()
    low_supply = len(entries) < 100 or len({entry["question"]["answer"] for entry in entries}) < 21
    stocked = {entry["question"]["image_id"] for entry in entries}
    def enough_candidates(photos):
        return sum(_photo_id(photo["title"]) not in stocked for photo in photos) >= 12
    photos, discovery = collect_city_photos(
        city, lambda params: _check_api_data(_api_get(params)),
        lambda city, pages: _select_photo_candidates(city, pages, filtering), commons_retry_delay,
        limit=PHOTO_CANDIDATE_LIMIT,
        search_retry_delay=commons_search_retry_delay,
        early_stop=enough_candidates if low_supply else None,
    )
    photo_statistics.record(name, "discovery", candidates=len(photos), **discovery, **filtering)

    if not photos and stale_cache:
        with _cache_lock:
            _photo_retry_after[name] = time.time() + EMPTY_CACHE_TTL_SECONDS
        return stale_cache["photos"]
    now = time.time()
    with _cache_lock:
        _photo_cache[name] = {
            # Revisit partial discovery soon to recover area/category variety.
            "expires_at": now + (CACHE_TTL_SECONDS if photos and not discovery.get("early_stopped")
                                  and not discovery.get("partial_failure")
                                  else EMPTY_CACHE_TTL_SECONDS),
            "stale_until": now + STALE_CACHE_TTL_SECONDS,
            "photos": photos,
        }
        _photo_retry_after.pop(name, None)
        _candidate_generation += 1
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


def _prepare_city_question(city, excluded, seen_images=()):
    """Try a bounded number of files, isolating thumbnail failures within this city."""
    photos = [
        photo.copy() for photo in _fetch_photos(city)
        if photo["title"] not in excluded and _photo_id(photo["title"]) not in excluded
    ]
    with _cache_lock:
        photos = [photo for photo in photos if _thumbnail_retry_after.get(photo["title"], 0) <= time.time()]
        stocked_scenes = {}
        for entry in _prepared_questions.get(city[0], {}).values():
            if entry["expires_at"] > time.time():
                key = entry["question"].get("scene_key", entry["question"]["image_id"])
                stocked_scenes[key] = stocked_scenes.get(key, 0) + 1
    unseen = [photo for photo in photos if _photo_id(photo["title"]) not in seen_images]
    photos = unseen or photos
    for _ in range(min(PHOTO_PREPARE_ATTEMPTS, len(photos))):
        minimum = min(stocked_scenes.get(photo.get("scene_key", photo["title"]), 0) for photo in photos)
        photo = random.choice([photo for photo in photos
                               if stocked_scenes.get(photo.get("scene_key", photo["title"]), 0) == minimum])
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
            "scene_key": photo.get("scene_key", _photo_family_key(photo["title"])),
        }
        with _cache_lock:
            _prepared_questions.setdefault(name, {})[question["image_id"]] = {
                "question": question.copy(), "expires_at": time.time() + PREPARED_QUESTION_TTL_SECONDS,
            }
        return question
    return None


def get_city_question(city, excluded_images=(), *, seen_images=()):
    """Prepare one named city so reservoir failures cannot discard other cities."""
    try:
        question = _prepare_city_question(city, set(excluded_images), set(seen_images))
        photo_statistics.record(city[0], "prepared" if question else "no_eligible_photo")
        return question
    except (OSError, ValueError, KeyError) as exc:
        photo_statistics.record(city[0], "prepare_error", last_error=str(exc))
        return None


def get_random_question(excluded_images=(), excluded_cities=(), *, seen_images=()):
    """Draw ready inventory first; generate only when no eligible stock exists."""
    cached = get_cached_question(excluded_images, excluded_cities, seen_images=seen_images, relax_cities=True)
    if cached is not None:
        return cached
    excluded = set(excluded_images)
    excluded_names = set(excluded_cities)
    available_cities = [city for city in CITIES if city[0] not in excluded_names] or CITIES
    for city in random.sample(available_cities, k=min(DYNAMIC_CITY_ATTEMPTS, len(available_cities))):
        if commons_retry_delay() > 0:
            break
        question = get_city_question(city, excluded, seen_images=seen_images)
        if question is not None:
            return question
    return get_cached_question(excluded_images, excluded_cities, seen_images=seen_images, relax_cities=True)


def candidate_cache_generation():
    with _cache_lock:
        return _candidate_generation


def candidate_photo_snapshot():
    """Copy bounded eligible candidates, retaining their original freshness."""
    now = time.time()
    with _cache_lock:
        return {
            "rule_version": CANDIDATE_RULE_VERSION,
            "generation": _candidate_generation,
            "cities": {
                name: {"expires_at": entry["expires_at"],
                       "stale_until": entry.get("stale_until", entry["expires_at"]),
                       "photos": [photo.copy() for photo in entry["photos"][:CANDIDATE_MAX_PER_CITY]]}
                for name, entry in _photo_cache.items()
                if entry.get("stale_until", entry["expires_at"]) > now
            },
        }


def restore_candidate_photos(data):
    """Restore validated candidates under current or explicitly compatible rules."""
    global _candidate_generation
    accepted_versions = {CANDIDATE_RULE_VERSION}
    if CANDIDATE_FILTER_VERSION == COMPATIBLE_FILTER_VERSION:
        accepted_versions |= COMPATIBLE_CANDIDATE_RULE_VERSIONS
    if not isinstance(data, dict) or data.get("rule_version") not in accepted_versions:
        return
    entries = data.get("cities")
    if not isinstance(entries, dict):
        return
    now = time.time()
    names = {city[0] for city in CITIES}
    restored = {}
    for name, entry in entries.items():
        if name not in names or not isinstance(entry, dict):
            continue
        expires = entry.get("expires_at")
        stale = entry.get("stale_until")
        photos = entry.get("photos")
        if (not isinstance(expires, (int, float)) or not isinstance(stale, (int, float))
                or not expires <= now + CACHE_TTL_SECONDS
                or not now < stale <= now + STALE_CACHE_TTL_SECONDS or stale < expires
                or not isinstance(photos, list) or len(photos) > CANDIDATE_MAX_PER_CITY):
            continue
        valid = []
        identities = set()
        for photo in photos:
            if (not isinstance(photo, dict) or not isinstance(photo.get("title"), str)
                    or not photo["title"] or photo["title"] in identities
                    or not isinstance(photo.get("source_url"), str)
                    or not isinstance(photo.get("scene_key"), str)
                    or not (photo.get("image_url") is None or isinstance(photo.get("image_url"), str))):
                continue
            identities.add(photo["title"])
            valid.append({key: photo.get(key) for key in ("title", "image_url", "source_url", "scene_key")})
        # A corrupt list must not turn a nonempty city into a fresh empty cache.
        if photos and not valid:
            continue
        restored[name] = {"expires_at": expires, "stale_until": stale, "photos": valid}
    with _cache_lock:
        for name, entry in restored.items():
            if _photo_cache.get(name, {}).get("expires_at", 0) < entry["expires_at"]:
                _photo_cache[name] = entry
                _candidate_generation += 1


def set_local_image_checker(checker):
    """Allow expired metadata only while a verified local thumbnail exists."""
    global _local_image_checker
    _local_image_checker = checker


def _usable_prepared(entry, now):
    return entry["expires_at"] > now or _local_image_checker(entry["question"])


def effective_city_exclusions(excluded_images=(), excluded_cities=()):
    """Relax city history only when strict stock is empty; never relax photos."""
    history = list(excluded_cities)
    excluded = set(excluded_images)
    now = time.time()
    with _cache_lock:
        available = {name for name, photos in _prepared_questions.items()
                     if any(image not in excluded and _usable_prepared(entry, now)
                            for image, entry in photos.items())}
    if available - set(history):
        return history
    # Keep a small random choice of older cities whenever supply permits it.
    older = available - set(history[-1:])
    target = min(2, len(older)) or 1
    for window in (10, 5, 2, 1, 0):
        if window > len(history):
            continue
        blocked = history[-window:] if window else []
        if len(available - set(blocked)) >= target:
            return blocked
    return history


def get_cached_question(excluded_images=(), excluded_cities=(), *, seen_images=(), relax_cities=False):
    """Pick prepared photos without network calls; optionally relax city history."""
    excluded = set(excluded_images)
    excluded_names = set(effective_city_exclusions(excluded_images, excluded_cities)
                         if relax_cities else excluded_cities)
    now = time.time()
    candidates = {}
    with _cache_lock:
        for name, photos in _prepared_questions.items():
            for image_id in list(photos):
                if not _usable_prepared(photos[image_id], now):
                    photos.pop(image_id)
            if name not in excluded_names:
                eligible = [entry["question"] for image_id, entry in photos.items() if image_id not in excluded]
                if eligible:
                    candidates[name] = eligible
        if not candidates:
            return None
        photos = candidates[random.choice(list(candidates))]
        unseen = [question for question in photos if question["image_id"] not in seen_images]
        return random.choice(unseen or photos).copy()


def has_unseen_cached(city, excluded_images, seen_images):
    excluded = set(excluded_images) | set(seen_images)
    with _cache_lock:
        return any(image_id not in excluded and _usable_prepared(entry, time.time())
                   for image_id, entry in _prepared_questions.get(city, {}).items())


def prepared_question_snapshot():
    """Copy unexpired prepared questions for replenishment and disk storage."""
    now = time.time()
    with _cache_lock:
        return [
            {"question": entry["question"].copy(), "expires_at": entry["expires_at"]}
            for photos in _prepared_questions.values() for entry in photos.values()
            if _usable_prepared(entry, now)
        ]


def restore_prepared_questions(entries):
    """Restore valid dynamic questions without extending their original lifetime."""
    names = {city[0] for city in CITIES}
    now = time.time()
    if not isinstance(entries, list):
        return
    with _cache_lock:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            question = entry.get("question")
            expires_at = entry.get("expires_at")
            if (not isinstance(question, dict)
                    or not isinstance(expires_at, (int, float))
                    or not 0 < expires_at <= now + PREPARED_QUESTION_TTL_SECONDS
                    or not isinstance(question.get("answer"), str)
                    or question["answer"] not in names
                    or question.get("is_dynamic") is not True
                    or not all(isinstance(question.get(key), str) and question[key]
                               for key in ("image_id", "image_url"))
                    or not isinstance(question.get("aliases"), list)
                    or not isinstance(question.get("credit"), list)
                    or len(question["credit"]) != 2
                    or not all(isinstance(value, str) for value in question["credit"])
                    or not all(isinstance(value, str) for value in question["aliases"])
                    or not isinstance(question.get("scene_key", ""), str)):
                continue
            if not _usable_prepared(entry, now):
                continue
            photos = _prepared_questions.setdefault(question["answer"], {})
            existing = photos.get(question["image_id"])
            if existing is None or existing["expires_at"] < expires_at:
                photos[question["image_id"]] = {
                    "question": question.copy(), "expires_at": expires_at,
                }
