"""Hints and immutable daily question manifests; no player accounts required."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile

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


def daily_questions(directory, date, build):
    """Publish a complete file atomically; concurrent builders use the same winner."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{date}.json"
    if not target.exists():
        questions = build(date)
        if (len(questions) != 10 or len({q['answer'] for q in questions}) != 10
                or len({q['image_id'] for q in questions}) != 10):
            raise ValueError("Daily challenge requires ten distinct cities and images")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(questions, handle, ensure_ascii=False)
        try:
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            temporary.unlink()
    return json.loads(target.read_text(encoding="utf-8"))
