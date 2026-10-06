"""Network-free dynamic questions for gameplay tests."""

import secrets

from city_catalog import CITIES


def dynamic_question(excluded_images=(), excluded_cities=(), *, name=None):
    city = next(city for city in CITIES if (city[0] == name if name else city[0] not in excluded_cities))
    return {"answer": city[0], "aliases": city[1], "image_id": secrets.token_hex(8),
            "image_url": "https://example.test/city.jpg", "source_url": "https://example.test/source",
            "credit": ["Photographer", "CC0"], "is_dynamic": True}
