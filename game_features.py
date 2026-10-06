"""Question hints and the Beijing-time daily boundary."""

from datetime import datetime, timedelta, timezone

from city_choices import CITY_PROFILES

BEIJING = timezone(timedelta(hours=8))


def today():
    return datetime.now(BEIJING).date().isoformat()


def question_hints(name):
    profile = CITY_PROFILES[name]
    region = " / ".join(sorted(profile["continents"]))
    second = {
        "Singapore": "An island city with a major international port.",
        "Hong Kong": "A harbour city on China's southern coast, with steep hills.",
        "Macau": "A coastal city with Portuguese-influenced architecture.",
    }.get(name, f"Country / region: {profile['country']}")
    return [f"Geographic region: {region}", second]
