from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch

import app as game
from app import start_question_prefetch as real_prefetch
import game_features as features
import daily_progress
from question_fixtures import dynamic_question
from itsdangerous import URLSafeSerializer


class FeatureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        game.app.config.update(TESTING=True, DAILY_DIRECTORY=directory.name)
        self.addCleanup(game.app.config.pop, "DAILY_DIRECTORY", None)
        self.client = game.app.test_client()
        for name in ("get_cached_question", "get_random_question", "start_question_prefetch"):
            mock = (patch.object(game, name, side_effect=dynamic_question) if name == "get_cached_question"
                    else patch.object(game, name, return_value=None))
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

    def dynamic(self, name="Kyoto"):
        return {"answer":name, "aliases":game.city_aliases[name], "image_id":f"commons:{name}",
                "is_dynamic":True, "image_url":f"https://example.com/{name}.jpg",
                "source_url":f"https://example.com/{name}", "credit":["Photographer", "CC BY 4.0"]}

    def test_daily_uses_ordinary_dynamic_fetch_and_keeps_each_players_question(self):
        with patch.object(game, "get_cached_question", side_effect=[self.dynamic(), self.dynamic("Athens")]) as fetch:
            self.start("daily")
            first = self.question()
            other = game.app.test_client()
            self.start("daily", "choice", client=other)
            self.assertEqual("Kyoto", first["answer"])
            self.assertEqual("Athens", self.question(other)["answer"])
            self.assertEqual(2, fetch.call_count)
        with patch.object(game, "get_cached_question", side_effect=AssertionError("Must keep current question")):
            self.client.get("/play")
            self.start("daily", "choice")
        self.assertEqual(first, self.question())
        self.assertEqual([], list(Path(game.app.config["DAILY_DIRECTORY"]).iterdir()))

    def test_daily_next_uses_prefetched_dynamic_question_and_recent_history(self):
        self.start("daily")
        first = self.question()
        self.client.post("/check", data={"question_id":first["question_id"], "guess":first["answer"]})
        with patch.object(game, "get_prefetched_question", return_value=self.dynamic()) as prefetched:
            self.client.get("/next")
            prefetched.assert_called_once_with(consume=True)
        self.assertEqual("Kyoto", self.question()["answer"])
        q = self.question()
        self.client.post("/check", data={"question_id":q["question_id"], "guess":"wrong"})
        with patch.object(game, "get_cached_question", return_value=self.dynamic("Athens")) as cached:
            self.client.get("/next")
            self.assertIn(first["image_id"], cached.call_args.args[0])
            self.assertIn("Kyoto", cached.call_args.args[1])

    def test_daily_prefetch_fetches_dynamic_photos_but_stops_at_tenth_question(self):
        future = Future()
        future.set_result(self.dynamic())
        with game.app.test_request_context(), patch.object(game.question_prefetch.executor, "submit", return_value=future) as submit:
            game.session.update(game_mode="daily", recent_images=["seen-image"], recent_cities=["Tokyo"])
            real_prefetch()
            player_id = game.session["player_id"]
            self.addCleanup(game.question_prefetch.tasks.pop, player_id, None)
            fetch, images, cities = submit.call_args.args
            self.assertIs(fetch.func, game.get_random_question)
            self.assertEqual({"seen_images": ()}, fetch.keywords)
            self.assertEqual((("seen-image",), ("Tokyo",)), (images, cities))
            self.assertEqual(self.dynamic(), game.get_prefetched_question(consume=True))
            game.session["game_stats"] = {"answered":9}
            game.session["current_question"] = self.dynamic()
            game.session["question_resolved"] = False
            real_prefetch()
            self.assertEqual(1, submit.call_count)

    def test_dynamic_daily_restores_full_question_choices_hints_and_history_without_files(self):
        with patch.object(game, "get_cached_question", return_value=self.dynamic()):
            self.start("daily", "choice")
        q = self.question()
        self.client.post("/check", data={"question_id":q["question_id"], "guess":q["answer"]})
        with self.client.session_transaction() as state:
            token = daily_progress.describe(state["daily_runs"][features.today()], game.app.secret_key)["token"]
        other = game.app.test_client()
        with patch.object(game, "get_cached_question", side_effect=AssertionError("Must restore saved photo")):
            self.start("daily", client=other, daily_token=token)
        self.assertEqual(q, self.question(other))
        with other.session_transaction() as state:
            self.assertIn(q["image_id"], state["recent_images"])
            self.assertIn(q["answer"], state["recent_cities"])
            self.assertEqual(100, state["game_stats"]["score"])
        # Text-mode hint bookkeeping must likewise survive signed restoration.
        text = game.app.test_client()
        with patch.object(game, "get_cached_question", return_value=self.dynamic("Athens")):
            self.start("daily", client=text)
        q = self.question(text)
        text.post("/check", data={"question_id":q["question_id"], "guess":q["answer"], "hint_level":2})
        with text.session_transaction() as state:
            token = daily_progress.describe(state["daily_runs"][features.today()], game.app.secret_key)["token"]
        self.start("daily", client=other, daily_token=token)
        with other.session_transaction() as state:
            self.assertEqual(2, state["hint_level"])
            self.assertEqual(1, state["game_stats"]["assisted"])

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
            token = daily_progress.describe(state["daily_runs"][features.today()], game.app.secret_key)["token"]
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
            old = daily_progress.describe(state["daily_runs"][features.today()], game.app.secret_key)["token"]
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
            token = daily_progress.describe(state["daily_runs"][features.today()], game.app.secret_key)["token"]
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

    def test_legacy_snapshot_restores_original_photo_then_uses_shared_selection(self):
        self.start("daily", "choice")
        q = self.question()
        with self.client.session_transaction() as state:
            token = daily_progress.describe(state["daily_runs"][features.today()], game.app.secret_key)["token"]
        payload = daily_progress.read(token, game.app.secret_key)
        payload.pop("current_question")
        payload["question_index"] = 0
        token = URLSafeSerializer(game.app.secret_key, salt="daily-progress-v1").dumps(payload)
        path = Path(game.app.config["DAILY_DIRECTORY"]) / f"{features.today()}.json"
        path.write_text(json.dumps([q]), encoding="utf-8")
        other = game.app.test_client()
        self.start("daily", client=other, daily_token=token)
        self.assertEqual(q, self.question(other))
        other.post("/check", data={"question_id":q["question_id"], "guess":q["answer"]})
        with patch.object(game, "get_cached_question", return_value=self.dynamic()):
            other.get("/next")
        self.assertEqual("Kyoto", self.question(other)["answer"])
        # Removing the old file no longer affects the migrated browser snapshot.
        path.unlink()
        self.start("daily", client=other)
        self.assertEqual("Kyoto", self.question(other)["answer"])

    def test_missing_legacy_file_does_not_reset_progress(self):
        self.start("daily")
        q = self.question()
        with self.client.session_transaction() as state:
            token = daily_progress.describe(state["daily_runs"][features.today()], game.app.secret_key)["token"]
        payload = daily_progress.read(token, game.app.secret_key)
        payload.pop("current_question")
        payload["question_index"] = 0
        token = URLSafeSerializer(game.app.secret_key, salt="daily-progress-v1").dumps(payload)
        other = game.app.test_client()
        response = self.start("daily", client=other, daily_token=token)
        self.assertEqual(409, response.status_code)
        self.assertEqual(q, self.question())

    def test_full_history_and_two_daily_snapshots_fit_the_session_cookie(self):
        with self.client.session_transaction() as state:
            state["recent_images"] = [game._photo_id(str(i)) for i in range(100)]
            state["recent_cities"] = [city[0] for city in game.CITIES[:20]]
        q = self.dynamic()
        q["image_url"] += "?filename=" + "long_filename_" * 30
        with patch.object(game, "get_cached_question", return_value=q), patch.object(game, "today", return_value="2026-10-05"):
            self.start("daily")
        with patch.object(game, "get_cached_question", return_value=self.dynamic("Athens")), patch.object(game, "today", return_value="2026-10-06"):
            response = self.start("daily")
        for header in response.headers.getlist("Set-Cookie"):
            self.assertLess(len(header), 4093)
        with self.client.session_transaction() as state:
            self.assertEqual(2, len(state["daily_runs"]))
            self.assertEqual("Athens", state["current_question"]["answer"])

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
