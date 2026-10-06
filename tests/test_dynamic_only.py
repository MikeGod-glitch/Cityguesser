from concurrent.futures import Future
import time
import unittest
from unittest.mock import patch

import app as game
from question_fixtures import dynamic_question


class DynamicOnlyTests(unittest.TestCase):
    def setUp(self):
        game.app.config.update(TESTING=True)
        self.client = game.app.test_client()
        self.cached = patch.object(game, "get_cached_question", return_value=None)
        self.cached_mock = self.cached.start()
        self.addCleanup(self.cached.stop)
        self.prefetch = patch.object(game, "start_question_prefetch")
        self.prefetch.start()
        self.addCleanup(self.prefetch.stop)
        self.ready = patch.object(game, "get_prefetched_question", return_value=None)
        self.ready.start()
        self.addCleanup(self.ready.stop)

    def test_empty_supply_shows_bounded_retry_without_a_fixed_question(self):
        before = time.monotonic()
        response = self.client.get("/play")
        self.assertLess(time.monotonic() - before, 1)
        self.assertEqual(503, response.status_code)
        self.assertEqual("5", response.headers["Retry-After"])
        self.assertIn(b'content="5;url=/play"', response.data)
        self.assertIn(b"Try again", response.data)
        self.assertIn(b'name="theme-color"', response.data)
        self.assertNotIn(b'class="city-photo"', response.data)
        with self.client.session_transaction() as state:
            self.assertNotIn("current_question", state)
            self.assertNotIn("recent_images", state)
            self.assertNotIn("remaining", state)
            self.assertEqual(0, state["game_stats"]["answered"])
        self.assertFalse(hasattr(game, "get_local_question"))

    def test_retry_uses_dynamic_photo_once_it_becomes_available(self):
        self.client.get("/play")
        prepared = dynamic_question(name="London")
        self.cached_mock.return_value = prepared
        response = self.client.get("/play")
        self.assertEqual(200, response.status_code)
        with self.client.session_transaction() as state:
            self.assertTrue(state["current_question"]["is_dynamic"])
            self.assertEqual([prepared["image_id"]], state["recent_images"])
            question_id = state["current_question"]["question_id"]
        self.client.get("/play")
        with self.client.session_transaction() as state:
            self.assertEqual(question_id, state["current_question"]["question_id"])
            self.assertEqual(1, len(state["recent_images"]))

    def test_waiting_between_questions_preserves_outcome_score_and_history(self):
        prepared = dynamic_question(name="Paris") | {"question_id": "previous"}
        with self.client.session_transaction() as state:
            state.update(game_mode="challenge", current_question=prepared,
                         question_resolved=True, question_result="Correct!",
                         game_stats={"answered": 1, "score": 100, "correct": 1},
                         recent_images=[prepared["image_id"]], recent_cities=["Paris"])
        for _ in range(3):
            response = self.client.get("/next")
            self.assertEqual(503, response.status_code)
            self.assertIn(b'content="5;url=/next"', response.data)
        with self.client.session_transaction() as state:
            self.assertEqual(prepared, state["current_question"])
            self.assertTrue(state["question_resolved"])
            self.assertEqual("Correct!", state["question_result"])
            self.assertEqual(100, state["game_stats"]["score"])
            self.assertEqual(1, state["game_stats"]["answered"])
            self.assertEqual([prepared["image_id"]], state["recent_images"])

    def test_submission_without_a_question_waits_without_counting_an_answer(self):
        for route in ("/check", "/reveal"):
            response = self.client.post(route, data={"guess": "Paris"})
            self.assertEqual(503, response.status_code)
            with self.client.session_transaction() as state:
                self.assertEqual(0, state["game_stats"]["answered"])

    def test_daily_waiting_preserves_reserved_run_and_answer_mode(self):
        self.client.post("/reset", data={"mode": "daily", "answer_mode": "choice"})
        self.client.get("/play")
        with self.client.session_transaction() as state:
            run_id = state["daily_run_id"]
        self.client.get("/play")
        with self.client.session_transaction() as state:
            self.assertEqual(run_id, state["daily_run_id"])
            self.assertEqual("choice", state["answer_mode"])
            self.assertEqual(0, state["game_stats"]["answered"])

    def test_tenth_question_can_be_fetched_after_waiting(self):
        self.prefetch.stop()
        self.ready.stop()
        pending = Future()
        with self.client.session_transaction() as state:
            state.update(game_mode="challenge", player_id="tenth-wait-test",
                         current_question=dynamic_question(name="Paris") | {"question_id": "previous"},
                         question_resolved=True, game_stats={"answered": 9}, recent_cities=["Paris"])
        self.addCleanup(game.question_prefetch.tasks.pop, "tenth-wait-test", None)
        with patch.object(game.question_prefetch.executor, "submit", return_value=pending) as submit:
            self.assertEqual(503, self.client.get("/next").status_code)
            self.assertFalse(pending.cancelled())
            pending.set_result(dynamic_question(name="London"))
            response = self.client.get("/next")
            self.assertEqual(200, response.status_code)
            self.assertIn(b"Question 10 / 10", response.data)
            submit.assert_called_once()
