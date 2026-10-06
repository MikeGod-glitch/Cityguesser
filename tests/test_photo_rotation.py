from concurrent.futures import Future
import time
import unittest
from unittest.mock import patch

import app as game
import city_provider as provider
from photo_rotation import PhotoRotation
import photo_statistics
from question_fixtures import dynamic_question


class PhotoRotationTests(unittest.TestCase):
    def test_player_cycles_are_isolated_and_reset_when_a_photo_repeats(self):
        rotation = PhotoRotation()
        rotation.record("a", "Paris", "one")
        rotation.record("a", "Paris", "two")
        rotation.record("b", "Paris", "other")
        self.assertEqual({"one", "two"}, set(rotation.seen("a")))
        rotation.record("a", "Paris", "one")
        self.assertEqual(("one",), rotation.seen("a"))
        self.assertEqual(("other",), rotation.seen("b"))

    def test_expiration_and_player_limit_bound_memory(self):
        rotation = PhotoRotation(ttl_seconds=60, max_players=1)
        rotation.record("a", "Paris", "one")
        rotation.record("b", "Paris", "two")
        self.assertEqual((), rotation.seen("a"))
        with patch("photo_rotation.time.monotonic", return_value=time.monotonic() + 61):
            self.assertEqual((), rotation.seen("b"))

    def test_cached_selector_uses_unseen_then_recycles_without_relaxing_history(self):
        questions = []
        for index in range(8):
            q = dynamic_question(name="Paris") | {"image_id": str(index)}
            questions.append(q)
        pool = {"Paris": {q["image_id"]: {"question": q, "expires_at": time.time()+60} for q in questions}}
        with patch.object(provider, "_prepared_questions", pool):
            seen = []
            for _ in range(8):
                q = provider.get_cached_question(seen_images=seen)
                self.assertNotIn(q["image_id"], seen)
                seen.append(q["image_id"])
            self.assertIsNotNone(provider.get_cached_question(seen_images=seen))
            self.assertIsNone(provider.get_cached_question(excluded_images=seen, seen_images=seen))
            self.assertIsNone(provider.get_cached_question(excluded_cities=["Paris"]))

    def test_prefetch_peek_does_not_record_a_display(self):
        rotation = PhotoRotation()
        future = Future()
        q = dynamic_question(name="Paris")
        future.set_result(q)
        with game.app.test_request_context(), patch.object(game, "question_rotation", rotation):
            game.session.update(game_mode="endless", player_id="rotation-peek")
            game.question_prefetch.tasks["rotation-peek"] = (future, time.monotonic())
            self.addCleanup(game.question_prefetch.tasks.pop, "rotation-peek", None)
            self.assertEqual(q, game.get_prefetched_question())
            self.assertEqual((), rotation.seen("rotation-peek"))
            game.remember_question(q)
            self.assertEqual((q["image_id"],), rotation.seen("rotation-peek"))

    def test_restarting_game_preserves_player_cycle(self):
        rotation = PhotoRotation()
        with game.app.test_request_context(), patch.object(game, "question_rotation", rotation):
            game.session.update(game_mode="challenge", player_id="cycle-player")
            game.remember_question(dynamic_question(name="Paris"))
            seen = rotation.seen("cycle-player")
            game.start_new_game()
            self.assertEqual("cycle-player", game.session["player_id"])
            self.assertEqual(seen, rotation.seen("cycle-player"))

    def test_late_seen_prefetch_is_rejected_when_an_unseen_alternative_exists(self):
        rotation = PhotoRotation()
        old = dynamic_question(name="Paris") | {"image_id": "seen"}
        new = dynamic_question(name="Paris") | {"image_id": "unseen"}
        pool = {"Paris": {"unseen": {"question": new, "expires_at": time.time()+60}}}
        with game.app.test_request_context(), patch.object(game, "question_rotation", rotation), \
             patch.object(provider, "_prepared_questions", pool):
            game.session.update(game_mode="endless", player_id="late-cycle")
            rotation.record("late-cycle", "Paris", "seen")
            self.assertTrue(game.question_conflicts_with_history(old))
            self.assertFalse(game.question_conflicts_with_history(new))

    def test_statistics_track_actual_displays_in_a_bounded_window(self):
        with patch.object(photo_statistics, "_cities", {}):
            for _ in range(1005):
                photo_statistics.record("Paris", "display", image_id="same")
            row = photo_statistics.snapshot([])["cities"]["Paris"]
        self.assertEqual(1005, row["events"]["display"])
        self.assertEqual(1000, row["display_window"])
        self.assertEqual(1, row["unique_images"])
        self.assertEqual([["same", 1000]], [list(pair) for pair in row["top_images"]])

    def test_named_landmark_is_recognized_across_different_filenames(self):
        city = next(c for c in provider.CITIES if c[0] == "Amsterdam")
        first = provider._scene_key(city, "File:Stopera city hall west.jpg", ["Category:Stopera"])
        second = provider._scene_key(city, "File:Amsterdam Stopera opera house east.jpg", ["Category:Stopera"])
        self.assertEqual(first, second)
