from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from lieta_automator.journal import RunJournal
from lieta_automator.page_state import contains_ticker, read_page_state
from lieta_automator.scraper import LietaScraper
from lieta_automator.request_flow import PageState


def part(text, identity="node"):
    return {"node": SimpleNamespace(id=identity), "html": "<p>" + text + "</p>", "text": text}


class PageStateTests(unittest.TestCase):
    def snapshot(self, model="Gamma", **updates):
        value = dict(authenticated=True, sessionExpired=False, selectedModel=model, busy=False,
                     downloadable=True, charts=[], paragraphs=[], errors=[])
        value.update(updates)
        driver = Mock()
        driver.execute_script.return_value = value
        return driver

    def test_ticker_boundaries(self):
        self.assertTrue(contains_ticker("$SPX Gamma", "SPX"))
        self.assertFalse(contains_ticker("SPXL Gamma", "SPX"))
        self.assertFalse(contains_ticker("BRK.B", "BRK"))
        self.assertTrue(contains_ticker("BRK.B Gamma", "BRK.B"))

    def test_chart_must_match_ticker_and_selected_model(self):
        driver = self.snapshot(charts=[part("NVDA Gamma")])
        self.assertFalse(read_page_state(driver, "AAPL", "Gamma").matches)
        self.assertFalse(read_page_state(driver, "NVDA", "Term").matches)
        self.assertTrue(read_page_state(driver, "NVDA", "Gamma").matches)
        driver = self.snapshot(model="Term", charts=[part("NVDA Gamma")])
        self.assertFalse(read_page_state(driver, "NVDA", "Term").matches)

    def test_tv_code_does_not_match_suffix_of_other_ticker(self):
        driver = self.snapshot(model="TV Code", paragraphs=[part("BASX: wrong")])
        self.assertFalse(read_page_state(driver, "ASX", "TV Code").ready)

    def test_text_is_captured_without_reading_stale_webelement_properties(self):
        driver = self.snapshot(model="TV Code", paragraphs=[part("ASX: 123")])
        result = read_page_state(driver, "ASX", "TV Code")
        self.assertEqual(result.text, "ASX: 123")
        driver.execute_script.assert_called_once()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_invalid_or_wrong_ticker_html_is_rejected(self):
        html = self.root / "model.html"
        for content in ("<html>AAPL", "<html>NVDA</html>", ""):
            html.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                LietaScraper._validate_html(html, "AAPL")
        html.write_text("<html><body>AAPL Gamma</body></html>", encoding="utf-8")
        LietaScraper._validate_html(html, "AAPL")

    def test_download_keeps_the_original_minute_precision_filename(self):
        scraper = LietaScraper(str(self.root / "downloads"), 9222)
        scraper.driver = Mock()
        source = self.root / "source.html"
        source.write_text("<html>AAOI Gamma</html>", encoding="utf-8")
        state = PageState(ready=True, matches=True, busy=False, result_key="fresh")
        with patch("lieta_automator.scraper.read_page_state", return_value=state), \
                patch("lieta_automator.scraper.datetime") as clock, \
                patch.object(scraper, "_wait", return_value=Mock()), \
                patch.object(scraper, "_wait_for_download", return_value=source):
            clock.now.return_value = datetime(2026, 9, 3, 19, 31, 39, 997616)
            path = scraper._download_html("AAOI", "Gamma", self.root / "output", "fresh")
        self.assertEqual(path.name, "2026-09-03_19;31_AAOI_Gamma.html")
        self.assertEqual(path.read_text(encoding="utf-8"), "<html>AAOI Gamma</html>")

    def test_model_selection_recovers_from_a_click_before_ui_is_ready(self):
        scraper = LietaScraper(str(self.root), 9222)
        state = {"selected": "Select model...", "expanded": False, "clicks": 0}

        class Button:
            @property
            def text(self):
                return state["selected"]

            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            def get_attribute(self, name):
                return "true" if state["expanded"] else "false"

            def click(self):
                state["clicks"] += 1
                state["expanded"] = state["clicks"] > 1

        class Option(Button):
            def click(self):
                state["selected"] = "Gamma"
                state["expanded"] = False

        def find(by, selector):
            if "option" in selector:
                if not state["expanded"]:
                    raise NoSuchElementException()
                return Option()
            return Button()

        scraper.driver = Mock()
        scraper.driver.find_element.side_effect = find
        scraper.driver.execute_script.side_effect = (
            lambda script, *elements: elements[0].click() if elements else None)

        def until(condition):
            try:
                result = condition(scraper.driver)
            except NoSuchElementException:
                raise TimeoutException()
            if not result:
                raise TimeoutException()
            return result

        wait = Mock()
        wait.until.side_effect = until
        with patch.object(scraper, "_wait", return_value=wait):
            scraper._select_model("Gamma")
        self.assertEqual(state["selected"], "Gamma")
        self.assertEqual(state["clicks"], 2)

    def test_tv_code_retry_does_not_duplicate_and_keeps_other_tickers(self):
        path = LietaScraper._save_tv_code("AAPL", "AAPL: 123", self.root)
        LietaScraper._save_tv_code("NVDA", "NVDA: 456", self.root)
        LietaScraper._save_tv_code("AAPL", "AAPL: 123", self.root)
        self.assertEqual(path.read_text(encoding="utf-8"), "AAPL: 123\nNVDA: 456\n")

    def test_resume_rechecks_files_and_detects_corruption(self):
        with patch("lieta_automator.journal.config.BASE_DIR", str(self.root)):
            journal = RunJournal(["AAPL", "NVDA"], "Gamma", str(self.root))
        file = self.root / "AAPL.html"
        file.write_text("<html>AAPL</html>", encoding="utf-8")
        journal.record("AAPL", path=file)
        resumed = RunJournal(["AAPL", "NVDA"], "Gamma", str(self.root), journal.path)
        self.assertTrue(resumed.completed("AAPL"))
        self.assertFalse(resumed.completed("NVDA"))
        file.write_text("corrupt", encoding="utf-8")
        self.assertFalse(resumed.completed("AAPL"))

    def test_tv_resume_survives_additional_ticker_in_shared_file(self):
        with patch("lieta_automator.journal.config.BASE_DIR", str(self.root)):
            journal = RunJournal(["AAPL", "NVDA"], "TV Code", str(self.root))
        path = LietaScraper._save_tv_code("AAPL", "AAPL: 123", self.root)
        journal.record("AAPL", path=path, content="AAPL: 123")
        LietaScraper._save_tv_code("NVDA", "NVDA: 456", self.root)
        self.assertTrue(journal.completed("AAPL"))

    def test_resume_cannot_silently_change_destination(self):
        with patch("lieta_automator.journal.config.BASE_DIR", str(self.root)):
            journal = RunJournal(["AAPL"], "Gamma", str(self.root))
        with self.assertRaises(ValueError):
            RunJournal(["AAPL"], "Gamma", str(self.root / "other"), journal.path)


if __name__ == "__main__":
    unittest.main()
