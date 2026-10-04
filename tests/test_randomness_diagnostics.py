"""Validate diagnostic counting, without any external requests."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import diagnose_randomness as diagnostic


class DiagnosticMetricTests(unittest.TestCase):
    def test_repeat_windows_cross_game_boundaries(self):
        # The last question of game one reappears at the start of game two.
        exposures = [{"image_id":str(i), "city":str(i)} for i in range(10)]
        exposures += [{"image_id":"9", "city":"9"}]
        result = diagnostic.metrics(exposures, [str(i) for i in range(10)])
        self.assertEqual(result["image_repeat_rate_10"], 1)
        self.assertEqual(result["city_repeat_rate_10"], 1)
        self.assertAlmostEqual(result["image_unique_rate"], 10/11)

    def test_unseen_cities_enter_distance(self):
        result = diagnostic.metrics([{"image_id":"x", "city":"A"}]*4, ["A","B"])
        self.assertEqual(result["city_distribution_tv"], .5)
        self.assertEqual(result["city_coverage"], .5)
        self.assertEqual(result["effective_image_count"], 1)

    def test_short_and_empty_samples_are_not_zero(self):
        self.assertIsNone(diagnostic.metrics([])["image_unique_rate"])
        self.assertIsNone(diagnostic.metrics([{"image_id":"x", "city":"A"}])["image_repeat_rate_10"])

    def test_local_and_dynamic_commons_file_share_identity(self):
        name = "Sample city.jpg"
        a = {"commons":name, "is_dynamic":False, "image_id":"local:city"}
        b = {"is_dynamic":True, "image_id":diagnostic.provider._photo_id("File:"+name)}
        self.assertEqual(diagnostic.question_identity(a), diagnostic.question_identity(b))

    def test_http429_stops_further_network_calls(self):
        recorder = diagnostic.Recorder()
        original = Mock(side_effect=HTTPError("https://example.invalid",429,"rate limited",{"Retry-After":"3"},None))
        with self.assertRaises(HTTPError):
            recorder.api(original, {"generator":"geosearch"})
        with self.assertRaisesRegex(RuntimeError,"stopped_after_http_429"):
            recorder.api(original, {"generator":"geosearch"})
        self.assertEqual(original.call_count,1)
        self.assertEqual(recorder.api_calls[0]["retry_after"],"3")

    def test_api_error_is_not_reported_as_true_empty_pool(self):
        recorder = diagnostic.Recorder()
        city = ("Diagnostic city", [], 0, 0)
        diagnostic.provider._photo_cache.pop(city[0], None)
        with patch.object(diagnostic.provider, "_api_get", return_value={"error":{"code":"urlparamnormal", "info":"bad thumbnail"}}):
            with recorder.patches():
                try:
                    diagnostic.provider._fetch_photos(city)
                except diagnostic.provider.CommonsApiError:
                    pass  # Updated provider correctly propagates API errors.
        try:
            self.assertEqual(recorder.attempts[0]["status"],"api_error_payload")
        finally:
            diagnostic.provider._photo_cache.pop(city[0], None)
            diagnostic.provider._photo_retry_after.pop(city[0], None)


if __name__ == "__main__":
    unittest.main()
