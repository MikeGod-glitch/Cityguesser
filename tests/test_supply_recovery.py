import time
import tempfile
import os
import unittest
from unittest.mock import patch
from concurrent.futures import Future

import app as game
import city_provider as provider
from question_fixtures import dynamic_question
from image_cache import ThumbnailCache


def stock(*names, expired=False):
    return {name: {name: {"question": dynamic_question(name=name) | {"image_id": name},
                         "expires_at": time.time() + (-60 if expired else 60)}}
            for name in names}


class SupplyRecoveryTests(unittest.TestCase):
    def test_normal_supply_keeps_city_history(self):
        with patch.object(provider, "_prepared_questions", stock("Paris", "London")):
            for _ in range(20):
                question = provider.get_cached_question(excluded_cities=["Paris"], relax_cities=True)
                self.assertEqual("London", question["answer"])

    def test_low_supply_randomizes_older_cities_and_avoids_latest(self):
        with patch.object(provider, "_prepared_questions", stock("Paris", "London", "Tokyo")):
            draws = set()
            for _ in range(100):
                question = provider.get_cached_question(
                    excluded_cities=["Paris", "London", "Tokyo"], relax_cities=True)
                draws.add(question["answer"])
            self.assertEqual({"Paris", "London"}, draws)
            self.assertIsNone(provider.get_cached_question(
                excluded_images=["Paris", "London", "Tokyo"], relax_cities=True))

    def test_single_city_can_use_a_new_photo_without_network(self):
        with patch.object(provider, "_prepared_questions", stock("Paris")), \
                patch.object(provider, "get_city_question") as network:
            question = provider.get_random_question(excluded_images=["old-photo"], excluded_cities=["Paris"])
            self.assertEqual("Paris", question["answer"])
            network.assert_not_called()

    def test_relaxed_prefetch_is_accepted_by_foreground(self):
        with game.app.test_request_context(), \
                patch.object(provider, "_prepared_questions", stock("Paris", "London")):
            game.session.update(game_mode="endless", player_id="recovery-test",
                                recent_cities=["Paris", "London"], recent_images=[])
            future = Future()
            question = stock("Paris")["Paris"]["Paris"]["question"]
            future.set_result(question)
            game.question_prefetch.tasks["recovery-test"] = (future, time.monotonic())
            self.addCleanup(game.question_prefetch.cancel, "recovery-test")
            self.assertEqual(question, game.get_prefetched_question(consume=True))

    def test_expired_metadata_restores_only_with_local_image_and_keeps_expiry(self):
        entry = stock("Paris", expired=True)["Paris"]["Paris"]
        with patch.object(provider, "_prepared_questions", {}), \
                patch.object(provider, "_local_image_checker", return_value=True), \
                patch.object(provider, "get_city_question") as network:
            provider.restore_prepared_questions([entry])
            self.assertEqual(entry["question"], provider.get_random_question())
            self.assertEqual(entry["expires_at"], provider.prepared_question_snapshot()[0]["expires_at"])
            network.assert_not_called()
            with patch.object(provider, "_local_image_checker", return_value=False):
                self.assertIsNone(provider.get_cached_question())
                self.assertEqual([], provider.prepared_question_snapshot())

    def test_expired_remote_only_question_is_not_restored(self):
        entry = stock("Paris", expired=True)["Paris"]["Paris"]
        with patch.object(provider, "_prepared_questions", {}), \
                patch.object(provider, "_local_image_checker", return_value=False):
            provider.restore_prepared_questions([entry])
            self.assertEqual([], provider.prepared_question_snapshot())

    def test_restart_serves_expired_question_from_local_thumbnail(self):
        entry = stock("Paris", expired=True)["Paris"]["Paris"]
        with tempfile.TemporaryDirectory() as directory:
            cache = ThumbnailCache(directory, ttl_seconds=30 * 24 * 3600)
            key = cache.key(entry["question"]["image_url"])
            from pathlib import Path
            path = Path(directory) / (key + ".jpg")
            path.write_bytes(b"\xff\xd8\xffcached-photo")
            old = time.time() - 2 * 24 * 3600
            os.utime(path, (old, old))
            with patch.object(game, "thumbnail_cache", cache), \
                    patch.dict(game.app.config, TESTING=False), \
                    patch.object(provider, "_prepared_questions", {}):
                provider.restore_prepared_questions([entry])
                with game.app.test_request_context():
                    self.assertEqual("/photos/" + key,
                                     game.browser_image_url(provider.get_cached_question()))

    def test_legacy_candidate_migration_stops_if_filters_change(self):
        data = {"rule_version": next(iter(provider.COMPATIBLE_CANDIDATE_RULE_VERSIONS)),
                "cities": {"Paris": {"expires_at": time.time() + 60,
                                     "stale_until": time.time() + 120, "photos": []}}}
        with patch.object(provider, "_photo_cache", {}):
            provider.restore_candidate_photos(data)
            self.assertIn("Paris", provider._photo_cache)
            provider._photo_cache.clear()
            with patch.object(provider, "CANDIDATE_FILTER_VERSION", "changed-filters"):
                provider.restore_candidate_photos(data)
                self.assertEqual({}, provider._photo_cache)
