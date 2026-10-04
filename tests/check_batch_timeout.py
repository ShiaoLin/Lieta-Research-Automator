"""Opt-in multi-Chrome test with a real 90-second timeout; localhost only."""
from collections import Counter
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
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_session_recovery import Fixture, PAGE
from check_batch_browser import DOWNLOAD
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from lieta_automator.batch import BatchRunner, MODELS
from lieta_automator.scraper import LietaScraper


class TimeoutFixture(Fixture):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/request':
            query = parse_qs(parsed.query)
            model, ticker = query['model'][0], query['ticker'][0]
            with self.server.lock:
                self.server.requests.append((model, ticker, time.monotonic()))
                count = sum(m == model and t == ticker for m, t, _ in self.server.requests)
            if model == 'Term' and ticker == 'LATE' and count == 1:
                # The server completes after the client has abandoned tracking.
                time.sleep(95)
                self.server.late_completed.set()
            body = json.dumps({'error': 'Please Try Again' if model == 'Smile' and ticker == 'RETRY' else '',
                               'transient': True})
            kind = 'application/json'
        elif parsed.path == '/favicon.ico':
            self.send_error(404)
            return
        else:
            body, kind = PAGE.replace('<button>Download</button>', DOWNLOAD), 'text/html'
        encoded = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class TimeoutBrowserTests(unittest.TestCase):
    def test_real_timeout_late_result_and_bounded_second_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            server = ThreadingHTTPServer(('127.0.0.1', 0), TimeoutFixture)
            server.requests, server.lock = [], threading.Lock()
            server.late_completed = threading.Event()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

            class LocalScraper(LietaScraper):
                def setup_driver(self):
                    options = Options()
                    options.add_argument('--headless=new')
                    options.add_argument('--disable-background-networking')
                    self.driver = WebDriver(options=options, service=Service(os.environ.get('LIETA_TEST_CHROMEDRIVER')))
                    self.driver.set_page_load_timeout(5)
                    return True

            with patch('lieta_automator.config.BASE_DIR', folder), \
                    patch('lieta_automator.config.LIETA_AUTOMATION_URL', f'http://127.0.0.1:{server.server_port}/'), \
                    patch('lieta_automator.batch.chrome_launcher.prepare_profiles'), \
                    patch('lieta_automator.batch.chrome_launcher.launch_chrome_in_debug_mode', return_value=True), \
                    patch('lieta_automator.batch.chrome_launcher.wait_for_chrome', return_value=True):
                runner = BatchRunner(['LATE', 'RETRY', 'OK'], MODELS, folder, scraper_factory=LocalScraper)
                events = []
                original_event = runner.event
                def event(model, ticker, name, **details):
                    events.append((model, ticker, name, time.monotonic(), details))
                    original_event(model, ticker, name, **details)
                runner.event = event
                worker = threading.Thread(target=runner.run)
                worker.start()
                try:
                    worker.join(360)
                    self.assertFalse(worker.is_alive(), runner.snapshot())
                    counts = Counter((m, t) for m, t, _ in server.requests)
                    self.assertEqual(counts['Smile', 'RETRY'], 4)
                    self.assertEqual(counts['Term', 'LATE'], 2)
                    self.assertEqual(runner.metrics['timeouts'], 1)
                    self.assertEqual(runner.metrics['abandoned'], 1)
                    self.assertTrue(server.late_completed.is_set())
                    self.assertEqual(runner.data['status'], 'incomplete')
                    for model, journal in runner.journals.items():
                        for ticker in runner.tickers:
                            self.assertEqual(journal.completed(ticker), (model, ticker) != ('Smile', 'RETRY'))
                    # The first abandoned Term request cannot produce a saved completion.
                    term_events = [e for e in events if e[0:2] == ('Term', 'LATE')]
                    timeout_at = next(e[3] for e in term_events if e[2] == 'timeouts')
                    submitted_at = next(e[3] for e in term_events if e[2] == 'submissions')
                    self.assertGreaterEqual(timeout_at - submitted_at, 90)
                    self.assertEqual([e[4]['pass_number'] for e in term_events if e[2] == 'complete'], [2])
                    # Actual HTTP arrivals retain the configured five-second spacing.
                    arrivals = sorted(t for _, _, t in server.requests)
                    self.assertTrue(all(b - a >= 4.8 for a, b in zip(arrivals, arrivals[1:])))
                    for _, _, name, when, details in events:
                        if name == 'cooldown':
                            following = [t for t in arrivals if t > when]
                            if following:
                                self.assertGreaterEqual(min(following) - when, details['seconds'] - .2)
                    total = len(MODELS) * len(runner.tickers)
                    print(f'{total - 1}/{total} verified; only deliberate permanent error remains. Real 90s timeout, late result, 5s spacing and cooldown passed.')
                finally:
                    runner.request_stop()
                    worker.join(35)


if __name__ == '__main__':
    unittest.main()
