"""Explicit Chrome integration test; localhost fixture, no Lieta account/requests.

Run: python tests/check_session_recovery.py -v
Optional: LIETA_TEST_CHROMEDRIVER points to an already installed ChromeDriver.
"""
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from selenium.webdriver.chrome.webdriver import WebDriver as ChromeDriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from lieta_automator.page_state import read_page_state
from lieta_automator.request_flow import LoginRequired, RequestCoordinator, RetryPolicy, fetch_result
from lieta_automator.scraper import LietaScraper
from lieta_automator.config import MODELS


PAGE = """<!doctype html><html><body>
<button role="combobox" aria-expanded="false" onclick="menu.hidden=false;this.setAttribute('aria-expanded','true')">Select model...</button>
<div id="menu" hidden></div>
<input placeholder="Ticker"><button type="submit" onclick="requestModel(this)">Submit</button>
<button>Download</button><div id="result"></div><div role="alert" id="notice"></div>
<script>
for (const model of ['Gamma','Term','Smile','TV Code','Table']) {
  const option = document.createElement('button'); option.role='option'; option.textContent=model;
  option.onclick=()=>{document.querySelector('[role=combobox]').textContent=model;menu.hidden=true;};
  menu.appendChild(option);
}
async function requestModel(button) {
  window.requestCompleted=false;
  button.disabled=true;
  const ticker=document.querySelector('input').value;
  const model=document.querySelector('[role=combobox]').textContent;
  const data=await (await fetch('/request?ticker='+encodeURIComponent(ticker)+'&model='+encodeURIComponent(model))).json();
  if (data.error) {
    notice.textContent=data.error;
    if (data.transient) setTimeout(()=>{notice.textContent='';window.requestCompleted=true;},100);
    return; // deliberately stays busy until refresh, even after the notice disappears
  }
  notice.textContent='';button.disabled=false;
  result.innerHTML=model==='TV Code' ? '<p>'+ticker+': verified-code</p>' :
    model==='Table' ? '<svg class="main-svg"><text>Expiration</text><text>Gex</text><text>Dex</text><text>Total</text><text>100</text><text>200</text></svg>' :
    '<svg class="main-svg"><text>'+ticker+' '+model+' fresh</text></svg>';
  window.requestCompleted=true;
}
</script></body></html>"""


class Fixture(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass  # Chrome can close an unused speculative connection at teardown.

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_error(404)
            return
        if parsed.path == "/request":
            query = parse_qs(parsed.query)
            self.server.requests.append((query["ticker"][0], query["model"][0]))
            failed = self.server.mode == "permanent" or self.server.visits < 2
            body = json.dumps({"error": "Unauthorized request. Please log in again." if failed else "",
                               "transient": self.server.mode == "flash"})
            kind = "application/json"
        else:
            self.server.visits += 1
            body = ('<html><body><a href="/auth">Log in</a><input type="password"></body></html>'
                    if self.server.mode == "login" and self.server.visits > 1 else PAGE)
            kind = "text/html"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class BrowserRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-background-networking")
        service = Service(executable_path=os.environ.get("LIETA_TEST_CHROMEDRIVER"))
        cls.driver = ChromeDriver(service=service, options=options)
        cls.addClassCleanup(cls.driver.quit)
        cls.driver.set_page_load_timeout(5)

    def setUp(self):
        self.server.visits = 0
        self.server.requests = []
        self.server.mode = "recover"
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        url = f"http://127.0.0.1:{self.server.server_port}/"
        self.url_patch = patch("lieta_automator.config.LIETA_AUTOMATION_URL", url)
        self.url_patch.start()
        self.addCleanup(self.url_patch.stop)
        policy = RetryPolicy(minimum_interval=.1, response_timeout=.5, ticker_timeout=8,
                             poll_interval=.05, stable_for=.1)
        self.coordinator = RequestCoordinator(policy)
        self.scraper = LietaScraper(self.folder.name, 0, self.coordinator)
        self.scraper.driver = self.driver
        original_wait = self.scraper._wait
        self.scraper._wait = lambda timeout=None: original_wait(timeout=.5)

    def fetch(self, model):
        self.scraper._select_model(model)
        self.scraper._fill_ticker("VRT")
        def submit():
            self.scraper._submit()
            if self.server.mode == "flash":
                # Hold the Selenium call until the 100ms toast has disappeared.
                self.driver.execute_async_script("""
                    const done=arguments[arguments.length-1];
                    const check=()=>window.requestCompleted ? done() : setTimeout(check,10);
                    check();
                """)
        return fetch_result(
            lambda: read_page_state(self.driver, "VRT", model), submit,
            self.coordinator, lambda message: None,
            recover_session=lambda: self.scraper._restore_session("VRT", model),
        )

    def test_all_models_refresh_and_resubmit_same_ticker(self):
        self.server.mode = "flash"
        for model in MODELS:
            with self.subTest(model=model):
                self.server.visits = 0
                self.server.requests = []
                result = self.fetch(model)
                self.assertTrue(result.ready and result.matches)
                self.assertFalse(result.session_expired)
                self.assertEqual(self.server.visits, 2)
                self.assertEqual(self.server.requests, [("VRT", model), ("VRT", model)])

    def test_repeated_unauthorized_is_bounded(self):
        self.server.mode = "permanent"
        with self.assertRaises(LoginRequired):
            self.fetch("Term")
        self.assertEqual(self.server.visits, 3)
        self.assertEqual(len(self.server.requests), 3)
        self.assertIsNotNone(self.coordinator.login_error)

    def test_login_page_after_refresh_stops(self):
        self.server.mode = "login"
        with self.assertRaises(LoginRequired):
            self.fetch("Term")
        self.assertEqual(self.server.visits, 2)
        self.assertEqual(self.server.requests, [("VRT", "Term")])

    def test_visible_toast_is_distinct_from_hidden_text_and_try_again(self):
        self.scraper._select_model("Term")
        self.driver.execute_script("notice.hidden=true;notice.innerHTML='<span>Unauthorized request.</span> <b>Please log in again.</b>'")
        self.assertFalse(read_page_state(self.driver, "VRT", "Term").session_expired)
        self.driver.execute_script("notice.hidden=false")
        self.assertTrue(read_page_state(self.driver, "VRT", "Term").session_expired)
        self.driver.execute_script("notice.hidden=true")
        self.assertTrue(read_page_state(self.driver, "VRT", "Term").session_expired)
        self.scraper._select_model("Term", refresh=True)
        self.driver.execute_script("notice.hidden=false;notice.textContent='Please try again'")
        state = read_page_state(self.driver, "VRT", "Term")
        self.assertFalse(state.session_expired)
        self.assertTrue(state.errors)

    def flash_notice(self, text):
        # Both creation and removal occur between two Python DOM reads.
        self.driver.execute_async_script("""
            const text = arguments[0], done = arguments[arguments.length - 1];
            const toast = document.createElement('div');
            toast.textContent = text;
            document.body.appendChild(toast);
            setTimeout(() => { toast.remove(); done(); }, 100);
        """, text)

    def test_unauthorized_removed_between_reads_is_retained_until_refresh(self):
        self.scraper._select_model("Smile")
        self.scraper._fill_ticker("ASPI")
        read_page_state(self.driver, "ASPI", "Smile")
        self.flash_notice("Unauthorized request. Please log in again.")
        self.assertNotIn("Unauthorized", self.driver.find_element("tag name", "body").text)
        self.assertTrue(read_page_state(self.driver, "ASPI", "Smile").session_expired)
        self.assertTrue(read_page_state(self.driver, "ASPI", "Smile").session_expired)
        self.scraper._restore_session("ASPI", "Smile")
        self.assertFalse(read_page_state(self.driver, "ASPI", "Smile").session_expired)

    def test_try_again_removed_between_reads_has_a_stable_event_identity(self):
        self.scraper._select_model("TV Code")
        before = read_page_state(self.driver, "BWXT", "TV Code")
        self.flash_notice("Please Try Again")
        after = read_page_state(self.driver, "BWXT", "TV Code")
        self.assertTrue(after.errors - before.errors)
        self.assertFalse(after.session_expired)
        self.assertEqual(after.errors, read_page_state(self.driver, "BWXT", "TV Code").errors)
        self.flash_notice("Please Try Again")
        self.assertTrue(read_page_state(self.driver, "BWXT", "TV Code").errors - after.errors)

    def test_reused_toast_element_creates_a_new_event_after_hiding(self):
        self.scraper._select_model("TV Code")
        for _ in range(2):
            before = read_page_state(self.driver, "BWXT", "TV Code")
            self.driver.execute_async_script("""
                const done=arguments[arguments.length-1];
                notice.hidden=false;notice.textContent='Please Try Again';
                setTimeout(()=>{notice.hidden=true;done();},100);
            """)
            self.assertTrue(read_page_state(self.driver, "BWXT", "TV Code").errors - before.errors)


if __name__ == "__main__":
    unittest.main()
