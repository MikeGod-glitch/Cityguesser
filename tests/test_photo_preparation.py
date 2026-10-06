import io
import json
from concurrent.futures import Future
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import app as game
import city_provider as provider


def photo_page(title="File:Paris river skyline.jpg", mime="image/jpeg"):
    return {"title":title, "categories":[{"title":"City streets in Paris"}],
            "imageinfo":[{"url":"https://example.test/original", "descriptionurl":"https://example.test/source",
                          "width":1600, "height":900, "mime":mime}]}


def question(city="Paris", image="ready-photo"):
    return {"answer":city, "aliases":[], "image_id":image, "image_url":"https://example.test/image",
            "source_url":"https://example.test/source", "credit":["Author","CC0"], "is_dynamic":True}


class PhotoPreparationTests(unittest.TestCase):
    def setUp(self):
        pause = patch("photo_sources.time.sleep")
        pause.start()
        self.addCleanup(pause.stop)
        self.city = next(c for c in provider.CITIES if c[0] == "Paris")
        for cache in (provider._photo_cache, provider._credit_cache, provider._prepared_questions,
                      provider._photo_retry_after, provider._thumbnail_retry_after, provider._api_retry_after):
            cache.clear()
        self.addCleanup(self.clear_caches)

    def clear_caches(self):
        for cache in (provider._photo_cache, provider._credit_cache, provider._prepared_questions,
                      provider._photo_retry_after, provider._thumbnail_retry_after, provider._api_retry_after):
            cache.clear()

    def test_api_error_json_is_raised_even_if_http_succeeded(self):
        data = {"error":{"code":"urlparamnormal", "info":"bad thumbnail"}}
        with patch.object(provider,"urlopen",return_value=io.StringIO(json.dumps(data))):
            with self.assertRaises(provider.CommonsApiError):
                provider._api_get({"action":"query"})

    def test_metadata_filters_tiff_before_requesting_any_thumbnail(self):
        data = {"query":{"pages":[photo_page(), photo_page("File:Paris satellite.tiff","image/tiff")]}}
        with patch.object(provider,"_api_get",return_value=data) as api:
            photos = provider._fetch_photos(self.city)
        self.assertEqual([photo_page()["title"]],[p["title"] for p in photos])
        self.assertNotIn("iiurlwidth",api.call_args.args[0])
        self.assertIsNone(photos[0]["image_url"])

    def test_api_error_does_not_create_six_hour_empty_cache(self):
        with patch.object(provider,"_api_get",return_value={"error":{"code":"urlparamnormal","info":"bad"}}):
            with self.assertRaises(provider.CommonsApiError):
                provider._fetch_photos(self.city)
        self.assertNotIn("Paris",provider._photo_cache)

    def test_valid_empty_pool_has_shorter_ttl(self):
        with patch.object(provider,"_api_get",return_value={"batchcomplete":True}):
            self.assertEqual([],provider._fetch_photos(self.city))
        self.assertLess(provider._photo_cache["Paris"]["expires_at"]-time.time(),provider.CACHE_TTL_SECONDS)

    def test_refresh_failure_keeps_known_good_pool_and_defers_retry(self):
        pool = [{"title":"File:known.jpg", "image_url":"https://example.test/image"}]
        cached = {"photos":pool, "expires_at":time.time()-1, "stale_until":time.time()+100}
        provider._photo_cache["Paris"] = cached
        with patch.object(provider,"_api_get",side_effect=OSError("network")) as api:
            self.assertEqual(pool,provider._fetch_photos(self.city))
            self.assertEqual(pool,provider._fetch_photos(self.city))
        self.assertIs(cached,provider._photo_cache["Paris"])
        self.assertEqual(5,api.call_count)

    def test_retry_after_suppresses_subsequent_http_requests(self):
        error = HTTPError("https://example.test",429,"limited",{"Retry-After":"28"},None)
        with patch.object(provider,"urlopen",side_effect=error) as network:
            with self.assertRaises(HTTPError):
                provider._api_get({"action":"query"})
            with self.assertRaises(provider.CommonsApiError):
                provider._api_get({"action":"query"})
        self.assertEqual(1,network.call_count)
        self.assertGreater(provider._api_retry_after[provider.COMMONS_API],time.time()+25)

    def test_one_file_prepares_thumbnail_and_credit_then_reuses_them(self):
        photo = {"title":photo_page()["title"], "source_url":"https://example.test/source", "image_url":None}
        data = {"query":{"pages":[{"imageinfo":[{"thumburl":"https://example.test/thumb",
               "extmetadata":{"Artist":{"value":"<b>Author</b>"},"LicenseShortName":{"value":"CC0"}}}]}]}}
        with patch.object(provider,"_api_get",return_value=data) as api:
            ready = provider._add_credit(photo.copy())
            again = provider._add_credit(photo.copy())
        self.assertEqual(ready,again)
        self.assertEqual("Author",ready["author"])
        self.assertEqual("https://example.test/thumb",ready["image_url"])
        self.assertEqual(1,api.call_count)
        self.assertEqual(photo["title"],api.call_args.args[0]["titles"])

    def test_bad_file_does_not_discard_other_images_in_same_city(self):
        bad = {"title":"File:bad.jpg", "image_url":None, "source_url":"bad"}
        good = {"title":"File:good.jpg", "image_url":"good", "source_url":"good"}
        def prepare(photo):
            if photo["title"] == bad["title"]:
                raise provider.CommonsApiError("urlparamnormal","bad image")
            return photo
        with patch.object(provider.random,"sample",return_value=[self.city]), \
             patch.object(provider.random,"choice",side_effect=lambda values:values[0]), \
             patch.object(provider,"_fetch_photos",return_value=[bad,good]), \
             patch.object(provider,"_add_credit",side_effect=prepare) as credit:
            ready = provider.get_random_question()
            provider.get_random_question()
        self.assertEqual("Paris",ready["answer"])
        self.assertEqual("good",ready["image_url"])
        self.assertEqual(3,credit.call_count)  # The bad file is skipped on the second visit.

    def test_ready_cache_excludes_recent_images_and_cities_without_network(self):
        q = question()
        provider._prepared_questions["Paris"] = {q["image_id"]:{"question":q,"expires_at":time.time()+60}}
        with patch.object(provider,"_api_get",side_effect=AssertionError("must not request network")):
            self.assertEqual(q,provider.get_cached_question())
            self.assertIsNone(provider.get_cached_question(excluded_cities=["Paris"]))
            self.assertIsNone(provider.get_cached_question(excluded_images=[q["image_id"]]))

    def test_prepared_photo_remains_available_during_network_failure(self):
        q = question()
        provider._prepared_questions["Paris"] = {q["image_id"]:{"question":q,"expires_at":time.time()+60}}
        with patch.object(provider,"_fetch_photos",side_effect=OSError("offline")):
            self.assertEqual(q,provider.get_random_question())
            self.assertIsNone(provider.get_random_question(excluded_images=[q["image_id"]]))


class PrefetchHistoryTests(unittest.TestCase):
    def setUp(self):
        self.context = game.app.test_request_context("/play")
        self.context.push()
        self.addCleanup(self.context.pop)
        game.session.update(game_mode="endless",player_id="prefetch-test",recent_cities=["Paris"],recent_images=["used"])
        self.addCleanup(lambda:game.question_prefetch.tasks.pop("prefetch-test",None))

    def seed(self, q=None):
        future = Future()
        if q is not None:
            future.set_result(q)
        game.question_prefetch.tasks["prefetch-test"] = (future,time.monotonic())
        return future

    def test_conflicting_ready_result_is_discarded_even_when_only_peeking(self):
        self.seed(question())
        self.assertIsNone(game.get_prefetched_question())
        self.assertNotIn("prefetch-test",game.question_prefetch.tasks)
        self.seed(question("Tokyo","used"))
        self.assertIsNone(game.get_prefetched_question(consume=True))

    def test_pending_task_uses_prepared_alternative_without_waiting_or_local_fallback(self):
        pending = self.seed()
        cached = question("Tokyo","unused")
        with patch.object(game,"get_cached_question",return_value=cached), \
             patch.object(game,"start_question_prefetch"):
            result = game.get_next_question()
        self.assertEqual("Tokyo",result["answer"])
        self.assertIs(pending,game.question_prefetch.tasks["prefetch-test"][0])

    def test_late_result_is_checked_again_after_an_alternative_was_shown(self):
        pending = self.seed()
        cached = question("Tokyo","unused")
        with patch.object(game,"get_cached_question",return_value=cached), patch.object(game,"start_question_prefetch"):
            game.get_next_question()
        pending.set_result(question("Tokyo","different-photo"))
        self.assertIsNone(game.get_prefetched_question(consume=True))

    def test_no_prepared_photo_keeps_wait_bounded_and_pending_task_alive(self):
        pending = self.seed()
        with patch.object(game,"get_cached_question",return_value=None), patch.object(game,"start_question_prefetch"):
            before = time.monotonic()
            result = game.get_next_question()
        self.assertIsNone(result)
        self.assertLess(time.monotonic()-before,1)
        self.assertIs(pending,game.question_prefetch.tasks["prefetch-test"][0])

    def test_completed_conflicting_task_is_replaced_during_prefetch(self):
        old = self.seed(question())
        new = Future()
        with patch.object(game.question_prefetch.executor,"submit",return_value=new) as submit:
            game.start_question_prefetch()
        self.assertEqual(1,submit.call_count)
        self.assertIs(new,game.question_prefetch.tasks["prefetch-test"][0])
        self.assertIsNot(old,new)

    def test_image_history_survives_multiple_games_and_blocks_cached_recycling(self):
        ids = [provider._photo_id(f"File:history-{i}.jpg") for i in range(100)]
        game.session["recent_images"] = ids
        cached = question("Tokyo",ids[0])
        self.seed(cached)
        self.assertIsNone(game.get_prefetched_question(consume=True))
        game.start_new_game("challenge")
        self.assertEqual(ids,game.session["recent_images"])
        game.remember_question(question("London","new-image"))
        self.assertEqual(ids[1:]+["new-image"],game.session["recent_images"])

    def test_existing_local_history_is_migrated_without_resetting_progress(self):
        game.session["recent_images"] = ["local:Chicago","unchanged"]
        game.session["game_stats"] = {"score":100,"answered":1}
        game.ensure_game_mode()
        self.assertEqual([provider._photo_id("File:"+game.commons["Chicago"]),"unchanged"],game.session["recent_images"])
        self.assertEqual(100,game.session["game_stats"]["score"])


if __name__ == "__main__":
    unittest.main()
