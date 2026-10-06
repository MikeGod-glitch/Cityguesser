"""Small process-local supply counters and a bounded actual-display window."""

from collections import Counter, deque
from threading import Lock
import time
from city_catalog import CITIES

_cities = {}
_lock = Lock()


def record(city, event, *, image_id=None, **details):
    with _lock:
        entry = _cities.setdefault(city, {"events": Counter(), "displays": deque(maxlen=1000),
                                          "last": {}})
        entry["events"][event] += 1
        entry["last"].update(details)
        if image_id is not None:
            entry["displays"].append(image_id)


def snapshot(prepared):
    counts = Counter(entry["question"]["answer"] for entry in prepared)
    with _lock:
        rows = {}
        for city in {city[0] for city in CITIES} | counts.keys() | _cities.keys():
            entry = _cities.get(city, {"events": {}, "last": {}, "displays": []})
            frequencies = Counter(entry["displays"])
            displays = sum(frequencies.values())
            rows[city] = {**entry["last"], "prepared": counts[city], "events": dict(entry["events"]),
                          "display_window": displays, "unique_images": len(frequencies),
                          "repeat_fraction": (displays - len(frequencies)) / displays if displays else None,
                          "top_images": frequencies.most_common(5)}
        return {"updated_at": time.time(), "window_per_city": 1000, "cities": rows}
