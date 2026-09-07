import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lieta_automator.request_flow import LoginRequired, PageState, RequestCoordinator
from lieta_automator.scraper import LietaScraper


class SessionBatchTests(unittest.TestCase):
    def test_login_stop_preserves_success_and_blocks_other_models_and_windows(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("lieta_automator.journal.config.BASE_DIR", folder):
            output = Path(folder) / "AAPL.html"
            output.write_text("<html>AAPL Gamma</html>", encoding="utf-8")
            coordinator = RequestCoordinator()
            scraper = LietaScraper(folder, 9222, coordinator=coordinator)
            tickers = ["AAPL", "NVDA", "VRT"]
            with patch.object(scraper, "_wait_for_slot"), \
                    patch.object(scraper, "_select_model"), patch.object(scraper, "_fill_ticker"), \
                    patch.object(scraper, "_download_html", return_value=output), \
                    patch("lieta_automator.scraper.fetch_result",
                          side_effect=[PageState(result_key="fresh"), LoginRequired("重新登入後續跑")]) as fetch:
                self.assertEqual(scraper.run_automation(tickers, "Gamma", folder),
                                 ["NVDA (Gamma)", "VRT (Gamma)"])
                self.assertEqual(fetch.call_count, 2)
            gamma = scraper.journal
            self.assertTrue(gamma.completed("AAPL"))
            self.assertFalse(gamma.completed("NVDA"))
            self.assertIn("重新登入", gamma.data["items"]["VRT"]["error"])
            # The same window's next model and a separate window share the stop.
            for adapter, model in ((scraper, "Term"),
                                   (LietaScraper(folder, 9223, coordinator), "Smile")):
                with patch.object(adapter, "_select_model") as select, \
                        patch.object(adapter, "_submit") as submit:
                    self.assertEqual(len(adapter.run_automation(tickers, model, folder)), 3)
                    select.assert_not_called()
                    submit.assert_not_called()
                    self.assertEqual(len(adapter.journal.data["items"]), 3)
            # A new batch after login can resume only the unfinished items.
            fresh = LietaScraper(folder, 9222, RequestCoordinator())
            with patch.object(fresh, "_wait_for_slot"), patch.object(fresh, "_select_model"), \
                    patch.object(fresh, "_fill_ticker"), \
                    patch("lieta_automator.scraper.fetch_result", return_value=PageState(result_key="new")) as fetch, \
                    patch.object(fresh, "_download_html", return_value=output):
                self.assertEqual(fresh.run_automation(tickers, "Gamma", folder, gamma.path), [])
                self.assertEqual(fetch.call_count, 2)

    def test_restore_session_refreshes_and_refills_without_submitting(self):
        scraper = LietaScraper("downloads", 9222, RequestCoordinator())
        operations = Mock()
        with patch.object(scraper, "_select_model", operations.select), \
                patch.object(scraper, "_fill_ticker", operations.fill), \
                patch.object(scraper, "_submit", operations.submit):
            scraper._restore_session("VRT", "Term")
        self.assertEqual([call[0] for call in operations.mock_calls], ["select", "fill"])
        operations.select.assert_called_once_with("Term", refresh=True)
        operations.fill.assert_called_once_with("VRT")

    def test_unauthorized_chart_is_not_downloaded(self):
        scraper = LietaScraper("downloads", 9222, RequestCoordinator())
        scraper.driver = Mock()
        state = PageState(ready=True, matches=True, result_key="fresh", session_expired=True)
        with patch("lieta_automator.scraper.read_page_state", return_value=state):
            with self.assertRaisesRegex(RuntimeError, "Unauthorized"):
                scraper._download_html("VRT", "Term", "output", "fresh")
        scraper.driver.execute_cdp_cmd.assert_not_called()


if __name__ == "__main__":
    unittest.main()
