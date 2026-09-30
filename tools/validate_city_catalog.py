"""Validate the curated city catalog, optionally including Commons availability."""

import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cities as local_cities  # noqa: E402
from city_provider import CITIES, _fetch_photos  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = PROJECT_ROOT / "static" / "images"


def validate_catalog():
    errors = []
    names = [city[0] for city in CITIES]

    if len(CITIES) != 100:
        errors.append(f"expected 100 cities, found {len(CITIES)}")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"duplicate city names: {', '.join(duplicates)}")

    catalog_names = set(names)
    for name, aliases, latitude, longitude in CITIES:
        if not any(any("\u4e00" <= char <= "\u9fff" for char in alias) for alias in aliases):
            errors.append(f"{name}: missing Chinese alias")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            errors.append(f"{name}: invalid coordinates ({latitude}, {longitude})")

    for city in local_cities:
        if city["answer"] not in catalog_names:
            errors.append(f"local fallback is outside catalog: {city['answer']}")
        if not (IMAGE_DIR / city["image"]).is_file():
            errors.append(f"missing local image: {city['image']}")

    return errors


def check_commons(cities, delay_seconds):
    unavailable = []
    total = len(cities)
    for index, city in enumerate(cities, start=1):
        name = city[0]
        try:
            photos = _fetch_photos(city)
        except Exception as exc:  # Maintenance command: report and continue.
            print(f"[{index:3}/{total}] ERROR {name}: {exc}", flush=True)
            unavailable.append(name)
        else:
            print(f"[{index:3}/{total}] {name}: {len(photos)} suitable photos", flush=True)
            if not photos:
                unavailable.append(name)
        if delay_seconds:
            time.sleep(delay_seconds)
    return unavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-commons",
        action="store_true",
        help="query Wikimedia Commons for every city (slow and network-dependent)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="seconds to pause between Commons requests (default: 0.1)",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=1,
        help="one-based catalog position at which to start the Commons check",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="maximum number of cities to check",
    )
    args = parser.parse_args()

    errors = validate_catalog()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print(f"Catalog OK: {len(CITIES)} curated cities, {len(local_cities)} local fallbacks")
    if args.check_commons:
        start = max(args.start - 1, 0)
        selected = CITIES[start:]
        if args.limit is not None:
            selected = selected[:max(args.limit, 0)]
        unavailable = check_commons(selected, max(args.delay, 0))
        if unavailable:
            print("No suitable Commons photos:", ", ".join(unavailable))
            return 2
        print("Commons check OK for all cities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
