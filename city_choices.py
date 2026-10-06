"""Generate randomized multiple-choice questions from curated city profiles."""

import random

from city_catalog import CITIES, CITY_PROFILES


def generate_choices(answer, catalog=None):
    """Prefer related cities, while allowing many different combinations."""
    names = list(dict.fromkeys(catalog if catalog is not None else [city[0] for city in CITIES]))
    target = CITY_PROFILES[answer]
    candidates = [name for name in names if name != answer]
    related = [
        name for name in candidates
        if target["continents"] & CITY_PROFILES[name]["continents"]
        or target["styles"] & CITY_PROFILES[name]["styles"]
    ]
    closest = [
        name for name in related
        if target["continents"] & CITY_PROFILES[name]["continents"]
        and target["styles"] & CITY_PROFILES[name]["styles"]
    ]
    choices = [answer]
    for index in range(min(3, len(candidates))):
        # Guarantee one geographically and stylistically close distractor
        # whenever possible; keep the other two free to vary.
        pool = closest if index == 0 and closest else related or candidates
        weights = []
        for name in pool:
            profile = CITY_PROFILES[name]
            same_continent = bool(target["continents"] & profile["continents"])
            shared_styles = len(target["styles"] & profile["styles"])
            weights.append(1 + 4 * same_continent + 3 * shared_styles)
        selected = random.choices(pool, weights=weights, k=1)[0]
        choices.append(selected)
        candidates.remove(selected)
        if selected in related:
            related.remove(selected)
    random.shuffle(choices)
    return choices
