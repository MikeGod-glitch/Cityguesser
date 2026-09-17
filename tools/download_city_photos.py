"""Download the researched Commons photos into static/images when network access is available."""
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import commons  # noqa: E402

out_dir = Path(__file__).resolve().parents[1] / "static" / "images"
failed = []

for city, filename in commons.items():
    target = out_dir / f"{city.replace(' ', '')}.jpg"
    if target.exists():
        print(f"Already present: {target.name}")
        continue
    url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename)}?width=1280"
    try:
        request = Request(url, headers={"User-Agent": "CityGuessGame/1.0 (educational photo download)"})
        with urlopen(request, timeout=30) as response:
            data = response.read()
            if not response.headers.get("Content-Type", "").startswith("image/jpeg"):
                raise ValueError("unexpected response type")
            if not data.startswith(b"\xff\xd8\xff"):
                raise ValueError("invalid JPEG data")
        temp = target.with_suffix(".jpg.part")
        temp.write_bytes(data)
        temp.replace(target)
        print(f"Downloaded: {target.name}")
    except Exception as exc:
        failed.append(city)
        print(f"Failed: {city}: {exc}")

if failed:
    print("Could not download:", ", ".join(failed))
    sys.exit(1)
