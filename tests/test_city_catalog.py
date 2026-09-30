import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import app as game_app
from city_provider import CITIES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = PROJECT_ROOT / "static" / "images"


class CityCatalogTests(unittest.TestCase):
    def test_catalog_contains_exactly_one_hundred_unique_cities(self):
        names = [name for name, _aliases, _lat, _lon in CITIES]
        self.assertEqual(100, len(names))
        self.assertEqual(100, len(set(names)))

    def test_every_city_has_a_chinese_alias_and_valid_coordinates(self):
        for name, aliases, latitude, longitude in CITIES:
            with self.subTest(city=name):
                self.assertTrue(aliases)
                self.assertTrue(any(any("\u4e00" <= char <= "\u9fff" for char in alias) for alias in aliases))
                self.assertGreaterEqual(latitude, -90)
                self.assertLessEqual(latitude, 90)
                self.assertGreaterEqual(longitude, -180)
                self.assertLessEqual(longitude, 180)

    def test_local_fallback_is_a_catalog_subset_with_existing_images(self):
        catalog_names = {name for name, _aliases, _lat, _lon in CITIES}
        for city in game_app.cities:
            with self.subTest(city=city["answer"]):
                self.assertIn(city["answer"], catalog_names)
                self.assertTrue((IMAGE_DIR / city["image"]).is_file())
                ElementTree.parse(IMAGE_DIR / city["image"])

    def test_large_png_fallbacks_are_gone(self):
        for name in ("Chicago", "London", "Tokyo"):
            self.assertFalse((IMAGE_DIR / f"{name}.png").exists())
            self.assertTrue((IMAGE_DIR / f"{name}.svg").is_file())

    def test_homepage_and_answer_flow_work_offline(self):
        fallback = {
            "image": "Chicago.svg",
            "answer": "Chicago",
            "aliases": ["芝加哥"],
            "is_dynamic": False,
        }
        game_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        with patch.object(game_app, "get_random_question", return_value=None), patch.object(
            game_app, "get_local_question", return_value=fallback.copy()
        ), patch.object(game_app, "start_question_prefetch", return_value=None):
            client = game_app.app.test_client()
            response = client.get("/")
            self.assertEqual(200, response.status_code)

            with client.session_transaction() as session:
                question_id = session["current_question"]["question_id"]

            response = client.post(
                "/check",
                data={"guess": "芝加哥", "question_id": question_id},
            )
            self.assertEqual(200, response.status_code)
            self.assertIn(b"Correct", response.data)


if __name__ == "__main__":
    unittest.main()
