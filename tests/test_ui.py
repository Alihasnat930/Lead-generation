from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest
from core.store import Store
from core.config import config


class UITests(unittest.TestCase):
    def test_navigation_and_campaign_creation_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "ui.db")
            with patch("core.store.Store", return_value=store), patch("requests.get") as get, patch("requests.post") as post:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
                self.assertEqual(len(app.exception), 0)
                for page in ("Lead database", "Settings", "Outreach & CRM", "Campaigns"):
                    app.sidebar.radio[0].set_value(page).run()
                    self.assertEqual(len(app.exception), 0, page)
                next(button for button in app.button if button.label == "Create campaign").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(len(store.jobs()), 1)
                job = store.job(store.jobs()[0]["id"])
                self.assertEqual(job["status"], "ready")
                self.assertEqual(job["settings"]["target"], 1000)
                self.assertEqual(job["settings"]["provider"], "free")
                self.assertFalse(job["settings"]["use_ai"])
                self.assertEqual(len(job["settings"]["niches"]), 10)
                self.assertEqual(len(job["settings"]["locations"]), 150)
                self.assertTrue(any(button.label == "Start campaign" for button in app.button))
                get.assert_not_called()
                post.assert_not_called()

    def test_real_background_process_resumes_from_checkpoint_without_network(self):
        from core.campaigns import create_campaign, launch
        from test_campaigns import settings
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "worker.db")
            job_id = create_campaign(settings(max_search_calls=1), store)
            with store.db() as db:
                db.execute("UPDATE jobs SET search_calls=1 WHERE id=?", (job_id,))
            # The already exhausted allowance must stop the child before any network request.
            with patch.object(config, "SERPAPI_API_KEY", "fixture-unused"):
                launch(job_id, store)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                job = store.job(job_id)
                if job["status"] not in ("starting", "running"):
                    break
                time.sleep(0.2)
            self.assertEqual(job["status"], "search_limit")
            self.assertEqual(job["search_calls"], 1)


if __name__ == "__main__":
    unittest.main()
