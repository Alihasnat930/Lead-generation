from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import campaigns
from core.markets import plan_queries, locations_for, city_coordinates
from core.open_data import build_query, parse_elements
from core.providers import SearchPage, ProviderError
from core.store import Store
from test_campaigns import settings, candidate, qualified_process


class FreeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "free.db")
        self.sleep = patch("core.campaigns.time.sleep")
        self.pacing = patch.object(self.store, "seconds_until_source", return_value=0)
        self.sleep.start()
        self.pacing.start()

    def tearDown(self):
        self.sleep.stop()
        self.pacing.stop()
        self.temp.cleanup()

    def make(self, **kwargs):
        return campaigns.create_campaign(settings(provider="free", **kwargs), self.store)

    def test_free_mode_rejects_paid_ai(self):
        with self.assertRaises(ValueError):
            self.make(use_ai=True)

    def test_free_plan_covers_three_sources_without_geocoding(self):
        queries = plan_queries(settings(provider="free"))
        self.assertEqual({q["source_kind"] for q in queries}, {"osm", "bing", "duckduckgo"})
        self.assertEqual(len(city_coordinates()), 150)
        self.assertEqual(sum(q["source_kind"] == "osm" for q in queries), 1)

    def test_osm_failure_continues_other_sources(self):
        job = self.make(target=1)
        def search(provider, query, page):
            self.assertEqual(provider, "free")
            if query["source_kind"] == "osm":
                raise ProviderError("overpass_busy", "Endpoint busy", True)
            return SearchPage([candidate()], False)
        campaigns.run_job(job, self.store, search=search, process=qualified_process)
        self.assertEqual(self.store.job(job)["status"], "complete")
        self.assertEqual(self.store.job(job)["search_calls"], 2)
        self.assertEqual(self.store.source_health(job)[0]["source"], "osm")

    def test_all_blocked_at_budget_does_not_wait_forever(self):
        job = self.make(max_search_calls=3)
        campaigns.run_job(job, self.store, search=Mock(side_effect=ProviderError("blocked", "Unavailable")))
        self.assertEqual(self.store.job(job)["status"], "search_limit")
        self.assertEqual(len(self.store.source_health(job)), 3)

    def test_successful_searches_cached_across_campaigns(self):
        search = Mock(return_value=SearchPage([], False))
        first = self.make()
        campaigns.run_job(first, self.store, search=search)
        calls = search.call_count
        self.assertGreater(calls, 0)
        second = self.make()
        campaigns.run_job(second, self.store, search=search)
        self.assertEqual(search.call_count, calls)
        self.assertEqual(self.store.job(second)["search_calls"], 0)
        self.assertEqual(self.store.job(second)["status"], "exhausted")

    def test_osm_data_has_license_and_no_invented_contacts(self):
        query = plan_queries(settings(provider="free"))[0]
        data = {"elements": [{"type": "node", "id": 123, "tags": {"name": "Fixture clinic", "amenity": "dentist", "website": "https://fixture-clinic.co.uk", "email": "office@fixture-clinic.co.uk"}}]}
        result = parse_elements(data, query)
        self.assertEqual(result.items[0]["source_license"], "ODbL-1.0")
        self.assertEqual(result.items[0]["source_url"], "https://www.openstreetmap.org/node/123")
        self.assertNotIn("email", result.items[0])
        self.assertEqual(result.items[0]["listed_email"], "office@fixture-clinic.co.uk")
        self.assertEqual(result.items[0]["industry"], "Dental Clinics")

    def test_partial_osm_response_is_not_cached_as_success(self):
        query = plan_queries(settings(provider="free"))[0]
        with self.assertRaises(ProviderError):
            parse_elements({"remark": "runtime error: timeout", "elements": []}, query)

    def test_daily_open_data_limit_is_persistent(self):
        for _ in range(100):
            self.assertTrue(self.store.reserve_open_data_allowance())
        self.assertFalse(self.store.reserve_open_data_allowance())
        self.assertFalse(Store(self.store.path).reserve_open_data_allowance())

    def test_osm_query_groups_tags_and_uses_bounded_radius(self):
        query = plan_queries(settings(provider="free"))[0]
        body = build_query(query)
        self.assertIn("around:12000", body)
        self.assertIn("timeout:45", body)
        self.assertEqual(body.count("nwr["), 2)


if __name__ == "__main__":
    unittest.main()
