"""Bounded, city-scoped discovery shared by every city in the catalog."""

import math
import random
import time


def discovery_queries(city, limit=100):
    name, _aliases, lat, lon = city
    shared = {"action": "query", "prop": "categories|imageinfo", "cllimit": "max",
              "clshow": "!hidden", "iiprop": "url|size|mime"}
    # Two separated, randomized outer areas bring in neighborhoods beyond the
    # heavily overlapping center; request count remains bounded to five.
    angle = random.uniform(0, 2 * math.pi)
    points = []
    for direction in (angle, angle + math.pi):
        distance = random.uniform(.036, .072)  # Approximately 4–8 km.
        points.append((distance * math.sin(direction), distance * math.cos(direction)))
    longitude_scale = max(0.2, math.cos(math.radians(lat)))
    for index, (dy, dx) in enumerate([(0, 0), *points[:2]]):
        latitude = max(-89.9, min(89.9, lat + dy))
        longitude = (lon + dx / longitude_scale + 180) % 360 - 180
        yield "area", {**shared, "generator": "geosearch", "ggsprimary": "all",
                       "ggsnamespace": 6, "ggsradius": 5000 if index == 0 else 3500,
                       "ggslimit": limit, "ggscoord": f"{latitude}|{longitude}"}
    categories = [f"Streets in {name}", f"Buildings in {name}", f"Skylines of {name}"]
    categories.append(random.choice([f"Parks in {name}", f"Markets in {name}",
                                     f"Squares in {name}", f"Train stations in {name}",
                                     f"Residential buildings in {name}", f"Museums in {name}"]))
    for operator in ("deepcat", "incategory"):
        query = " OR ".join(f'{operator}:"{category}"' for category in categories)
        yield operator, {**shared, "generator": "search", "gsrnamespace": 6,
                         "gsrlimit": limit, "gsrsearch": f"({query}) filetype:bitmap"}


def collect_city_photos(city, api_get, select, retry_delay, *, limit=100, target=80,
                       search_retry_delay=None, early_stop=None):
    """Merge by file identity before family dedup; retain partial successes on errors."""
    pages = {}
    counts = {}
    location_rejections = set()
    last_error = None
    successful = 0
    previous_call = None
    early_stopped = False
    for source, params in discovery_queries(city, limit):
        if retry_delay() > 0:
            break
        if source != "area" and search_retry_delay and search_retry_delay() > 0:
            last_error = OSError("Photo search is waiting for the search retry period")
            continue
        # Always diversify geographically rich pools with one category query.
        # Direct categories remain a fallback for failed/empty deep searches.
        if (source == "incategory" and counts.get("deepcat", 0) > 0
                and len(select(city, list(pages.values()))) >= target):
            break
        if previous_call is not None:
            time.sleep(max(0, 2 - (time.monotonic() - previous_call)))
        previous_call = time.monotonic()
        try:
            data = api_get(params)
            if data.get("error"):
                raise ValueError("Photo discovery returned an API error")
            found = data.get("query", {}).get("pages", [])
            successful += 1
            counts[source] = counts.get(source, 0) + len(found)
            for page in found:
                if source != "area" or params.get("ggsradius") == 3500:
                    text = (page.get("title", "") + " " + " ".join(
                        c.get("title", "") for c in page.get("categories", []))).casefold()
                    if not any(label.casefold() in text for label in [city[0], *city[1]]):
                        location_rejections.add(page.get("title", ""))
                        continue
                key = page.get("title")
                if not key:
                    continue
                if key not in pages:
                    pages[key] = page
                else:
                    # The same file can carry more categories in a later response.
                    previous = pages[key]
                    cats = {c["title"]: c for c in previous.get("categories", [])}
                    cats.update({c["title"]: c for c in page.get("categories", [])})
                    pages[key] = {**previous, **page, "categories": list(cats.values())}
        except (OSError, ValueError, KeyError) as exc:
            last_error = exc
        if early_stop and early_stop(select(city, list(pages.values()))):
            early_stopped = True
            break
    if not successful and last_error is not None:
        raise last_error
    if not successful and retry_delay() > 0:
        raise OSError("Photo discovery is waiting for the API retry period")
    return select(city, list(pages.values())), {"raw_files": len(pages), "sources": counts,
                                              "location_rejections": len(location_rejections),
                                              "partial_failure": str(last_error) if last_error else None,
                                              "early_stopped": early_stopped}
