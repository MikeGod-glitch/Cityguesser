import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import app as game_app
import city_provider
from city_provider import CITIES
from local_photos import CREDITS, COMMONS, QUESTIONS
from question_fixtures import dynamic_question


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = PROJECT_ROOT / "static" / "images"


class CityCatalogTests(unittest.TestCase):
    def setUp(self):
        pause = patch("photo_sources.time.sleep")
        pause.start()
        self.addCleanup(pause.stop)
        city_provider._prepared_questions.clear()

    def tearDown(self):
        city_provider._prepared_questions.clear()

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

    def test_legacy_photo_metadata_is_a_catalog_subset_with_existing_images(self):
        catalog_names = {name for name, _aliases, _lat, _lon in CITIES}
        self.assertEqual(20, len(COMMONS))
        self.assertEqual(
            set(COMMONS),
            {city["answer"] for city in QUESTIONS},
        )
        for city in QUESTIONS:
            with self.subTest(city=city["answer"]):
                self.assertIn(city["answer"], catalog_names)
                self.assertIn("commons", city)
                self.assertIn(city["answer"], CREDITS)
                self.assertTrue((IMAGE_DIR / city["image"]).is_file())
                ElementTree.parse(IMAGE_DIR / city["image"])

    def test_large_png_fallbacks_are_gone(self):
        for name in ("Chicago", "London", "Tokyo"):
            self.assertFalse((IMAGE_DIR / f"{name}.png").exists())
            self.assertTrue((IMAGE_DIR / f"{name}.svg").is_file())

    def test_cached_dynamic_photo_and_answer_flow_work_offline(self):
        prepared = dynamic_question(name="Chicago")
        game_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        with patch.object(game_app, "get_random_question", return_value=None), patch.object(
            game_app, "get_cached_question", return_value=prepared.copy()
        ), patch.object(game_app, "start_question_prefetch", return_value=None):
            client = game_app.app.test_client()
            response = client.get("/play")
            self.assertEqual(200, response.status_code)
            self.assertIn(b"Question 1 / 10", response.data)
            self.assertNotIn(b"View on map", response.data)

            with client.session_transaction() as session:
                question_id = session["current_question"]["question_id"]

            response = client.post(
                "/check",
                data={"guess": "芝加哥", "question_id": question_id},
            )
            self.assertEqual(200, response.status_code)
            self.assertIn(b"Correct", response.data)
            self.assertIn(b"View on map", response.data)
            self.assertIn(b"openstreetmap.org", response.data)
            self.assertIn(b'target="_blank" rel="noopener noreferrer"', response.data)
            self.assertIn(b'class="result-actions has-map"', response.data)
            self.assertIn(b'class="secondary-button map-button"', response.data)
            self.assertLess(response.data.index(b"Next city"), response.data.index(b"View on map"))

    def test_map_url_uses_catalog_coordinates(self):
        url = game_app.get_map_url({"answer": "Chicago"})
        self.assertEqual(
            "https://www.openstreetmap.org/?mlat=41.8781&mlon=-87.6298"
            "#map=11/41.8781/-87.6298",
            url,
        )
        self.assertIsNone(game_app.get_map_url({"answer": "Unknown City"}))

    def test_dynamic_provider_excludes_recent_cities(self):
        excluded = [name for name, _aliases, _lat, _lon in CITIES[:-1]]
        photo = {
            "title": "Brisbane skyline.jpg",
            "image_url": "https://example.com/brisbane.jpg",
            "source_url": "https://example.com/source",
        }
        with patch.object(city_provider, "_fetch_photos", return_value=[photo]), patch.object(
            city_provider,
            "_add_credit",
            side_effect=lambda item: item | {"author": "Tester", "license": "CC0"},
        ):
            question = city_provider.get_random_question(excluded_cities=excluded)
        self.assertEqual("Brisbane", question["answer"])

    def test_dynamic_provider_tries_four_cities_before_falling_back(self):
        selected = CITIES[:4]
        photo = {
            "title": "City skyline.jpg",
            "image_url": "https://example.com/city.jpg",
            "source_url": "https://example.com/source",
        }
        with patch.object(city_provider.random, "sample", return_value=selected), patch.object(
            city_provider, "_fetch_photos", side_effect=[[], [], [], [photo]]
        ) as fetch, patch.object(
            city_provider,
            "_add_credit",
            side_effect=lambda item: item | {"author": "Tester", "license": "CC0"},
        ):
            question = city_provider.get_random_question()
        self.assertEqual(selected[-1][0], question["answer"])
        self.assertEqual(4, fetch.call_count)

    def test_similar_photo_filenames_share_a_family(self):
        self.assertEqual(
            city_provider._photo_family_key("File:Tejas Express 123 (2024).jpg"),
            city_provider._photo_family_key("File:Tejas Express 987 (2023).jpg"),
        )

    def test_fetch_photos_expands_and_deduplicates_candidate_pool(self):
        city = next(city for city in CITIES if city[0] == "Delhi")
        pages = []
        for number in (123, 987):
            pages.append({
                "title": f"File:Delhi city street {number} (2024).jpg",
                "categories": [{"title": "Streets in Delhi"}],
                "imageinfo": [{
                    "width": 1600,
                    "height": 900,
                    "mime": "image/jpeg",
                    "thumburl": f"https://example.com/{number}.jpg",
                    "descriptionurl": "https://example.com/source",
                }],
            })
        with city_provider._cache_lock:
            city_provider._photo_cache.pop("Delhi", None)
        try:
            with patch.object(
                city_provider,
                "_api_get",
                return_value={"query": {"pages": pages}},
            ) as api_get:
                photos = city_provider._fetch_photos(city)
            self.assertEqual(1, len(photos))
            self.assertEqual(
                city_provider.PHOTO_CANDIDATE_LIMIT,
                api_get.call_args_list[0].args[0]["ggslimit"],
            )
        finally:
            with city_provider._cache_lock:
                city_provider._photo_cache.pop("Delhi", None)

    def test_fetch_photos_accepts_portraits_but_rejects_tiny_images(self):
        city = next(city for city in CITIES if city[0] == "Tokyo")

        def page(title, width, height):
            return {
                "title": title,
                "categories": [{"title": "Streets in Tokyo"}],
                "imageinfo": [{
                    "width": width,
                    "height": height,
                    "mime": "image/jpeg",
                    "thumburl": f"https://example.com/{title}.jpg",
                    "descriptionurl": "https://example.com/source",
                }],
            }

        pages = [
            page("File:Tokyo vertical street.jpg", 480, 900),
            page("File:Tokyo tiny street.jpg", 400, 400),
        ]
        with city_provider._cache_lock:
            city_provider._photo_cache.pop("Tokyo", None)
        try:
            with patch.object(
                city_provider,
                "_api_get",
                return_value={"query": {"pages": pages}},
            ):
                photos = city_provider._fetch_photos(city)
            self.assertEqual(
                ["File:Tokyo vertical street.jpg"],
                [photo["title"] for photo in photos],
            )
        finally:
            with city_provider._cache_lock:
                city_provider._photo_cache.pop("Tokyo", None)

    def test_dynamic_provider_returns_none_when_commons_fails(self):
        with patch.object(city_provider, "_fetch_photos", side_effect=OSError):
            self.assertIsNone(city_provider.get_random_question())

    def test_indoor_reception_photo_is_rejected(self):
        city = next(city for city in CITIES if city[0] == "Lagos")
        score = city_provider._photo_score(
            city,
            "Lagos museum reception.jpg",
            ["City buildings in Lagos"],
            1600,
            900,
        )
        self.assertEqual(0, score)


class GameModeTests(unittest.TestCase):
    def setUp(self):
        game_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = game_app.app.test_client()

    def seed_question(self, *, mode="challenge", answered=0, correct=0, score=0, streak=0, best=0):
        city = dynamic_question(name="Chicago") | {
            "question_id": "question-token",
        }
        with self.client.session_transaction() as session:
            session["game_mode"] = mode
            session["game_stats"] = {
                "score": score,
                "streak": streak,
                "best_streak": best,
                "answered": answered,
                "correct": correct,
            }
            session["current_question"] = city
            session["question_resolved"] = False
            session["recent_cities"] = [city["answer"]]
        return city

    def test_tenth_answer_shows_results_and_cannot_be_counted_twice(self):
        self.seed_question(answered=9, correct=6, score=780, streak=1, best=3)
        with patch.object(game_app, "start_question_prefetch", return_value=None):
            response = self.client.post(
                "/check",
                data={"guess": "芝加哥", "question_id": "question-token"},
            )
            duplicate = self.client.post(
                "/check",
                data={"guess": "芝加哥", "question_id": "question-token"},
            )

        self.assertIn(b"Question 10 / 10", response.data)
        self.assertIn(b"View results", response.data)
        self.assertIn(b"View results", duplicate.data)
        with self.client.session_transaction() as session:
            self.assertEqual(10, session["game_stats"]["answered"])
            self.assertEqual(7, session["game_stats"]["correct"])
            self.assertEqual(900, session["game_stats"]["score"])

        results = self.client.get("/results")
        self.assertIn(b"Challenge complete", results.data)
        self.assertIn(b"7 / 10", results.data)
        self.assertIn(b"70%", results.data)
        self.assertIn(b"Play again", results.data)
        self.assertNotIn(b"View on map", results.data)

    def test_tenth_question_does_not_prefetch_an_eleventh(self):
        self.seed_question(answered=9, correct=6)
        with patch.object(game_app.question_prefetch.executor, "submit") as submit:
            response = self.client.get("/play")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Question 10 / 10", response.data)
        submit.assert_not_called()

    def test_reveal_finishes_challenge_as_an_incorrect_answer(self):
        self.seed_question(answered=9, correct=6, score=780, streak=2, best=3)
        intro = {"text": "Chicago introduction", "source_url": None}
        with patch.object(game_app, "get_city_intro", return_value=intro), patch.object(
            game_app, "start_question_prefetch", return_value=None
        ):
            response = self.client.post(
                "/reveal",
                data={"question_id": "question-token"},
            )
        self.assertIn(b"View results", response.data)
        self.assertIn(b"View on map", response.data)
        with self.client.session_transaction() as session:
            self.assertEqual(10, session["game_stats"]["answered"])
            self.assertEqual(6, session["game_stats"]["correct"])
            self.assertEqual(0, session["game_stats"]["streak"])

    def test_endless_mode_continues_past_ten_questions(self):
        self.seed_question(mode="endless", answered=10, correct=4, score=400)
        with patch.object(game_app, "start_question_prefetch", return_value=None), patch.object(
            game_app, "get_prefetched_question", return_value=None
        ):
            response = self.client.post(
                "/check",
                data={"guess": "Not Chicago", "question_id": "question-token"},
            )
        self.assertIn(b"Question 11", response.data)
        self.assertIn(b"Endless", response.data)
        self.assertIn(b"Next city", response.data)
        self.assertNotIn(b"View results", response.data)

    def test_reset_switches_modes_and_clears_round_state(self):
        self.seed_question(answered=5, correct=3, score=340)
        with self.client.session_transaction() as session:
            session["recent_images"] = [f"image-{index}" for index in range(25)]
            session["recent_cities"] = [f"City {index}" for index in range(25)]
        response = self.client.post("/reset", data={"mode": "endless"})
        self.assertEqual(302, response.status_code)
        with self.client.session_transaction() as session:
            self.assertEqual("endless", session["game_mode"])
            self.assertNotIn("game_stats", session)
            self.assertNotIn("current_question", session)
            self.assertEqual(
                [f"image-{index}" for index in range(25)],
                session["recent_images"],
            )
            self.assertEqual(
                [f"City {index}" for index in range(5, 25)],
                session["recent_cities"],
            )

    def test_results_redirects_before_challenge_is_complete(self):
        self.seed_question(answered=9, correct=6)
        response = self.client.get("/results")
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/play"))


if __name__ == "__main__":
    unittest.main()
