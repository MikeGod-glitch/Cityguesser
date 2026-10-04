import json
import re
import tempfile
import unittest
from unittest.mock import patch

import app as game


def events(response):
    return json.loads(re.search(rb'<script id="sound-events-data" type="application/json">(.*?)</script>',
                                response.data).group(1))


class SoundEventTests(unittest.TestCase):
    def setUp(self):
        self.client = game.app.test_client()
        game.app.config['TESTING'] = True
        for name in ('get_cached_question', 'get_random_question', 'start_question_prefetch'):
            mocked = patch.object(game, name, return_value=None)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(game, 'get_city_intro', return_value={'text': 'A city.', 'source_url': None})
        mocked.start()
        self.addCleanup(mocked.stop)
        self.client.post('/reset', data={'mode': 'challenge'}, follow_redirects=True)

    def answer(self, correct=True):
        with self.client.session_transaction() as state:
            question = state['current_question'].copy()
        data = {'question_id': question['question_id'], 'guess': question['answer'] if correct else 'wrong'}
        return self.client.post('/check', data=data), data

    def test_correct_streak_and_completion_are_separate_events(self):
        for number in range(1, 11):
            response, data = self.answer()
            self.assertEqual(['correct'] + (['streak'] if number in {3, 5, 10} else []),
                             [event['type'] for event in events(response)])
            self.assertEqual([], events(self.client.post('/check', data=data)))
            if number < 10:
                self.client.get('/next')
        result = events(self.client.get('/results'))
        self.assertEqual(['levelComplete'], [event['type'] for event in result])
        self.assertEqual(result, events(self.client.get('/results')))
        with self.client.session_transaction() as state:
            self.assertEqual('complete:' + state['completion']['run_id'], result[0]['id'])

    def test_wrong_reveal_stale_and_endless(self):
        response, _ = self.answer(False)
        self.assertEqual(['wrong'], [event['type'] for event in events(response)])
        self.assertEqual([], events(self.client.get('/play')))
        self.client.get('/next')
        self.assertEqual([], events(self.client.post('/check', data={'question_id': 'stale', 'guess': 'wrong'})))
        with self.client.session_transaction() as state:
            question_id = state['current_question']['question_id']
        self.assertEqual([], events(self.client.post('/reveal', data={'question_id': question_id})))
        self.client.post('/reset', data={'mode': 'endless'}, follow_redirects=True)
        for _ in range(11):
            response, _ = self.answer()
            self.assertNotIn('levelComplete', [event['type'] for event in events(response)])
            self.client.get('/next')
        self.assertEqual(302, self.client.get('/results').status_code)

    def test_local_assets_and_controls_on_both_pages(self):
        for path in ('/', '/play'):
            response = self.client.get(path)
            self.assertIn(b'data-sound-toggle', response.data)
            self.assertIn(b'/static/sound-system.js', response.data)
            self.assertIn(b'/static/vendor/howler/howler.core.min.js', response.data)
        for name in ('button-click', 'correct', 'wrong', 'streak', 'level-complete'):
            response = self.client.get('/static/audio/' + name + '.wav')
            self.assertEqual(200, response.status_code)
            self.assertTrue(response.data.startswith(b'RIFF'))
            response.close()

    def test_daily_completion_and_choice_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(game.app.config, {'DAILY_DIRECTORY': directory}):
                self.client.post('/reset', data={'mode': 'daily', 'answer_mode': 'choice'}, follow_redirects=True)
                with self.client.session_transaction() as state:
                    question_id = state['current_question']['question_id']
                invalid = self.client.post('/check', data={'question_id': question_id, 'guess': 'invalid city'})
                self.assertEqual(400, invalid.status_code)
                self.assertEqual([], events(invalid))
                for number in range(10):
                    response, _ = self.answer()
                    self.assertEqual('correct', events(response)[0]['type'])
                    if number < 9:
                        self.client.get('/next')
                self.assertEqual('levelComplete', events(self.client.get('/results'))[0]['type'])


if __name__ == '__main__':
    unittest.main()
