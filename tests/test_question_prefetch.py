"""Task lifecycle contracts that must survive moving prefetch out of Flask."""

from concurrent.futures import Future
import unittest
from unittest.mock import Mock, patch

from question_prefetch import QuestionPrefetch


class QuestionPrefetchLifecycleTests(unittest.TestCase):
    def setUp(self):
        with patch("question_prefetch.ThreadPoolExecutor"):
            self.prefetch = QuestionPrefetch(ttl_seconds=60)
        self.fetch = Mock()
        self.conflicts = lambda question: False

    def test_expired_pending_task_is_cancelled_and_replaced(self):
        expired = Future()
        replacement = Future()
        self.prefetch.tasks["player"] = (expired, 10)
        self.prefetch.executor.submit.return_value = replacement
        with patch("question_prefetch.time.monotonic", return_value=71):
            self.prefetch.start("player", self.fetch, ("seen-photo",), ("Paris",), self.conflicts)
        self.assertTrue(expired.cancelled())
        self.prefetch.executor.submit.assert_called_once_with(self.fetch, ("seen-photo",), ("Paris",))
        self.assertIs(replacement, self.prefetch.tasks["player"][0])

    def test_cancelling_one_player_keeps_other_players_tasks(self):
        first, second = Future(), Future()
        self.prefetch.tasks.update(first=(first, 0), second=(second, 0))
        self.prefetch.cancel("first")
        self.assertTrue(first.cancelled())
        self.assertNotIn("first", self.prefetch.tasks)
        self.assertIs(second, self.prefetch.tasks["second"][0])
        self.assertFalse(second.cancelled())

    def test_consuming_old_result_does_not_remove_a_replacement_task(self):
        old = Future()
        old.set_result({"answer": "Paris"})
        replacement = Future()
        self.prefetch.tasks["player"] = (old, 0)

        def conflicts(question):
            # Another request may install a replacement while this result is checked.
            self.prefetch.tasks["player"] = (replacement, 1)
            return False

        self.assertEqual({"answer": "Paris"}, self.prefetch.get("player", conflicts, consume=True))
        self.assertIs(replacement, self.prefetch.tasks["player"][0])


if __name__ == "__main__":
    unittest.main()
