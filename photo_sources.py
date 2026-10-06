"""Bounded, city-scoped discovery shared by every city in the catalog."""

import math
import random
import time


def discovery_queries(city, limit=100):
    name, _aliases, lat, lon = city
    shared = {"action": "query", "prop": "categories|imageinfo", "cllimit": "max",
              "clshow": "!hidden", "iiprop": "url|size|mime"}
    points = [(0.0225, 0), (-0.0225, 0), (0, 0.0225), (0, -0.0225)]
    random.shuffle(points)
    longitude_scale = max(0.2, math.cos(math.radians(lat)))
    for index, (dy, dx) in enumerate([(0, 0), *points[:2]]):
        latitude = max(-89.9, min(89.9, lat + dy))
        longitude = (lon + dx / longitude_scale + 180) % 360 - 180
        yield "area", {**shared, "generator": "geosearch", "ggsprimary": "all",
                       "ggsnamespace": 6, "ggsradius": 5000 if index == 0 else 2500,
                       "ggslimit": limit, "ggscoord": f"{latitude}|{longitude}"}
    categories = [f"Streets in {name}", f"Buildings in {name}", f"Skylines of {name}"]
    for operator in ("deepcat", "incategory"):
        query = " OR ".join(f'{operator}:"{category}"' for category in categories)
        yield operator, {**shared, "generator": "search", "gsrnamespace": 6,
                         "gsrlimit": limit, "gsrsearch": f"({query}) filetype:bitmap"}


def collect_city_photos(city, api_get, select, retry_delay, *, limit=100, target=40):
    """Merge by file identity before family dedup; retain partial successes on errors."""
    pages = {}
    counts = {}
    last_error = None
    successful = 0
    previous_call = None
    for source, params in discovery_queries(city, limit):
        if retry_delay() > 0:
            break
        # Rich pools still sample three areas. Categories supplement sparse pools.
        if source in {"deepcat", "incategory"} and len(select(city, list(pages.values()))) >= target:
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
                if source != "area":
                    text = (page.get("title", "") + " " + " ".join(
                        c.get("title", "") for c in page.get("categories", []))).casefold()
                    if not any(label.casefold() in text for label in [city[0], *city[1]]):
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
    if not successful and last_error is not None:
        raise last_error
    if not successful and retry_delay() > 0:
        raise OSError("Photo discovery is waiting for the API retry period")
    return select(city, list(pages.values())), {"raw_files": len(pages), "sources": counts,
                                              "partial_failure": str(last_error) if last_error else None}
