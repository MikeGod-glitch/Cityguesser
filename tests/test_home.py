import unittest
from unittest.mock import patch

import app as game
from question_fixtures import dynamic_question


class HomePageTests(unittest.TestCase):
    def setUp(self):
        game.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = game.app.test_client()
        for name, options in (
            ("get_random_question", {"return_value": None}),
            ("get_cached_question", {"side_effect": dynamic_question}),
            ("start_question_prefetch", {}),
        ):
            mocked = patch.object(game, name, **options)
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)

    def test_landing_does_not_start_or_fetch_a_question(self):
        response = self.client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Look. Guess. Discover.", response.data)
        self.assertIn(b"100", response.data)
        self.assertNotIn(b"Continue your game", response.data)
        self.get_random_question.assert_not_called()
        self.start_question_prefetch.assert_not_called()
        with self.client.session_transaction() as session:
            self.assertNotIn("current_question", session)
            self.assertNotIn("game_stats", session)

    def test_returning_home_and_resuming_preserves_question_and_score(self):
        self.client.get("/play")
        with self.client.session_transaction() as session:
            question = dict(session["current_question"])
        self.client.post("/check", data={
            "question_id": question["question_id"], "guess": question["answer"],
        })
        with self.client.session_transaction() as session:
            stats = dict(session["game_stats"])
        response = self.client.get("/")
        self.assertIn(b"Continue your game", response.data)
        response = self.client.get("/play")
        self.assertIn(b"Correct", response.data)
        with self.client.session_transaction() as session:
            self.assertEqual(question, session["current_question"])
            self.assertEqual(stats, session["game_stats"])
            self.assertTrue(session["question_resolved"])

    def test_start_form_applies_both_modes_and_resets_progress(self):
        self.client.get("/play")
        with self.client.session_transaction() as session:
            session["game_stats"] = {"score": 200, "answered": 2}
        response = self.client.post("/reset", data={
            "mode": "endless", "answer_mode": "choice",
        }, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.request.path.endswith("/play"))
        self.assertIn(b"choice-grid", response.data)
        with self.client.session_transaction() as session:
            self.assertEqual("endless", session["game_mode"])
            self.assertEqual("choice", session["answer_mode"])
            self.assertEqual(0, session["game_stats"]["answered"])
            self.assertEqual(0, session["game_stats"]["score"])

    def test_completed_challenge_links_to_results(self):
        self.client.get("/play")
        with self.client.session_transaction() as session:
            session["game_stats"] = {"answered": 10, "score": 1000, "correct": 8}
            session["question_resolved"] = True
        response = self.client.get("/")
        self.assertIn(b'href="/results"', response.data)
        self.assertIn(b"View your results", response.data)
        self.assertNotIn(b"Continue your game", response.data)


if __name__ == "__main__":
    unittest.main()
