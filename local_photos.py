"""Fixed fallback photo sources and credits, kept in game-pool order."""

from pathlib import Path
from urllib.parse import quote

# See static/images/SOURCES.md for provenance.
LOCAL_PHOTOS = [
    ('Chicago', 'Chicago Skyline - Dusk.JPG', 'Alanthebox', 'CC0 1.0'),
    ('London', 'London Skyline from London Bridge at dusk.jpg', 'Donnchadh H', 'CC BY 2.0'),
    ('Tokyo', 'Tokyo Skyline.jpg', 'Ningyou', 'Public domain'),
    ('Paris', 'The Eiffel Tower in Paris.jpg', 'Jeong seolah', 'CC0 1.0'),
    ('New York', 'NYC skyline empire.jpg', 'Matthew Wiebe', 'CC0 1.0'),
    ('San Francisco', 'GoldenGateBridge.jpg', 'Peter Craig', 'Public domain'),
    ('Rome', 'Colosseum romanum.jpg', 'maiterozas', 'CC0 1.0'),
    ('Venice', 'Rialto Bridge, Venice Italy.jpg', 'Peter Glyn', 'CC0 1.0'),
    ('Barcelona', 'Panoramic View of the Basílica de la Sagrada Família.jpg', 'Karanchawla30', 'CC BY-SA 4.0'),
    ('Berlin', 'The Brandenburg Gate.jpg', 'Nirmal Dulal', 'CC BY-SA 4.0'),
    ('Amsterdam', 'Canal houses and Oude Kerk at blue hour with water reflection in Damrak Amsterdam Netherlands.jpg', 'Basile Morin', 'CC BY-SA 4.0'),
    ('Sydney', 'The Sydney Opera House. Australia.jpg', 'Bernard Spragg. NZ', 'CC0 1.0'),
    ('Singapore', 'Singapore Marina Bay Sands Skyline.jpg', 'Aerosecure hub official', 'CC BY-SA 4.0'),
    ('Hong Kong', 'Victoria Harbour skyline, Hong Kong (2008).jpg', 'ImMrDrake', 'CC BY 3.0'),
    ('Dubai', 'Burj Khalifa Image.jpg', 'Meandmybrix', 'CC0 1.0'),
    ('Shanghai', 'ShanghaiPearlTower.jpg', 'Robpics69', 'Public domain'),
    ('Beijing', 'A panoramic view of the Forbidden City.jpg', 'Wuhuanqi', 'CC BY-SA 4.0'),
    ('Toronto', 'CN Tower Toronto. (48938595411).jpg', 'Bernard Spragg. NZ', 'CC0 1.0'),
    ('Rio de Janeiro', 'Christ-Redeemer-Rio-de-Janeiro.jpg', 'acediscovery', 'CC BY 4.0'),
    ('Istanbul', 'Exterior of Hagia Sophia-.jpg', 'Yair Haklai', 'CC BY-SA 4.0'),
]

COMMONS = {name: filename for name, filename, _author, _license in LOCAL_PHOTOS}
CREDITS = {name: (author, license_name) for name, _filename, author, license_name in LOCAL_PHOTOS}
QUESTIONS = [
    {"image": name.replace(" ", "") + ".svg", "answer": name, "commons": filename}
    for name, filename in COMMONS.items()
]


def question_image_context(question, static_folder):
    """Resolve the existing image/source/credit presentation for either question type."""
    if question.get("is_dynamic"):
        return {key: question[key] for key in ("image_url", "source_url", "credit")}

    context = {"image_url": f"/static/images/{question['image']}", "source_url": None, "credit": None}
    if "commons" not in question:
        return context

    filename = quote(question["commons"])
    local_photo = Path(static_folder) / "images" / f"{question['answer'].replace(' ', '')}.jpg"
    context.update(
        image_url=(f"/static/images/{local_photo.name}" if local_photo.is_file()
                   else f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width=1280"),
        source_url=f"https://commons.wikimedia.org/wiki/File:{filename}",
        credit=CREDITS[question["answer"]],
    )
    return context
