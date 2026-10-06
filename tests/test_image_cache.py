from concurrent.futures import Future
from email.message import Message
from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import app as game
from image_cache import ThumbnailCache
from question_fixtures import dynamic_question

URL = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/photo.jpg/1280px-photo.jpg"
JPEG = b"\xff\xd8\xff" + b"photo data"


class Response(BytesIO):
    def __init__(self, data=JPEG, mime="image/jpeg", url=URL):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = mime
        self.headers["Content-Length"] = str(len(data))
        self.url = url

    def geturl(self):
        return self.url


class ThumbnailCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = ThumbnailCache(directory.name)
        self.key = self.cache.key(URL)

    def test_download_is_atomic_and_cache_hit_does_not_fetch_again(self):
        with patch("image_cache.urlopen", return_value=Response()) as fetch:
            self.cache._download(self.key, URL)
            self.cache._download(self.key, URL)
        fetch.assert_called_once()
        self.assertEqual(JPEG, self.cache.find(self.key).read_bytes())
        self.assertFalse(list(self.cache.directory.glob("*.tmp")))
        self.assertIsNone(self.cache.find("../photo"))

    def test_invalid_large_and_redirected_responses_leave_no_cache_file(self):
        self.cache.max_file_bytes = 32
        responses = [Response(b"html", "text/html"), Response(b"html"),
                     Response(b"x" * 33), Response(url="https://example.test/photo")]
        for response in responses:
            with self.subTest(response=response), patch("image_cache.urlopen", return_value=response):
                with self.assertRaises(ValueError):
                    self.cache._download(self.key, URL)
                self.assertIsNone(self.cache.find(self.key))
                self.assertFalse(list(self.cache.directory.iterdir()))

    def test_expiration_and_capacity_evict_old_files(self):
        self.cache.max_bytes = len(JPEG)
        with patch("image_cache.urlopen", side_effect=lambda *a, **k: Response()):
            self.cache._download(self.key, URL)
            other = self.cache.key(URL + "?other")
            self.cache._download(other, URL)
        self.assertIsNone(self.cache.find(self.key))
        self.assertIsNotNone(self.cache.find(other))
        with patch("image_cache.time.time", return_value=time.time() + self.cache.ttl_seconds + 1):
            self.assertIsNone(self.cache.find(other))

    def test_enqueue_deduplicates_and_rejects_untrusted_sources(self):
        question = {"is_dynamic": True, "image_url": URL}
        with patch("image_cache.Thread"):
            self.cache.enqueue(question)
            self.cache.enqueue(question)
            self.cache.enqueue(question | {"image_url": "https://example.test/photo"})
            self.cache.enqueue(question | {"image_url": "https://thumb.wikimedia.org:invalid/photo"})
            self.cache.enqueue(question | {"is_dynamic": False})
        self.assertEqual(1, self.cache.queue.qsize())
        self.assertEqual({self.key}, self.cache.pending)

    def test_queue_is_bounded_and_failed_download_is_deferred(self):
        with patch("image_cache.Thread"):
            for index in range(100):
                self.cache.enqueue({"is_dynamic": True, "image_url": URL + f"?{index}"})
        self.assertEqual(64, self.cache.queue.qsize())
        self.assertEqual(64, len(self.cache.pending))
        self.cache.retry_at[self.key] = time.monotonic() + 60
        with patch("image_cache.Thread"):
            self.cache.enqueue({"is_dynamic": True, "image_url": URL})
        self.assertNotIn(self.key, self.cache.pending)


class ImageRouteTests(unittest.TestCase):
    def setUp(self):
        game.app.config.update(TESTING=True)
        self.client = game.app.test_client()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = ThumbnailCache(directory.name)
        replacement = patch.object(game, "thumbnail_cache", self.cache)
        replacement.start()
        self.addCleanup(replacement.stop)
        self.current = dynamic_question(name="Paris") | {"question_id": "current", "image_url": URL}
        with self.client.session_transaction() as state:
            state.update(game_mode="endless", player_id="image-tests", current_question=self.current,
                         recent_images=[self.current["image_id"]], recent_cities=["Paris"],
                         question_resolved=False)
        self.addCleanup(game.question_prefetch.tasks.pop, "image-tests", None)

    def test_cached_delivery_and_missing_file_fallback_keep_same_question(self):
        key = self.cache.key(URL)
        cached = self.cache.directory / (key + ".jpg")
        cached.write_bytes(JPEG)
        with patch.object(game, "start_question_prefetch"):
            response = self.client.get("/play")
        self.assertIn(f'src="/photos/{key}"'.encode(), response.data)
        self.assertIn(f'data-remote-src="{URL}"'.encode(), response.data)
        image = self.client.get(f"/photos/{key}")
        self.assertEqual(JPEG, image.data)
        self.assertEqual("image/jpeg", image.mimetype)
        self.assertIn("max-age=86400", image.headers["Cache-Control"])
        conditional = self.client.get(f"/photos/{key}", headers={"If-None-Match": image.headers["ETag"]})
        self.assertEqual(304, conditional.status_code)
        conditional.close()
        image.close()
        cached.unlink()
        self.assertEqual(404, self.client.get(f"/photos/{key}").status_code)
        with patch.object(game, "start_question_prefetch"):
            response = self.client.get("/play")
        self.assertIn(f'src="{URL}"'.encode(), response.data)
        with self.client.session_transaction() as state:
            self.assertEqual(self.current, state["current_question"])
            self.assertEqual([self.current["image_id"]], state["recent_images"])

    def test_prefetch_peeks_existing_task_without_consuming_or_disclosing_answer(self):
        following = dynamic_question(name="London") | {"image_url": URL + "?next"}
        future = Future()
        future.set_result(following)
        game.question_prefetch.tasks["image-tests"] = (future, time.monotonic())
        with patch.object(game, "get_cached_question") as draw, \
             patch.object(game.question_prefetch.executor, "submit") as submit:
            for _ in range(2):
                response = self.client.get("/prefetch-image?question_id=current")
                self.assertEqual({"image_url": following["image_url"]}, response.json)
                self.assertEqual("no-store", response.headers["Cache-Control"])
            draw.assert_not_called()
            submit.assert_not_called()
        self.assertIs(future, game.question_prefetch.tasks["image-tests"][0])
        with self.client.session_transaction() as state:
            self.assertEqual(self.current, state["current_question"])
            self.assertEqual(["Paris"], state["recent_cities"])
            self.assertEqual([self.current["image_id"]], state["recent_images"])

    def test_pending_stale_and_completed_game_prefetch_returns_no_image(self):
        future = Future()
        game.question_prefetch.tasks["image-tests"] = (future, time.monotonic())
        self.assertEqual({}, self.client.get("/prefetch-image?question_id=current").json)
        self.assertFalse(future.cancelled())
        self.assertEqual({}, self.client.get("/prefetch-image?question_id=stale").json)
        future.set_result(dynamic_question(name="Paris"))
        self.assertEqual({}, self.client.get("/prefetch-image?question_id=current").json)
        with self.client.session_transaction() as state:
            state.update(game_mode="challenge", game_stats={"answered": 10})
        self.assertEqual({}, self.client.get("/prefetch-image?question_id=current").json)

    def test_background_image_wrapper_preserves_selector_arguments_and_result(self):
        following = dynamic_question(name="London")
        with patch.object(game, "get_random_question", return_value=following) as select, \
             patch.object(game, "prepare_question_image") as prepare:
            result = game.fetch_question_with_image(("photo",), ("Paris",), seen_images=("seen",))
        select.assert_called_once_with(("photo",), ("Paris",), seen_images=("seen",))
        prepare.assert_called_once_with(following)
        self.assertIs(following, result)


if __name__ == "__main__":
    unittest.main()
