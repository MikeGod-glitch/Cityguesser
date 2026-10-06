import random
import time
import unittest
from collections import Counter
from unittest.mock import patch

import city_provider as provider
from dynamic_pool import DynamicQuestionPool
from photo_rules import photo_assessment, photo_score
from test_photo_preparation import photo_page


class PhotoDiversityTests(unittest.TestCase):
    def setUp(self):
        self.city = next(city for city in provider.CITIES if city[0] == "Paris")

    def score(self, title, categories=()):
        return photo_score(self.city, "File:" + title + ".jpg", categories, 1600, 900)

    def test_expanded_city_subjects_and_city_gardens_pass_without_quality_awards(self):
        for title in ("Paris panorama", "Paris residential neighborhood", "Paris market",
                      "Paris park", "Paris garden", "Paris aerial view", "Paris promenade",
                      "Paris museum exterior", "Paris mural"):
            with self.subTest(title=title):
                self.assertGreaterEqual(self.score(title), provider.MIN_PHOTO_SCORE)

    def test_noisy_indoor_or_nature_categories_do_not_veto_explicit_city_scene(self):
        for title in ("Paris street", "Paris park", "Paris museum exterior"):
            with self.subTest(title=title):
                self.assertGreaterEqual(self.score(title, ["Category:Interiors", "Category:Trees"]), 5)
        self.assertGreaterEqual(self.score("Paris garden", ["Category:Botanical gardens"]), 5)

    def test_non_scenes_and_explicit_unsuitable_subjects_remain_rejected(self):
        for title in ("Paris map", "Paris flag", "Paris museum reception", "Paris interior",
                      "Paris flower", "Paris bird", "Paris portrait", "Paris food",
                      "Paris door detail", "Paris close-up", "garden"):
            with self.subTest(title=title):
                self.assertEqual(0, self.score(title))
        self.assertEqual(0, self.score("Paris image", ["Category:Interiors", "Category:Buildings in Paris"]))

    def test_unchanged_format_pixel_and_location_evidence_floors(self):
        tiny = photo_page("File:Paris panorama.jpg")
        tiny["imageinfo"][0].update(width=400, height=400)
        unsupported = photo_page("File:Paris city view.tiff", "image/tiff")
        self.assertEqual([], provider._select_photo_candidates(self.city, [tiny, unsupported]))
        self.assertEqual(0, self.score("garden", ["Category:Gardens"]))

    def test_same_numbered_view_keeps_one_but_explicit_time_variants_keep_three(self):
        same_view = [photo_page(f"File:Paris street {i} (2024).jpg") for i in range(10)]
        self.assertEqual(1, len(provider._select_photo_candidates(self.city, same_view)))
        varied = [photo_page(f"File:Paris street {i} ({year}).jpg")
                  for i, year in enumerate(range(2010, 2020))]
        rng = random.Random(23)
        with patch.object(provider.random, "sample", side_effect=rng.sample):
            results = [provider._select_photo_candidates(self.city, varied) for _ in range(20)]
        self.assertTrue(all(len(result) == 3 for result in results))
        self.assertEqual(10, len({p["title"] for result in results for p in result}))
        identical = [varied[0], varied[0]]
        self.assertEqual(1, len(provider._select_photo_candidates(self.city, identical)))

    def test_different_aspects_or_bracketed_views_are_preserved(self):
        landscape = photo_page("File:Paris street 01.jpg")
        portrait = photo_page("File:Paris street 02.jpg")
        portrait["imageinfo"][0].update(width=900, height=1600)
        self.assertEqual(2, len(provider._select_photo_candidates(self.city, [landscape, portrait])))
        photos = [photo_page("File:Paris street (night).jpg"), photo_page("File:Paris street (day).jpg")]
        self.assertEqual(2, len(provider._select_photo_candidates(self.city, photos)))

    def test_rejection_diagnostics_account_for_each_file(self):
        pages = [photo_page("File:Paris map.jpg"), photo_page("File:Paris interior.jpg"),
                 photo_page("File:Paris garden.jpg"), photo_page("File:Paris street 1.jpg"),
                 photo_page("File:Paris street 2.jpg")]
        stats = {}
        accepted = provider._select_photo_candidates(self.city, pages, stats)
        self.assertEqual(2, len(accepted))
        self.assertEqual({"non_photo_subject": 1, "unsuitable_title": 1, "same_family_view": 1},
                         stats["filter_rejections"])
        self.assertEqual(len(pages), len(accepted) + sum(stats["filter_rejections"].values()))
        self.assertEqual((0, "non_photo_subject"),
                         photo_assessment(self.city, "File:Paris map.jpg", [], 1600, 900))

    def test_random_city_weights_do_not_depend_on_inventory_size(self):
        pool = {}
        for city, count in (("Paris", 3), ("London", 24)):
            pool[city] = {str(i): {"question": {"answer": city, "image_id": str(i)},
                                   "expires_at": time.time() + 100} for i in range(count)}
        rng = random.Random(77)
        with patch.object(provider, "_prepared_questions", pool), \
             patch.object(provider.random, "choice", side_effect=rng.choice):
            counts = Counter(provider.get_cached_question()["answer"] for _ in range(4000))
        self.assertGreater(counts["Paris"], 1800)
        self.assertLess(counts["Paris"], 2200)

    def test_expanded_stock_is_seen_once_before_recycling_and_recent_ids_stay_blocked(self):
        pool = {"Paris": {str(i): {"question": {"answer": "Paris", "image_id": str(i)},
                                  "expires_at": time.time() + 100} for i in range(24)}}
        with patch.object(provider, "_prepared_questions", pool):
            seen = []
            for _ in range(24):
                question = provider.get_cached_question(seen_images=seen)
                self.assertNotIn(question["image_id"], seen)
                seen.append(question["image_id"])
            self.assertIsNone(provider.get_cached_question(excluded_images=seen, seen_images=seen))
        defaults = DynamicQuestionPool("unused.json")
        self.assertEqual((12, 24), (defaults.minimum, defaults.target))

    def test_new_stock_lifetime_survives_restore_without_extending_expiration(self):
        question = {"answer": "Paris", "aliases": [], "image_id": "day-long",
                    "image_url": "https://example.test/photo", "is_dynamic": True,
                    "credit": ["Author", "CC0"]}
        expires = time.time() + 12 * 3600
        with patch.object(provider, "_prepared_questions", {}):
            provider.restore_prepared_questions([{"question": question, "expires_at": expires}])
            self.assertEqual(expires, provider.prepared_question_snapshot()[0]["expires_at"])


if __name__ == "__main__":
    unittest.main()
