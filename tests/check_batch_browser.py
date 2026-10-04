"""Opt-in five independent Chrome integration test; localhost only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_session_recovery import Fixture, PAGE
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from lieta_automator.batch import BatchRunner, MODELS
from lieta_automator.scraper import LietaScraper

DOWNLOAD = """<button onclick="const a=document.createElement('a');
const model=document.querySelector('[role=combobox]').textContent;
const table='<table><tr><th>Expiration</th><th>Gex</th><th>Dex</th></tr><tr><td>Total</td><td>100</td><td>200</td></tr></table>';
a.href='data:text/html;charset=utf-8,'+encodeURIComponent('<html>'+(model==='Table'?table:document.querySelector('input').value+' '+model)+'</html>');
a.download='model.html';a.click();">Download</button>"""


class BatchFixture(Fixture):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/request":
            q = parse_qs(parsed.query)
            model, ticker = q["model"][0], q["ticker"][0]
            with self.server.lock:
                self.server.requests.append((model, ticker, time.monotonic()))
            body = json.dumps({"error": "Unauthorized request. Please log in again."
                               if model == "Gamma" and self.server.block_gamma else "", "transient": True})
            kind = "application/json"
        elif parsed.path == "/favicon.ico":
            self.send_error(404)
            return
        else:
            body = PAGE.replace("<button>Download</button>", DOWNLOAD)
            kind = "text/html"
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class MultiBrowserTests(unittest.TestCase):
    def test_other_models_finish_while_gamma_paused_then_gamma_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            server = ThreadingHTTPServer(("127.0.0.1", 0), BatchFixture)
            server.block_gamma, server.requests, server.lock = True, [], threading.Lock()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = f"http://127.0.0.1:{server.server_port}/"

            class LocalScraper(LietaScraper):
                def setup_driver(self):
                    options = Options()
                    options.add_argument("--headless=new")
                    options.add_argument("--disable-background-networking")
                    self.driver = WebDriver(options=options,
                                            service=Service(os.environ.get("LIETA_TEST_CHROMEDRIVER")))
                    self.driver.set_page_load_timeout(5)
                    return True

            with patch("lieta_automator.config.BASE_DIR", folder), \
                    patch("lieta_automator.config.LIETA_AUTOMATION_URL", url), \
                    patch("lieta_automator.batch.chrome_launcher.prepare_profiles"), \
                    patch("lieta_automator.batch.chrome_launcher.launch_chrome_in_debug_mode", return_value=True), \
                    patch("lieta_automator.batch.chrome_launcher.wait_for_chrome", return_value=True):
                runner = BatchRunner(["VRT", "SPY"], MODELS, folder, scraper_factory=LocalScraper)
                runner.dispatch.interval = .1  # Local fixture only; production default remains 5.
                thread = threading.Thread(target=runner.run)
                thread.start()
                try:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        states, _ = runner.snapshot()
                        if states["Gamma"]["state"] == "等待登入" and all(states[m]["state"] == "完成" for m in MODELS[1:]):
                            break
                        time.sleep(.1)
                    self.assertEqual(states["Gamma"]["state"], "等待登入", states)
                    self.assertTrue(all(states[m]["state"] == "完成" for m in MODELS[1:]), states)
                    self.assertEqual(sum(m == "Gamma" for m, t, when in server.requests), 3)
                    server.block_gamma = False
                    runner.continue_model("Gamma")
                    thread.join(20)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(runner.data["status"], "complete")
                    self.assertTrue(all(j.completed(t) for j in runner.journals.values() for t in runner.tickers))
                    for ticker in runner.tickers:
                        item = runner.journals['Table'].data['items'][ticker]
                        target = Path(item['path'])
                        self.assertEqual(target.parent, Path(folder) / 'Table' / ticker)
                        self.assertTrue(target.name.endswith(f'_{ticker}_Table.html'))
                        LietaScraper._validate_html(target, ticker, 'Table')
                    ordered = sorted(when for m, t, when in server.requests)
                    self.assertTrue(all(b - a >= .09 for a, b in zip(ordered, ordered[1:])))
                finally:
                    runner.request_stop()
                    thread.join(35)


if __name__ == "__main__":
    unittest.main()
