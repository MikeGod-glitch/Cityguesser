import random
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import app as game
from city_choices import CITY_PROFILES, generate_choices
from city_provider import CITIES


class ChoiceGenerationTests(unittest.TestCase):
    def test_profiles_and_local_flags_cover_the_catalog(self):
        self.assertEqual({city[0] for city in CITIES}, set(CITY_PROFILES))
        flag_dir = Path(game.app.static_folder) / "flags"
        for name, profile in CITY_PROFILES.items():
            with self.subTest(city=name):
                self.assertTrue(profile["styles"])
                ElementTree.parse(flag_dir / f"{profile['flag']}.svg")

    def test_every_city_has_four_unique_related_options(self):
        for answer in CITY_PROFILES:
            for _ in range(10):
                choices = generate_choices(answer)
                self.assertEqual(4, len(set(choices)))
                self.assertIn(answer, choices)
                target = CITY_PROFILES[answer]
                self.assertTrue(any(
                    target["continents"] & CITY_PROFILES[name]["continents"]
                    and target["styles"] & CITY_PROFILES[name]["styles"]
                    for name in set(choices) - {answer}
                ))
                for name in set(choices) - {answer}:
                    profile = CITY_PROFILES[name]
                    self.assertTrue(
                        target["continents"] & profile["continents"]
                        or target["styles"] & profile["styles"]
                    )

    def test_option_sets_and_correct_position_vary(self):
        # Use a local seeded generator so this check is reproducible.
        rng = random.Random(42)
        with patch("city_choices.random.choices", rng.choices), patch(
            "city_choices.random.shuffle", rng.shuffle
        ):
            rounds = [generate_choices("Paris") for _ in range(100)]
        self.assertGreater(len({tuple(sorted(row)) for row in rounds}), 50)
        self.assertEqual({0, 1, 2, 3}, {row.index("Paris") for row in rounds})

    def test_unrelated_candidates_fill_a_small_catalog(self):
        choices = generate_choices("Agra", ["Agra", "Paris", "Venice", "Quebec City"])
        self.assertEqual({"Agra", "Paris", "Venice", "Quebec City"}, set(choices))


class MultipleChoiceFlowTests(unittest.TestCase):
    def setUp(self):
        game.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = game.app.test_client()
        self.prefetch = patch.object(game, "start_question_prefetch")
        self.prefetch.start()
        self.addCleanup(self.prefetch.stop)
        self.dynamic = patch.object(game, "get_random_question", return_value=None)
        self.dynamic.start()
        self.addCleanup(self.dynamic.stop)
        self.ready = patch.object(game, "get_prefetched_question", return_value=None)
        self.ready.start()
        self.addCleanup(self.ready.stop)
        self.intro = patch.object(game, "get_city_intro", return_value={"text": "City", "source_url": None})
        self.intro.start()
        self.addCleanup(self.intro.stop)

    def question(self):
        with self.client.session_transaction() as session:
            return dict(session["current_question"])

    def start_choice(self, mode="challenge"):
        return self.client.post(
            "/reset", data={"mode": mode, "answer_mode": "choice"}, follow_redirects=True
        )

    def test_mode_switch_resets_score_and_refresh_keeps_choices(self):
        self.client.get("/")
        with self.client.session_transaction() as session:
            session["game_stats"] = {"answered": 3, "correct": 2, "score": 200}
        response = self.start_choice()
        self.assertEqual(200, response.status_code)
        self.assertNotIn(b'id="city-guess"', response.data)
        self.assertEqual(4, response.data.count(b'class="choice-flag"'))
        city = self.question()
        self.client.get("/")
        self.assertEqual(city["choices"], self.question()["choices"])
        with self.client.session_transaction() as session:
            self.assertEqual(0, session["game_stats"]["answered"])

    def test_invalid_submission_does_not_count(self):
        self.start_choice()
        city = self.question()
        response = self.client.post(
            "/check", data={"question_id": city["question_id"], "guess": "Invalid city"}
        )
        self.assertEqual(400, response.status_code)
        with self.client.session_transaction() as session:
            self.assertFalse(session["question_resolved"])
            self.assertEqual(0, session["game_stats"]["answered"])

    def test_wrong_answer_marks_both_choices_and_duplicate_does_not_score(self):
        self.start_choice()
        city = self.question()
        wrong = next(name for name in city["choices"] if name != city["answer"])
        data = {"question_id": city["question_id"], "guess": wrong}
        response = self.client.post("/check", data=data)
        self.assertIn(b"choice-correct", response.data)
        self.assertIn(b"choice-wrong", response.data)
        self.client.post("/check", data={**data, "guess": city["answer"]})
        with self.client.session_transaction() as session:
            self.assertEqual(1, session["game_stats"]["answered"])
            self.assertEqual(0, session["game_stats"]["correct"])
        self.client.get("/next")
        with self.client.session_transaction() as session:
            self.assertNotIn("selected_choice", session)

    def test_ten_question_challenge_results_and_play_again(self):
        self.start_choice()
        for number in range(10):
            city = self.question()
            response = self.client.post(
                "/check", data={"question_id": city["question_id"], "guess": city["answer"]}
            )
            self.assertIn(b"choice-correct", response.data)
            if number < 9:
                self.client.get("/next")
        response = self.client.get("/results")
        self.assertIn(b"10 / 10", response.data)
        self.assertIn(b"100%", response.data)
        self.assertNotIn(b"choice-grid", response.data)
        self.client.post("/reset", data={"mode": "challenge"}, follow_redirects=True)
        with self.client.session_transaction() as session:
            self.assertEqual("choice", session["answer_mode"])
            self.assertEqual(0, session["game_stats"]["answered"])
            self.assertEqual(4, len(session["current_question"]["choices"]))

    def test_reveal_and_endless_mode(self):
        self.start_choice("endless")
        city = self.question()
        response = self.client.post("/reveal", data={"question_id": city["question_id"]})
        self.assertIn(b"choice-correct", response.data)
        self.assertNotIn(b"choice-wrong", response.data)
        with self.client.session_transaction() as session:
            self.assertEqual("endless", session["game_mode"])
            self.assertEqual(1, session["game_stats"]["answered"])
            session["game_stats"] = {**session["game_stats"], "answered": 10}
        response = self.client.get("/next")
        self.assertIn(b"Question 11", response.data)
        self.assertNotIn(b"Challenge complete", response.data)
        self.client.post("/reset", data={"answer_mode": "text"}, follow_redirects=True)
        self.assertNotIn("choices", self.question())


if __name__ == "__main__":
    unittest.main()
