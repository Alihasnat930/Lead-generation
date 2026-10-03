import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock, MagicMock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import campaigns, providers, websites, sheets, ai
from core.exports import csv_bytes
from core.markets import locations_for, plan_queries, NICHES
from core.providers import SearchPage, ProviderError
from core.qualification import qualify
from core.store import Store


def settings(**changes):
    result = dict(name="Test campaign", niches=["Dental Clinics"], locations=locations_for(["UK"])[:1],
                  keywords=[], target=10, min_score=75, provider="serpapi", require_email=True,
                  use_ai=False, max_search_calls=100, max_candidates=2000, max_ai_calls=20,
                  workers=4, max_pages=3, website_pages=4, target_service="Appointment automation")
    return {**result, **changes}


def candidate(number=0, **changes):
    return dict(company_name=f"Fixture Dental {number}", website=f"https://fixture{number}.com/",
                domain=f"fixture{number}.com", industry="Dental Clinics", target_city="London",
                target_country="United Kingdom", country_code="gb", language="en", source="fixture",
                source_url="https://search.example/fixtures", **changes)


def enrichment(**changes):
    text = "Independent dental clinic and dentist in London, United Kingdom. Contact our clinic for an appointment."
    return dict(text=text, pages=[dict(url="https://fixture0.com/", text=text)], crawl_status="ok",
                email="hello@fixture0.com", email_source="https://fixture0.com/contact", phone="",
                countries=["GB"], **changes)


def qualified_process(item, config, *_):
    e = enrichment()
    e["email"] = "hello@" + item["domain"]
    e["email_source"] = item["website"] + "contact"
    return qualify(item, e, config)


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")
        self.sleep = patch("core.campaigns.time.sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.temp.cleanup()

    def make(self, **changes):
        return campaigns.create_campaign(settings(**changes), self.store)

    def test_1000_unique_qualified_leads_and_target_stop(self):
        job = self.make(target=1000, provider="free", max_search_calls=200, locations=locations_for(["US", "UK", "EU"]))
        counter = 0
        def search(*_):
            nonlocal counter
            batch = [candidate(i) for i in range(counter, counter + 10)]
            counter += 10
            return SearchPage(batch, True)
        with patch.object(self.store, "seconds_until_source", return_value=0):
            campaigns.run_job(job, self.store, search=search, process=qualified_process)
        self.assertEqual(self.store.job(job)["status"], "complete")
        self.assertEqual(self.store.stats(job)["qualified"], 1000)
        self.assertEqual(self.store.job(job)["search_calls"], 100)
        self.assertEqual(len({row["domain"] for row in self.store.leads(job)}), 1000)

    def test_search_budget_resume_retains_results(self):
        job = self.make(target=2, max_search_calls=1)
        counter = 0
        def search(*_):
            nonlocal counter
            item = candidate(counter)
            counter += 1
            return SearchPage([item], True)
        campaigns.run_job(job, self.store, search=search, process=qualified_process)
        self.assertEqual(self.store.job(job)["status"], "search_limit")
        self.assertEqual(self.store.stats(job)["qualified"], 1)
        self.store.revise_limits(job, 5, 2000, "serpapi")
        campaigns.run_job(job, self.store, search=search, process=qualified_process)
        self.assertEqual(self.store.stats(job)["qualified"], 2)
        self.assertEqual(self.store.job(job)["search_calls"], 2)

    def test_deduplication_across_pages_and_campaigns(self):
        first = self.make(target=1)
        search = Mock(return_value=SearchPage([candidate(), candidate()], False))
        campaigns.run_job(first, self.store, search=search, process=qualified_process)
        second = self.make(target=1)
        campaigns.run_job(second, self.store, search=search, process=qualified_process)
        self.assertEqual(self.store.stats()["qualified"], 1)
        self.assertEqual(self.store.job(second)["status"], "exhausted")
        self.assertGreater(self.store.job(second)["duplicates"], 0)

    def test_captcha_pauses_without_claiming_empty_success(self):
        job = self.make()
        campaigns.run_job(job, self.store, search=Mock(side_effect=ProviderError("captcha", "CAPTCHA required")))
        self.assertEqual(self.store.job(job)["status"], "source_blocked")
        self.assertEqual(self.store.next_query(job)["page"], 1)
        self.assertIn("CAPTCHA", self.store.job(job)["reason"])

    def test_retries_consume_budget(self):
        job = self.make(max_search_calls=2)
        search = Mock(side_effect=ProviderError("rate_limit", "Retry later", True))
        campaigns.run_job(job, self.store, search=search)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(self.store.job(job)["status"], "search_limit")

    def test_exhaustion_does_not_claim_target_reached(self):
        job = self.make(target=1000)
        campaigns.run_job(job, self.store, search=Mock(return_value=SearchPage([], False)))
        self.assertEqual(self.store.job(job)["status"], "exhausted")
        self.assertIn("0/1,000", self.store.job(job)["reason"])

    def test_active_worker_lock_and_stale_recovery(self):
        first, second = self.make(), self.make()
        token = self.store.reserve_worker(first)
        with self.assertRaises(ValueError):
            self.store.reserve_worker(second)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET heartbeat=0 WHERE id=?", (first,))
        new_token = self.store.reserve_worker(first)
        self.assertNotEqual(token, new_token)
        self.assertFalse(self.store.activate(first, token))
        self.assertTrue(self.store.activate(first, new_token))

    def test_pause_after_batch_and_resume(self):
        job = self.make(target=2, workers=1)
        def process(*args):
            self.store.request_pause(job)
            return qualified_process(*args)
        search = Mock(return_value=SearchPage([candidate(1), candidate(2)], False))
        campaigns.run_job(job, self.store, search=search, process=process)
        self.assertEqual(self.store.job(job)["status"], "paused")
        self.assertEqual(self.store.stats(job)["qualified"], 1)
        campaigns.run_job(job, self.store, search=search, process=qualified_process)
        self.assertEqual(self.store.stats(job)["qualified"], 2)
        self.assertEqual(search.call_count, 1)

    def test_candidate_limit_does_not_drop_remainder_of_search_page(self):
        job = self.make(target=2, max_candidates=2)
        def process(item, config, *_):
            value = qualified_process(item, config)
            value["status"] = value["qualification_status"] = "REVIEW"
            return value
        search = Mock(return_value=SearchPage([candidate(i) for i in range(10)], False))
        campaigns.run_job(job, self.store, search=search, process=process)
        self.assertEqual(self.store.stats(job)["processed"], 2)
        self.assertEqual(self.store.stats(job)["pending"], 8)
        self.assertEqual(self.store.job(job)["status"], "candidate_limit")

    def test_ai_failure_retains_candidate_for_resume(self):
        job = self.make(target=1)
        search = Mock(return_value=SearchPage([candidate()], False))
        campaigns.run_job(job, self.store, search=search, process=Mock(side_effect=campaigns.AIUnavailable("AI unavailable")))
        self.assertEqual(self.store.job(job)["status"], "ai_unavailable")
        self.assertEqual(self.store.stats(job)["pending"], 1)
        campaigns.run_job(job, self.store, search=search, process=qualified_process)
        self.assertEqual(self.store.job(job)["status"], "complete")
        self.assertEqual(search.call_count, 1)


class QualityTests(unittest.TestCase):
    def test_ranked_list_is_not_counted_as_a_business(self):
        item = candidate()
        item["company_name"] = "10 best dental clinics in London"
        self.assertEqual(qualify(item, enrichment(), settings())["qualification_status"], "REVIEW")

    def test_query_labels_cannot_qualify_unrelated_site(self):
        e = enrichment()
        e["pages"] = [{"url": "https://fixture0.com/", "text": "A great website about space and planets."}]
        lead = qualify(candidate(), e, settings())
        self.assertEqual(lead["qualification_status"], "REVIEW")
        self.assertFalse(lead["fit"])

    def test_published_email_required(self):
        e = enrichment()
        e["email"] = ""
        self.assertEqual(qualify(candidate(), e, settings())["qualification_status"], "REVIEW")

    def test_email_must_have_source(self):
        e = enrichment()
        e["email_source"] = ""
        self.assertEqual(qualify(candidate(), e, settings())["email"], "")

    def test_matching_country_and_city_evidence_pass(self):
        lead = qualify(candidate(), enrichment(), settings())
        self.assertEqual(lead["qualification_status"], "QUALIFIED")
        self.assertEqual(lead["email_status"], "published_unverified")

    def test_conflicting_country_fails(self):
        e = enrichment()
        e["countries"] = ["Canada"]
        self.assertEqual(qualify(candidate(), e, settings())["qualification_status"], "REVIEW")

    def test_us_state_zip_supports_country(self):
        item = candidate()
        item.update(target_city="Houston", target_country="United States", country_code="us")
        e = enrichment()
        text = "Dental clinic in Houston, TX 77001. We help patients with dental care."
        e.update(text=text, countries=[], pages=[dict(url=item["website"], text=text)])
        self.assertEqual(qualify(item, e, settings())["qualification_status"], "QUALIFIED")

    def test_local_language_city_alias(self):
        item = candidate()
        item.update(target_city="Munich", target_country="Germany", country_code="de", language="de")
        e = enrichment()
        text = "Zahnarzt in Munchen. Unsere Praxis bietet Zahnpflege in Deutschland."
        e.update(text=text, countries=["DE"], pages=[dict(url=item["website"], text=text)])
        self.assertEqual(qualify(item, e, settings())["qualification_status"], "QUALIFIED")

    def test_multi_country_coverage(self):
        locations = locations_for(["US", "UK", "EU"])
        self.assertEqual(len({x["country_code"] for x in locations if x["region"] == "EU"}), 27)
        self.assertGreaterEqual(len(locations), 100)
        query = plan_queries(settings(locations=locations, niches=list(NICHES)))
        self.assertGreater(len(query), 4000)
        self.assertEqual(query[0]["country_code"], "us")
        self.assertTrue(any(q["keyword"] == "Zahnarzt" for q in query))


class ExtractionTests(unittest.TestCase):
    def test_invalid_server_charset_falls_back_without_stopping_worker(self):
        session = MagicMock()
        response = Mock(status_code=200, headers={"Content-Type": "text/html"}, encoding="invalid-charset-name")
        response.iter_content.return_value = [b"<html>Business contact page</html>"]
        session.get.return_value.__enter__.return_value = response
        with patch("core.websites.validate_public_url", side_effect=lambda url: url):
            html, url = websites.fetch_html(session, "https://clinic.co.uk")
        self.assertIn("Business contact page", html)

    def test_unsuitable_department_inboxes_are_not_lead_contacts(self):
        for local in ("press", "privacy", "ap", "careers", "billing"):
            self.assertEqual(websites.valid_email(local + "@clinic.co.uk"), "")
        self.assertEqual(websites.valid_email("hello@clinic.co.uk"), "hello@clinic.co.uk")

    def test_crawler_follows_contact_link_and_excludes_vendor_email(self):
        def fetch(session, url, *_args, **_kwargs):
            if url.endswith("robots.txt"):
                return "User-agent: *\nAllow: /", url
            if url.endswith("contact"):
                return '<h1>Contact our London dental clinic</h1><p>Appointments and dental care in the United Kingdom.</p><a href="mailto:office@clinic.co.uk">Email our team</a>', url
            return '<h1>London dental clinic in the United Kingdom</h1><p>We provide a full range of dental services to patients.</p><a href="/contact">Contact us</a><a href="mailto:info@unrelated-vendor.com">Website by vendor</a>', url
        with patch("core.websites.fetch_html", side_effect=fetch), patch("core.websites.time.sleep"):
            result = websites.enrich_website("https://clinic.co.uk", max_pages=2)
        self.assertEqual(result["email"], "office@clinic.co.uk")
        self.assertEqual(result["email_source"], "https://clinic.co.uk/contact")
        self.assertEqual(len(result["pages"]), 2)

    def test_robots_disallow_prevents_website_fetch(self):
        with patch("core.websites.fetch_html", return_value=("User-agent: *\nDisallow: /", "https://clinic.co.uk/robots.txt")) as fetch:
            result = websites.enrich_website("https://clinic.co.uk")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["crawl_status"], "robots_disallowed")
        self.assertEqual(result["email"], "")

    def test_domain_deduplication_with_public_suffixes(self):
        self.assertEqual(websites.domain_from_url("https://www.clinic.co.uk/contact"), "clinic.co.uk")
        self.assertEqual(websites.domain_from_url("https://booking.clinic.co.uk/"), "clinic.co.uk")
        self.assertNotEqual(websites.domain_from_url("https://shop1.myshopify.com"), websites.domain_from_url("https://shop2.myshopify.com"))

    def test_captcha_widget_is_not_challenge_page(self):
        html = '<title>Our dental practice</title><script src="recaptcha.js"></script><h1>Contact us</h1>'
        self.assertFalse(websites.is_challenge(html))
        self.assertTrue(websites.is_challenge("<title>Just a moment...</title>"))

    def test_contacts_are_extracted_with_metadata_order_independent(self):
        html = '''<meta content="Dental practice" name="description"><p>Email office@clinic.co.uk</p>
        <script>var tracking="tracker@sentry.io";</script><a href="mailto:hello%40clinic.co.uk?subject=Hello">Email</a>
        <a href="tel:+442012345678">Call</a><form><textarea name="message"></textarea></form>
        <script type="application/ld+json">{"@type":"Dentist","address":{"addressLocality":"London","addressCountry":"GB"}}</script>'''
        page = websites.parse_page(html, "https://clinic.co.uk/")
        self.assertEqual(page["description"], "Dental practice")
        self.assertEqual(page["emails"], ["hello@clinic.co.uk", "office@clinic.co.uk"])
        self.assertEqual(page["countries"], ["GB"])
        self.assertTrue(page["contact_form"])

    def test_private_destinations_rejected(self):
        with patch("core.websites.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(websites.FetchError):
                websites.validate_public_url("https://local.test/")
        self.assertEqual(websites.normalize_url("file:///etc/passwd"), "")
        self.assertEqual(websites.normalize_url("https://user:pass@example.com"), "")

    def test_csv_neutralizes_spreadsheet_formula(self):
        result = csv_bytes([dict(company_name="=HYPERLINK(1)", phone="+44123456789")])
        row = next(csv.DictReader(io.StringIO(result.decode("utf-8-sig"))))
        self.assertTrue(row["company_name"].startswith("'="))
        self.assertEqual(row["phone"], "'+44123456789")

    def test_serpapi_pagination_and_secret_not_persisted(self):
        response = Mock(status_code=200)
        response.json.return_value = {"organic_results": [{"title": "Clinic", "link": "https://clinic.co.uk", "snippet": "Dental practice"}], "serpapi_pagination": {"next": "https://serpapi.com/next?api_key=SECRET"}}
        with patch.object(providers.config, "SERPAPI_API_KEY", "SECRET"), patch("core.providers.requests.get", return_value=response) as get:
            result = providers.search_page("serpapi", plan_queries(settings())[0], 3)
        self.assertEqual(get.call_args.kwargs["params"]["start"], 20)
        self.assertTrue(result.has_more)
        self.assertNotIn("SECRET", json.dumps(result.items))

    def test_invalid_ai_output_is_rejected(self):
        with patch("core.ai._call", return_value={"lead_score": "99", "ideal_customer_fit": True}):
            self.assertIn("error", ai.review_evidence(qualified_process(candidate(), settings()), "", "automation"))


class SheetTests(unittest.TestCase):
    def test_sync_preserves_existing_crm_and_uses_actual_header_order(self):
        ws = Mock()
        headers = ["email", "status", "domain", "company_name"]
        before = [headers, ["old@clinic.co.uk", "CONTACTED", "clinic.co.uk", "Clinic"]]
        ws.row_values.return_value = headers
        ws.row_count = 100
        ws.get.side_effect = [before, before + [["info@new.com", "QUALIFIED", "new.com", "New"]]]
        ws.spreadsheet.url = 'https://docs.google.com/spreadsheets/d/fixture/edit'
        leads = [dict(domain="clinic.co.uk", email="old@clinic.co.uk", qualification_status="QUALIFIED", status="QUALIFIED"),
                 dict(domain="new.com", email="info@new.com", qualification_status="QUALIFIED", status="QUALIFIED", company_name="New")]
        with patch("core.sheets.get_or_create_worksheet", return_value=ws):
            self.assertEqual(sheets.append_new_qualified(leads)['inserted'], 1)
        ws.batch_update.assert_not_called()
        ws.update.assert_called_once_with(range_name='A3', values=[["info@new.com", "QUALIFIED", "new.com", "New"]], value_input_option="RAW")


if __name__ == "__main__":
    unittest.main()
