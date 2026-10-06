import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from pathlib import Path
import random
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import app as game
import city_provider
from photo_feedback import list_feedback, record_feedback
from question_fixtures import dynamic_question


class PhotoFeedbackTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "photo-feedback.sqlite3"
        config = patch.dict(game.app.config, TESTING=True, SECRET_KEY="feedback-test-secret",
                            PHOTO_FEEDBACK_PATH=self.path)
        config.start()
        self.addCleanup(config.stop)
        self.client = game.app.test_client()
        self.question = dynamic_question(name="Hanoi") | {"question_id": "feedback-question"}
        self.seed(self.client, "player-one")

    def seed(self, client, player):
        with client.session_transaction() as state:
            state.update(current_question=self.question, player_id=player,
                         recent_images=[self.question["image_id"]], recent_cities=["Hanoi"],
                         game_mode="daily", daily_date="2026-10-07", hint_level=1,
                         game_stats={"score": 200, "streak": 2, "answered": 3})

    def submit(self, client=None, **extra):
        return (client or self.client).post("/photo-feedback", data={
            "question_id": self.question["question_id"], "reason": "object_closeup", **extra,
        })

    def test_feedback_does_not_change_session_randomness_stock_or_start_background_work(self):
        with self.client.session_transaction() as state:
            before = copy.deepcopy(dict(state))
        stock = copy.deepcopy(city_provider._prepared_questions)
        candidates = copy.deepcopy(city_provider._photo_cache)
        rng = random.getstate()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(game.app.config, TESTING=False))
            functions = [stack.enter_context(patch.object(game, name)) for name in (
                "get_random_question", "get_cached_question", "get_new_question",
                "start_question_prefetch", "get_prefetched_question", "prepare_question_image",
            )]
            pool = stack.enter_context(patch.object(game.dynamic_pool, "start"))
            rotation = stack.enter_context(patch.object(game.question_rotation, "record"))
            statistics = stack.enter_context(patch.object(game.photo_statistics, "record"))
            response = self.submit()
            for function in [*functions, pool, rotation, statistics]:
                function.assert_not_called()
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json["ok"])
        self.assertNotIn("Set-Cookie", response.headers)
        with self.client.session_transaction() as state:
            self.assertEqual(before, dict(state))
        self.assertEqual(rng, random.getstate())
        self.assertEqual(stock, city_provider._prepared_questions)
        self.assertEqual(candidates, city_provider._photo_cache)

    def test_repeated_feedback_merges_and_uses_server_photo_metadata(self):
        self.submit(image_id="fake", answer="fake", image_url="https://fake.invalid")
        before = list_feedback(self.path)
        response = self.submit()
        self.assertEqual(200, response.status_code)
        self.assertEqual(before, list_feedback(self.path))
        row = before[0]
        self.assertEqual(1, row["report_count"])
        self.assertEqual(self.question["image_id"], row["image_id"])
        self.assertEqual(self.question["image_url"], row["image_url"])
        self.assertEqual("Hanoi", row["city"])
        self.assertEqual({"Object close-up": 1}, row["reasons"])

    def test_multiple_players_merge_into_one_photo_record(self):
        self.submit()
        other = game.app.test_client()
        self.seed(other, "player-two")
        self.submit(other)
        self.assertEqual(1, len(list_feedback(self.path)))
        self.assertEqual(2, list_feedback(self.path)[0]["report_count"])
        with closing(sqlite3.connect(self.path)) as database:
            reporters = database.execute("SELECT reporter FROM reports").fetchall()
        self.assertTrue(all(len(reporter[0]) == 64 for reporter in reporters))
        self.assertNotIn("player-one", str(reporters))

    def test_missing_or_stale_question_is_rejected_without_creating_storage(self):
        self.assertEqual(400, self.submit(question_id="old-question").status_code)
        self.assertEqual(400, game.app.test_client().post("/photo-feedback").status_code)
        self.assertFalse(self.path.exists())

    def test_feedback_storage_failure_allows_retry_without_changing_game(self):
        with self.client.session_transaction() as state:
            before = dict(state)
        with patch.object(game, "record_feedback", side_effect=sqlite3.OperationalError("locked")), \
                patch.object(game.app.logger, "warning"):
            response = self.submit()
        self.assertEqual(503, response.status_code)
        self.assertFalse(response.json["ok"])
        with self.client.session_transaction() as state:
            self.assertEqual(before, dict(state))
        self.assertEqual(200, self.submit().status_code)

    def test_concurrent_duplicate_reports_are_counted_once(self):
        with ThreadPoolExecutor(max_workers=6) as workers:
            futures = [workers.submit(record_feedback, self.path, self.question, "one-reporter")
                       for _ in range(6)]
            for future in futures:
                future.result()
        self.assertEqual(1, list_feedback(self.path)[0]["report_count"])

    def test_feedback_control_is_outside_the_photo_frame(self):
        with patch.object(game, "start_question_prefetch"), patch.object(game, "get_prefetched_question", return_value=None):
            response = self.client.get("/play")
        html = response.get_data(as_text=True)
        toolbar = html.index('class="photo-feedback"')
        closing = html.index("</dialog>", toolbar)
        frame = html.index('class="photo-frame"')
        self.assertLess(closing, frame)
        self.assertIn("missing city clues", html[toolbar:closing])
        self.assertIn("Does this photo lack city clues?", html[toolbar:closing])
        for label in ("Indoor scene", "Person close-up", "Object close-up", "Other"):
            self.assertIn(label, html[toolbar:closing])

    def test_missing_or_invalid_reason_is_rejected_without_saving(self):
        for reason in ("", "invalid"):
            response = self.submit(reason=reason)
            self.assertEqual(400, response.status_code)
            self.assertFalse(response.json["ok"])
        self.assertFalse(self.path.exists())

    def test_all_reasons_are_saved_and_aggregated_without_duplicate_counts(self):
        for index, reason in enumerate(("indoor", "person_closeup", "object_closeup", "other")):
            client = game.app.test_client()
            self.seed(client, f"reason-player-{index}")
            self.submit(client, reason=reason)
            self.submit(client, reason=reason)
        row = list_feedback(self.path)[0]
        self.assertEqual(4, row["report_count"])
        self.assertEqual({"Indoor scene": 1, "Person close-up": 1, "Object close-up": 1, "Other": 1}, row["reasons"])

    def test_old_database_is_preserved_and_migrated_on_submission(self):
        with closing(sqlite3.connect(self.path)) as database, database:
            database.execute("CREATE TABLE photos (image_id TEXT PRIMARY KEY, city TEXT, image_url TEXT, source_url TEXT, first_reported_at REAL, last_reported_at REAL)")
            database.execute("CREATE TABLE reports (image_id TEXT, reporter TEXT, PRIMARY KEY(image_id, reporter))")
            database.execute("INSERT INTO photos VALUES (?, ?, ?, ?, 1, 1)",
                             (self.question["image_id"], "Hanoi", self.question["image_url"], self.question["source_url"]))
            database.execute("INSERT INTO reports VALUES (?, 'legacy-player')", (self.question["image_id"],))
        self.assertEqual({"Unspecified (legacy)": 1}, list_feedback(self.path)[0]["reasons"])
        self.assertEqual(200, self.submit(reason="indoor").status_code)
        row = list_feedback(self.path)[0]
        self.assertEqual(2, row["report_count"])
        self.assertEqual({"Unspecified (legacy)": 1, "Indoor scene": 1}, row["reasons"])

    def test_reading_empty_queue_does_not_create_a_database(self):
        self.assertEqual([], list_feedback(self.path))
        self.assertFalse(self.path.exists())
