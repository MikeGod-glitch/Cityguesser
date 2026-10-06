from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import json
from pathlib import Path
import tempfile
from threading import Event, Lock
import time
import unittest
from unittest.mock import patch

import city_provider as provider
from dynamic_pool import DynamicQuestionPool
from test_dynamic_pool import entry


class SupplyTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "questions.json"
        for name in ("_photo_cache", "_credit_cache", "_prepared_questions",
                     "_photo_retry_after", "_thumbnail_retry_after", "_api_retry_after"):
            replacement = patch.object(provider, name, {})
            replacement.start()
            self.addCleanup(replacement.stop)
        for name in ("_commons_next_request_at", "_candidate_generation"):
            replacement = patch.object(provider, name, 0)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.city = next(city for city in provider.CITIES if city[0] == "Paris")

    def seed_candidates(self):
        candidate = {"title": "File:Paris street.jpg", "image_url": None,
                     "source_url": "https://commons.wikimedia.org/wiki/File:Paris_street.jpg",
                     "scene_key": "paris street"}
        provider._photo_cache["Paris"] = {
            "photos": [candidate], "expires_at": time.time() + 100,
            "stale_until": time.time() + 500,
        }
        provider._candidate_generation += 1
        return provider._photo_cache["Paris"].copy()


class CandidatePersistenceTests(SupplyTestCase):
    def test_disk_restore_reuses_candidates_without_discovery_or_expiry_extension(self):
        original = self.seed_candidates()
        pool = DynamicQuestionPool(self.path, cities=[self.city], target=1, minimum=1)
        provider.restore_prepared_questions([entry()])
        pool._save()
        provider._photo_cache.clear()
        provider._prepared_questions.clear()
        restored_pool = DynamicQuestionPool(self.path, cities=[self.city], target=1, minimum=1)
        with patch("dynamic_pool.Thread") as worker:
            restored_pool.start()
        worker.assert_not_called()
        self.assertEqual(original, provider._photo_cache["Paris"])
        provider._prepared_questions.clear()
        metadata = {"query": {"pages": [{"imageinfo": [{
            "thumburl": "https://thumb.wikimedia.org/photo.jpg",
            "extmetadata": {"Artist": {"value": "Author"}, "LicenseShortName": {"value": "CC0"}},
        }]}]}}
        with patch.object(provider, "_refresh_photos") as discover, \
             patch.object(provider, "_api_get", return_value=metadata) as metadata_fetch:
            question = provider.get_city_question(self.city)
        discover.assert_not_called()
        metadata_fetch.assert_called_once()
        self.assertEqual("Paris", question["answer"])
        self.assertEqual(["Author", "CC0"], question["credit"])

    def test_rule_changes_expired_future_and_malformed_candidates_are_ignored(self):
        self.seed_candidates()
        data = provider.candidate_photo_snapshot()
        invalid = []
        changed = json.loads(json.dumps(data))
        changed["rule_version"] = "old-rules"
        invalid.append(changed)
        for change in ({"stale_until": time.time() - 1},
                       {"expires_at": time.time() + provider.CACHE_TTL_SECONDS + 100},
                       {"photos": [None]}, {"photos": []}):
            item = json.loads(json.dumps(data))
            item["cities"]["Paris"].update(change)
            if change == {"photos": []}:
                item["cities"] = {"Unknown": item["cities"]["Paris"]}
            invalid.append(item)
        for item in invalid:
            with self.subTest(item=item):
                provider._photo_cache.clear()
                provider.restore_candidate_photos(item)
                self.assertEqual({}, provider._photo_cache)

    def test_expired_freshness_is_retained_only_as_stale_failure_fallback(self):
        original = self.seed_candidates()
        provider._photo_cache["Paris"]["expires_at"] = time.time() - 1
        data = provider.candidate_photo_snapshot()
        provider._photo_cache.clear()
        provider.restore_candidate_photos(data)
        with patch.object(provider, "_refresh_photos", side_effect=OSError("offline")) as refresh:
            photos = provider._fetch_photos(self.city)
        refresh.assert_called_once()
        self.assertEqual(original["photos"], photos)
        self.assertLess(provider._photo_cache["Paris"]["expires_at"], time.time())

    def test_discovered_candidates_are_saved_even_if_question_preparation_fails(self):
        pool = DynamicQuestionPool(self.path, cities=[self.city], batch_size=1)
        def fetch(*args):
            self.seed_candidates()
            return None
        with patch("dynamic_pool.get_city_question", side_effect=fetch):
            pool._refill()
        saved = json.loads(pool.candidate_path.read_text())
        self.assertEqual("File:Paris street.jpg", saved["cities"]["Paris"]["photos"][0]["title"])

    def test_candidate_file_is_not_rewritten_without_new_discovery(self):
        self.seed_candidates()
        pool = DynamicQuestionPool(self.path)
        with patch.object(pool, "_write", wraps=pool._write) as write:
            pool._save_candidates()
            pool._save_candidates()
            self.assertEqual(1, write.call_count)
            self.seed_candidates()
            pool._save_candidates()
            self.assertEqual(2, write.call_count)

    def test_corrupt_candidate_file_does_not_discard_ready_inventory(self):
        pool = DynamicQuestionPool(self.path, cities=[self.city], target=1, minimum=1)
        pool.path.write_text(json.dumps([entry()]))
        pool.candidate_path.write_text("broken")
        with patch("dynamic_pool.Thread") as worker, self.assertLogs("dynamic_pool", "WARNING"):
            pool.start()
        worker.assert_not_called()
        self.assertEqual("Paris", provider.get_cached_question()["answer"])


class ContinuousPoolTests(SupplyTestCase):
    def test_multiple_batches_fill_without_further_page_requests_and_stop_cleanly(self):
        cities = [city for city in provider.CITIES if city[0] in {"Paris", "London"}]
        pool = DynamicQuestionPool(self.path, cities=cities, minimum=1, target=2,
                                  batch_size=1, retry_seconds=0, request_interval=0)
        filled = Event()
        calls = []
        def fetch(city, images):
            calls.append((city[0], tuple(images)))
            prepared = entry(city[0], f"new-{len(calls)}")
            provider.restore_prepared_questions([prepared])
            if len(calls) == 4:
                filled.set()
            return prepared["question"]
        with patch("dynamic_pool.get_city_question", side_effect=fetch):
            pool.start()
            try:
                self.assertTrue(filled.wait(3), "Background batches stopped without another request")
            finally:
                pool.stop()
        self.assertIsNone(pool.worker)
        self.assertFalse(pool.running)
        self.assertEqual(4, len(calls))
        self.assertNotEqual(calls[0][0], calls[1][0])  # Fill low-stock cities first.
        self.assertEqual(4, len(json.loads(pool.path.read_text())))

    def test_idle_and_global_backoff_sleep_without_network_requests(self):
        for delay in (0, 90):
            with self.subTest(delay=delay):
                pool = DynamicQuestionPool(self.path, cities=[self.city], target=1, minimum=1)
                provider.restore_prepared_questions([entry()])
                waits = []
                def pause(seconds):
                    waits.append(seconds)
                    pool.stop_event.set()
                pool.pause = pause
                with patch("dynamic_pool.commons_retry_delay", return_value=delay), \
                     patch("dynamic_pool.get_city_question") as fetch:
                    pool._run()
                fetch.assert_not_called()
                self.assertEqual([delay or 60], waits)

    def test_global_backoff_does_not_mark_city_as_failed_for_five_minutes(self):
        pool = DynamicQuestionPool(self.path, cities=[self.city])
        with patch("dynamic_pool.commons_retry_delay", side_effect=[0, 90, 90]), \
             patch("dynamic_pool.get_city_question", return_value=None):
            pool._refill()
        self.assertEqual({}, pool.city_retry_at)
        self.assertGreater(pool.retry_at, time.monotonic() + 89)


class CandidateSchedulingTests(SupplyTestCase):
    def seed_stock(self, name, count):
        provider.restore_prepared_questions([entry(name, f"{name}-{i}") for i in range(count)])

    def seed_many_candidates(self, name, count=20):
        provider._photo_cache[name] = {
            "photos": [{"title": f"File:{name} street {i}.jpg",
                        "image_url": None, "source_url": "https://example.test/source",
                        "scene_key": f"street-{i}"} for i in range(count)],
            "expires_at": time.time() + 100, "stale_until": time.time() + 500,
        }

    def run_batch(self, pool, fail=None):
        calls = []
        def fetch(city, images):
            calls.append(city[0])
            if city[0] == fail:
                return None
            item = entry(city[0], f"new-{len(calls)}")
            self.assertNotIn(item["question"]["image_id"], images)
            provider.restore_prepared_questions([item])
            return item["question"]
        with patch("dynamic_pool.get_city_question", side_effect=fetch):
            pool._refill()
        return calls

    def pool(self, batch_size=12):
        cities = [city for city in provider.CITIES if city[0] in {"Paris", "London"}]
        return DynamicQuestionPool(self.path, cities=cities, batch_size=batch_size,
                                   request_interval=0)

    def test_reuses_richer_stock_candidates_but_reserves_a_low_stock_turn(self):
        self.seed_stock("Paris", 3)
        self.seed_stock("London", 2)
        self.seed_many_candidates("Paris")
        calls = self.run_batch(self.pool())
        self.assertEqual(["Paris"] * 9, calls[:9])
        self.assertEqual("London", calls[9])

    def test_short_runs_rotate_between_cities_with_candidates(self):
        for name in ("Paris", "London"):
            self.seed_stock(name, 2)
            self.seed_many_candidates(name)
        calls = self.run_batch(self.pool(batch_size=6))
        self.assertEqual(1, len(set(calls[:3])))
        self.assertEqual(1, len(set(calls[3:])))
        self.assertNotEqual(calls[0], calls[3])

    def test_empty_city_gets_coverage_before_reusing_other_city_candidates(self):
        self.seed_stock("Paris", 2)
        self.seed_many_candidates("Paris")
        self.assertEqual(["London"], self.run_batch(self.pool(batch_size=1)))

    def test_expired_exhausted_and_temporarily_failed_candidates_do_not_take_priority(self):
        self.seed_many_candidates("Paris", 1)
        photo = provider._photo_cache["Paris"]["photos"][0]
        pool = self.pool()
        stocked = [entry("Paris", provider._photo_id(photo["title"]))]
        self.assertEqual(set(), pool._candidate_cities(stocked))
        provider._photo_cache["Paris"]["expires_at"] = time.time() - 1
        self.assertEqual(set(), pool._candidate_cities([]))
        provider._photo_cache["Paris"]["expires_at"] = time.time() + 100
        self.seed_stock("Paris", 2)
        self.seed_stock("London", 2)
        calls = self.run_batch(self.pool(batch_size=4), fail="Paris")
        self.assertEqual(1, calls.count("Paris"))
        self.assertIn("London", calls)

    def test_burst_stops_at_target_and_global_backoff(self):
        self.seed_stock("Paris", 2)
        self.seed_many_candidates("Paris")
        pool = DynamicQuestionPool(self.path, cities=[self.city], minimum=3, target=3,
                                   batch_size=12, request_interval=0)
        self.assertEqual(["Paris"], self.run_batch(pool))
        provider._prepared_questions.clear()
        pool = self.pool()
        with patch("dynamic_pool.commons_retry_delay", side_effect=[0, 90, 90]), \
             patch("dynamic_pool.get_city_question", return_value=None) as fetch:
            pool._refill()
        self.assertEqual(1, fetch.call_count)
        self.assertEqual({}, pool.city_retry_at)


class CommonsRequestSchedulingTests(SupplyTestCase):
    def test_parallel_workers_share_spacing_and_cannot_overlap_requests(self):
        clock = [0.0]
        started = []
        active = 0
        maximum = 0
        observation = Lock()
        real_sleep = time.sleep
        def sleep(seconds):
            clock[0] += seconds
        def network(*args, **kwargs):
            nonlocal active, maximum
            with observation:
                active += 1
                maximum = max(maximum, active)
                started.append(clock[0])
            real_sleep(.01)
            with observation:
                active -= 1
            return StringIO('{"query": {}}')
        # Only patch the pacing sleep through a callback that advances fake time;
        # the response is synchronous within the shared request lock.
        with patch.object(provider.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(provider.time, "sleep", side_effect=sleep), \
             patch.object(provider, "urlopen", side_effect=network):
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(provider._api_get, {"action": "query"}) for _ in range(3)]
                for future in futures:
                    future.result(timeout=3)
        self.assertEqual(1, maximum)
        self.assertTrue(all(b - a >= provider.COMMONS_REQUEST_INTERVAL_SECONDS
                            for a, b in zip(started, started[1:])))

    def test_search_overload_publishes_shared_backoff_and_blocks_following_request(self):
        overloaded = '{"error":{"code":"cirrussearch-too-busy-error","info":"busy"}}'
        with patch.object(provider, "urlopen", return_value=StringIO(overloaded)) as network:
            with self.assertRaises(provider.CommonsApiError):
                provider._api_get({"action": "query"})
            with self.assertRaises(provider.CommonsApiError):
                provider._api_get({"action": "query"})
        network.assert_called_once()
        self.assertGreater(provider.commons_retry_delay(), 29)

    def test_wikipedia_does_not_wait_for_commons_budget_or_backoff(self):
        provider._api_retry_after[provider.COMMONS_API] = time.time() + 90
        with patch.object(provider, "urlopen", return_value=StringIO('{"query": {}}')), \
             patch.object(provider.time, "sleep") as sleep:
            self.assertEqual({"query": {}}, provider._api_get({}, endpoint=provider.EN_WIKIPEDIA_API))
        sleep.assert_not_called()

    def test_ready_inventory_avoids_discovery_and_preserves_history_and_seen_rules(self):
        provider.restore_prepared_questions([entry("Paris", "seen"), entry("Paris", "new"),
                                             entry("London", "excluded")])
        with patch.object(provider, "get_city_question") as prepare:
            question = provider.get_random_question(excluded_cities=["London"], seen_images=["seen"])
        prepare.assert_not_called()
        self.assertEqual("new", question["image_id"])
        with patch.object(provider, "get_city_question", return_value=None) as prepare:
            self.assertIsNone(provider.get_random_question(excluded_images=["seen", "new", "excluded"]))
        self.assertGreater(prepare.call_count, 0)


if __name__ == "__main__":
    unittest.main()
