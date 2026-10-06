import unittest
from unittest.mock import patch

import city_provider as provider
from photo_sources import collect_city_photos, discovery_queries
from test_photo_preparation import photo_page


class PhotoSourceTests(unittest.TestCase):
    def setUp(self):
        self.city = next(city for city in provider.CITIES if city[0] == "Paris")
        pause = patch("photo_sources.time.sleep")
        pause.start()
        self.addCleanup(pause.stop)

    def collect(self, api):
        return collect_city_photos(self.city, api, provider._select_photo_candidates, lambda: 0)

    def test_all_cities_receive_distinct_bounded_areas_and_scoped_categories(self):
        for city in provider.CITIES:
            queries = list(discovery_queries(city))
            self.assertEqual(3, len({params["ggscoord"] for source, params in queries if source == "area"}))
            self.assertIn(f'Streets in {city[0]}', queries[3][1]["gsrsearch"])
            self.assertEqual(6, queries[3][1]["gsrnamespace"])

    def test_rich_center_still_samples_other_areas(self):
        calls = []
        def api(params):
            calls.append(params)
            return {"query": {"pages": [photo_page(f"File:Paris skyline {chr(0x4e00+i)}.jpg")
                                          for i in range(40)]}}
        photos, stats = self.collect(api)
        self.assertEqual(5, len(calls))
        self.assertEqual(40, len(photos))
        self.assertEqual(120, stats["sources"]["area"])
        self.assertIn("deepcat", stats["sources"])

    def test_rich_geographic_pool_still_gets_category_variety_with_four_calls(self):
        calls = []
        def api(params):
            calls.append(params)
            count = 100 if params["generator"] == "geosearch" else 1
            return {"query": {"pages": [photo_page(f"File:Paris skyline {chr(0x4e00+i)}.jpg")
                                          for i in range(count)]}}
        photos, _ = self.collect(api)
        self.assertEqual(100, len(photos))
        self.assertEqual(4, len(calls))

    def test_outer_areas_are_farther_apart_and_thematic_categories_rotate(self):
        with patch("photo_sources.random.uniform", side_effect=[0, .06, .06]), \
             patch("photo_sources.random.choice", side_effect=lambda values: values[0]):
            queries = list(discovery_queries(self.city))
        center, first, second = [params for source, params in queries if source == "area"]
        first_lon = float(first["ggscoord"].split("|")[1])
        second_lon = float(second["ggscoord"].split("|")[1])
        self.assertGreater(abs(first_lon - second_lon), .1)
        self.assertEqual(5000, center["ggsradius"])
        self.assertEqual(3500, first["ggsradius"])
        self.assertIn('Parks in Paris', queries[3][1]["gsrsearch"])

    def test_sparse_geographic_results_are_supplemented_by_categories(self):
        def api(params):
            return {"query": {"pages": [] if params["generator"] == "geosearch" else [photo_page()]}}
        photos, stats = self.collect(api)
        self.assertEqual(1, len(photos))
        self.assertEqual(1, stats["sources"]["deepcat"])

    def test_unsupported_deep_categories_fall_back_to_direct_categories(self):
        queries = []
        def api(params):
            queries.append(params)
            if "deepcat:" in params.get("gsrsearch", ""):
                raise provider.CommonsApiError("unsupported", "Deep category unavailable")
            return {"query": {"pages": [photo_page()] if params["generator"] == "search" else []}}
        photos, _ = self.collect(api)
        self.assertEqual(1, len(photos))
        self.assertIn("incategory:", queries[-1]["gsrsearch"])

    def test_same_file_merges_categories_before_quality_filtering(self):
        sparse = photo_page()
        sparse["categories"] = []
        count = 0
        def api(params):
            nonlocal count
            count += 1
            return {"query": {"pages": [sparse if count == 1 else photo_page()]}}
        photos, stats = self.collect(api)
        self.assertEqual(1, stats["raw_files"])
        self.assertEqual(1, len(photos))

    def test_category_results_with_no_city_evidence_are_rejected(self):
        foreign = photo_page("File:Tokyo street.jpg")
        foreign["categories"] = [{"title": "Streets in Tokyo"}]
        def api(params):
            return {"query": {"pages": [foreign] if params["generator"] == "search" else []}}
        self.assertEqual([], self.collect(api)[0])

    def test_outer_areas_require_city_evidence_to_avoid_neighbor_city_photos(self):
        foreign = photo_page("File:Tokyo street.jpg")
        foreign["categories"] = [{"title": "Streets in Tokyo"}]
        def api(params):
            return {"query": {"pages": [foreign] if params.get("ggsradius") == 3500 else []}}
        photos, stats = self.collect(api)
        self.assertEqual([], photos)
        self.assertEqual(1, stats["location_rejections"])

    def test_partial_network_failure_retains_valid_discoveries(self):
        count = 0
        def api(params):
            nonlocal count
            count += 1
            if count > 1:
                raise OSError("network")
            return {"query": {"pages": [photo_page()]}}
        photos, stats = self.collect(api)
        self.assertEqual(1, len(photos))
        self.assertEqual("network", stats["partial_failure"])

    def test_global_backoff_prevents_later_discovery_requests(self):
        paused = False
        def api(params):
            nonlocal paused
            paused = True
            return {"query": {"pages": [photo_page()]}}
        with patch("photo_sources.time.sleep") as pause:
            photos, _ = collect_city_photos(self.city, api, provider._select_photo_candidates,
                                            lambda: 30 if paused else 0)
        self.assertEqual(1, len(photos))
        pause.assert_not_called()
