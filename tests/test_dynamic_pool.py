import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import city_provider as provider
from dynamic_pool import DynamicQuestionPool


def entry(name="Paris", image="photo", expires_at=None):
    return {
        "question": {"answer": name, "aliases": [], "image_id": image,
                     "image_url": "https://example.test/photo", "source_url": None,
                     "credit": ["Author", "CC0"], "is_dynamic": True},
        "expires_at": expires_at or time.time() + 100,
    }


class DynamicPoolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "questions.json"
        cities = [city for city in provider.CITIES if city[0] in {"Paris", "London"}]
        self.pool = DynamicQuestionPool(self.path, cities=cities, minimum=1, target=1,
                                       batch_size=4, request_interval=0)
        saved = provider._prepared_questions.copy()
        provider._prepared_questions.clear()
        self.addCleanup(provider._prepared_questions.update, saved)
        self.addCleanup(provider._prepared_questions.clear)

    def test_disk_round_trip_preserves_expiry_credit_and_identity(self):
        original = entry()
        provider.restore_prepared_questions([original])
        self.pool._save()
        provider._prepared_questions.clear()
        with patch("dynamic_pool.Thread") as worker:
            self.pool.start()
        self.assertEqual([original], provider.prepared_question_snapshot())
        worker.assert_called_once()

    def test_restore_discards_expired_and_malformed_entries(self):
        malformed = entry()
        malformed["question"]["answer"] = []
        provider.restore_prepared_questions([
            entry(expires_at=time.time() - 1), malformed, {}, None, entry(),
        ])
        self.assertEqual(1, len(provider.prepared_question_snapshot()))

    def test_refill_excludes_stocked_cities_and_images_then_stops_when_full(self):
        calls = []
        def fetch(city, images):
            calls.append((city[0], list(images)))
            item = entry(city[0], str(len(calls)))
            provider.restore_prepared_questions([item])
            return item["question"]
        with patch("dynamic_pool.get_city_question", side_effect=fetch):
            self.pool._refill()
        self.assertEqual({"Paris", "London"}, {city for city, _ in calls})
        self.assertEqual([[], ["1"]], [images for _, images in calls])
        self.assertEqual(2, len(json.loads(self.path.read_text())))
        self.assertFalse(self.pool.running)
        self.assertGreater(self.pool.retry_at, time.monotonic())

    def test_unavailable_provider_stops_and_defers_retry(self):
        with patch("dynamic_pool.get_city_question", return_value=None) as fetch:
            self.pool._refill()
        self.assertEqual(2, fetch.call_count)
        with patch("dynamic_pool.Thread") as worker:
            self.pool.start()
        worker.assert_not_called()

    def test_refill_adds_photos_after_city_target_is_met(self):
        self.pool.minimum = 2
        self.pool.target = 2
        provider.restore_prepared_questions([entry(), entry("Paris", "other")])
        def fetch(city, images):
            self.assertEqual("London", city[0])
            item = entry(city[0], f"new-photo-{len(images)}")
            provider.restore_prepared_questions([item])
            return item["question"]
        with patch("dynamic_pool.get_city_question", side_effect=fetch) as fetch_mock:
            self.pool._refill()
        self.assertEqual(2, fetch_mock.call_count)
        self.assertEqual(4, len(provider.prepared_question_snapshot()))

    def test_failed_city_does_not_prevent_another_city_from_filling(self):
        def fetch(city, images):
            if city[0] == "Paris":
                return None
            item = entry("London", "new")
            provider.restore_prepared_questions([item])
            return item["question"]
        with patch("dynamic_pool.get_city_question", side_effect=fetch) as fetch_mock:
            self.pool._refill()
        self.assertEqual(2, fetch_mock.call_count)
        self.assertEqual("London", provider.prepared_question_snapshot()[0]["question"]["answer"])
        self.assertIn("Paris", self.pool.city_retry_at)

    def test_global_backoff_stops_all_replenishment(self):
        with patch("dynamic_pool.commons_retry_delay", return_value=90), \
             patch("dynamic_pool.get_city_question") as fetch:
            self.pool._refill()
        fetch.assert_not_called()
        self.assertGreater(self.pool.retry_at, time.monotonic() + 89)

    def test_statistics_report_inventory_and_targets(self):
        provider.restore_prepared_questions([entry()])
        self.pool._save_statistics()
        data = json.loads(self.path.with_name("photo-pool-stats.json").read_text())
        self.assertEqual(1, data["cities"]["Paris"]["prepared"])
        self.assertEqual(1, data["minimum_per_city"])

    def test_restored_cache_still_respects_city_and_image_history(self):
        provider.restore_prepared_questions([entry(), entry("London", "other")])
        self.assertEqual("London", provider.get_cached_question(excluded_images=["photo"])["answer"])
        self.assertIsNone(provider.get_cached_question(excluded_images=["photo"], excluded_cities=["London"]))

    def test_repeated_requests_start_only_one_worker(self):
        with patch("dynamic_pool.Thread") as worker:
            self.pool.start()
            self.pool.start()
        worker.assert_called_once()

    def test_full_pool_needs_no_background_fetch(self):
        provider.restore_prepared_questions([entry(), entry("London", "other")])
        with patch("dynamic_pool.Thread") as worker:
            self.pool.start()
        worker.assert_not_called()

    def test_corrupt_disk_cache_does_not_prevent_replenishment(self):
        self.path.write_text("broken")
        with patch("dynamic_pool.Thread") as worker, self.assertLogs("dynamic_pool", "WARNING"):
            self.pool.start()
        worker.assert_called_once()
