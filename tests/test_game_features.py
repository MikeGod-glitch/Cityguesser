from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app as game
import game_features as features


class FeatureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        game.app.config.update(TESTING=True, DAILY_DIRECTORY=directory.name)
        self.addCleanup(game.app.config.pop, "DAILY_DIRECTORY", None)
        self.client = game.app.test_client()
        for name in ("get_cached_question", "get_random_question", "start_question_prefetch"):
            mock = patch.object(game, name, return_value=None)
            mock.start()
            self.addCleanup(mock.stop)
        intro = patch.object(game, "get_city_intro", return_value={"text": "A city.", "source_url": None})
        intro.start()
        self.addCleanup(intro.stop)

    def start(self, mode="challenge", answer="text", client=None, **fields):
        client = client or self.client
        return client.post("/reset", data={"mode": mode, "answer_mode": answer, **fields}, follow_redirects=True)

    def question(self, client=None):
        with (client or self.client).session_transaction() as state:
            return dict(state["current_question"])

    def finish(self, *, correct=True):
        for index in range(10):
            question = self.question()
            response = self.client.post("/check", data={"question_id": question["question_id"],
                                                       "guess": question["answer"] if correct else "wrong"})
            self.assertEqual(200, response.status_code)
            if index != 9:
                self.client.get("/next")
        return self.client.get("/results")

    def test_preloaded_hints_are_counted_once_on_submission_without_changing_points(self):
        response = self.start()
        q = self.question()
        self.assertIn(b'id="hint-copy" aria-live="polite" hidden', response.data)
        self.assertIn(features.question_hints(q["answer"])[0].split(": ", 1)[-1].encode(), response.data)
        self.assertNotIn(b'action="/hint"', response.data)
        self.assertNotIn(b"Hints keep your points", response.data)
        self.client.post("/check", data={"question_id": q["question_id"], "guess": q["answer"], "hint_level": "2"})
        self.client.post("/check", data={"question_id": q["question_id"], "guess": q["answer"]})
        with self.client.session_transaction() as state:
            self.assertEqual(1, state["game_stats"]["assisted"])
            self.assertEqual(100, state["game_stats"]["score"])
        self.client.get("/next")
        with self.client.session_transaction() as state:
            self.assertNotIn("hint_level", state)

    def test_choice_stale_and_resolved_submissions_cannot_claim_hint_use(self):
        response = self.start(answer="choice")
        q = self.question()
        self.assertNotIn(b"hint-tongue", response.data)
        self.client.post("/check", data={"question_id": q["question_id"], "guess": q["answer"], "hint_level": "2"})
        with self.client.session_transaction() as state:
            self.assertEqual(0, state["game_stats"]["assisted"])
        self.start()
        self.client.post("/check", data={"question_id": "old-token", "guess": "wrong", "hint_level": "2"})
        q = self.question()
        self.client.post("/check", data={"question_id": q["question_id"], "guess": q["answer"]})
        self.client.post("/check", data={"question_id": q["question_id"], "guess": q["answer"], "hint_level": "2"})
        with self.client.session_transaction() as state:
            self.assertNotIn("hint_level", state)

    def test_special_city_hints_do_not_spell_out_the_answer(self):
        for name in ("Singapore", "Hong Kong", "Macau"):
            self.assertNotIn(name, features.question_hints(name)[1])
        self.assertEqual(100, len(game.CITY_PROFILES))
        for name in game.CITY_PROFILES:
            self.assertEqual(2, len(features.question_hints(name)))

    def test_daily_matches_across_players_modes_and_cache_changes(self):
        self.start("daily")
        first = self.question()
        other = game.app.test_client()
        self.start("daily", "choice", client=other)
        second = self.question(other)
        self.assertEqual(first["image_id"], second["image_id"])
        with patch.object(game, "build_daily_questions", side_effect=AssertionError("Must reuse manifest")):
            self.start("daily", "choice")
        self.assertEqual(second["choices"], self.question()["choices"])
        self.assertEqual(first["answer"], self.question()["answer"])

    def test_wrong_answers_with_hints_are_not_assisted_correct(self):
        self.start()
        q = self.question()
        self.client.post("/check", data={"question_id": q["question_id"], "guess": "wrong", "hint_level": "2"})
        with self.client.session_transaction() as state:
            self.assertEqual(0, state["game_stats"]["assisted"])

    def test_hint_submission_handles_malformed_and_out_of_range_levels(self):
        for value, expected in [("invalid", 0), ("-1", 0), ("999", 1)]:
            with self.subTest(value=value):
                self.start()
                q = self.question()
                self.client.post("/check", data={"question_id": q["question_id"], "guess": q["answer"], "hint_level": value})
                with self.client.session_transaction() as state:
                    self.assertEqual(expected, state["game_stats"]["assisted"])

    def test_daily_ten_unique_photos_and_no_skipping_unanswered_questions(self):
        self.start("daily")
        first = self.question()
        self.client.get("/next", follow_redirects=True)
        self.assertEqual(first, self.question())
        seen = []
        for index in range(10):
            q = self.question()
            seen.append(q)
            self.client.post("/reveal", data={"question_id": q["question_id"]})
            response = self.client.get("/next")
        self.assertEqual(10, len({q["image_id"] for q in seen}))
        self.assertEqual(10, len({q["answer"] for q in seen}))
        self.assertIn("/results", response.headers["Location"])
        with self.client.session_transaction() as state:
            self.assertEqual(0, state["completion"]["score"])
            self.assertEqual("daily", state["completion"]["mode"])

    def test_completed_daily_cannot_restart_or_change_answer_mode(self):
        self.start("daily")
        response = self.finish()
        self.assertIn(b"completion-data", response.data)
        with self.client.session_transaction() as state:
            first_id = state["completion"]["run_id"]
        self.client.get("/results")
        with self.client.session_transaction() as state:
            self.assertEqual(first_id, state["completion"]["run_id"])
        response = self.start("daily", "choice")
        self.assertTrue(response.request.path.endswith("/results"))
        self.assertNotIn(b"Practice again", response.data)
        self.assertNotIn(b"Replay daily", response.data)
        self.assertNotIn(b"answer-mode-form", response.data)
        with self.client.session_transaction() as state:
            self.assertEqual(first_id, state["completion"]["run_id"])
            self.assertEqual("text", state["answer_mode"])
            self.assertEqual(10, state["game_stats"]["answered"])

    def test_signed_browser_progress_restores_after_cookie_loss(self):
        self.start("daily", "choice")
        q = self.question()
        self.client.post("/check", data={"question_id": q["question_id"], "guess": q["answer"]})
        with self.client.session_transaction() as state:
            token = state["daily_runs"][features.today()]
        other = game.app.test_client()
        self.start("daily", "text", client=other, daily_token=token)
        with other.session_transaction() as state:
            self.assertEqual(1, state["game_stats"]["answered"])
            self.assertEqual(100, state["game_stats"]["score"])
            self.assertEqual("choice", state["answer_mode"])
            self.assertEqual(q["question_id"], state["current_question"]["question_id"])
            self.assertTrue(state["question_resolved"])

    def test_daily_resumes_after_an_ordinary_game_and_stale_tokens_cannot_rewind(self):
        self.start("daily")
        q = self.question()
        with self.client.session_transaction() as state:
            old = state["daily_runs"][features.today()]
        self.client.post("/check", data={"question_id": q["question_id"], "guess": q["answer"]})
        self.client.get("/next")
        next_q = self.question()
        self.start("challenge", "choice")
        self.start("daily", "choice", daily_token=old)
        with self.client.session_transaction() as state:
            self.assertEqual(1, state["game_stats"]["answered"])
            self.assertEqual("text", state["answer_mode"])
            self.assertEqual(next_q["question_id"], state["current_question"]["question_id"])

    def test_start_reserves_the_day_before_first_question_is_loaded(self):
        self.client.post("/reset", data={"mode":"daily", "answer_mode":"choice"})
        with self.client.session_transaction() as state:
            run_id = state["daily_run_id"]
        self.client.post("/reset", data={"mode":"daily", "answer_mode":"text"})
        with self.client.session_transaction() as state:
            self.assertEqual(run_id, state["daily_run_id"])
            self.assertEqual("choice", state["answer_mode"])

    def test_tampered_browser_progress_is_not_restored(self):
        self.start("daily")
        with self.client.session_transaction() as state:
            token = state["daily_runs"][features.today()]
        other = game.app.test_client()
        self.start("daily", client=other, daily_token=token + "tampered")
        with other.session_transaction() as state:
            self.assertEqual(0, state["game_stats"]["answered"])

    def test_home_has_only_ordinary_best_records(self):
        response = self.client.get("/")
        self.assertNotIn(b"Daily best", response.data)
        self.assertIn(b'data-record="challenge:text"', response.data)

    def test_midnight_preserves_started_daily_and_next_game_gets_new_date(self):
        with patch.object(game, "today", return_value="2026-10-04"):
            self.start("daily")
            old_question = self.question()
        with patch.object(game, "today", return_value="2026-10-05"):
            self.client.get("/play")
            with self.client.session_transaction() as state:
                self.assertEqual("2026-10-04", state["daily_date"])
            self.start("daily")
            with self.client.session_transaction() as state:
                self.assertEqual("2026-10-05", state["daily_date"])
            self.start("daily", daily_date="2026-10-04")
            self.assertEqual(old_question["question_id"], self.question()["question_id"])

    def test_day_boundary_uses_beijing_time(self):
        with patch.object(features, "datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 5, 0, 0, tzinfo=features.BEIJING)
            self.assertEqual("2026-10-05", features.today())
            clock.now.assert_called_once_with(features.BEIJING)

    def test_concurrent_daily_builders_publish_one_complete_manifest(self):
        def build(date):
            return [{"answer": str(i), "image_id": str(i), "builder": date} for i in range(10)]
        directory = game.app.config["DAILY_DIRECTORY"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: features.daily_questions(directory, "2026-10-04", build), range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(1, len(list(Path(directory).iterdir())))

    def test_endless_has_assistance_stats_but_never_creates_a_best_score_payload(self):
        self.start("endless")
        for _ in range(11):
            q = self.question()
            self.client.post("/check", data={"question_id": q["question_id"], "guess": q["answer"]})
            self.client.get("/next")
        with self.client.session_transaction() as state:
            self.assertNotIn("completion", state)
            self.assertEqual(11, state["game_stats"]["answered"])


if __name__ == "__main__":
    unittest.main()
