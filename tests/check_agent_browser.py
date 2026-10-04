"""Opt-in five-browser, two-list agent plan test against localhost only."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_batch_browser import BatchFixture
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from lieta_automator.agent_plan import AgentPlan
from lieta_automator.batch import BatchRunner
from lieta_automator.config import MODELS
from lieta_automator.scraper import LietaScraper


class AgentBrowserTests(unittest.TestCase):
    def test_other_models_finish_both_lists_while_gamma_waits_for_login(self):
        with tempfile.TemporaryDirectory() as folder:
            server = ThreadingHTTPServer(('127.0.0.1', 0), BatchFixture)
            server.block_gamma, server.requests, server.lock = True, [], threading.Lock()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            jobs = []
            for index, ticker in enumerate(('VRT', 'SPY')):
                source = Path(folder) / f'list{index}.txt'
                source.write_text(ticker)
                destination = Path(folder) / f'output{index}'
                destination.mkdir()
                jobs.append({'id': f'list{index}', 'tickers_file': str(source),
                             'destination': str(destination), 'models': list(MODELS)})

            class LocalScraper(LietaScraper):
                def setup_driver(self):
                    options = Options()
                    options.add_argument('--headless=new')
                    options.add_argument('--disable-background-networking')
                    self.driver = WebDriver(options=options, service=Service(os.environ.get('LIETA_TEST_CHROMEDRIVER')))
                    self.driver.set_page_load_timeout(5)
                    return True

            def factory(*args, **kwargs):
                return BatchRunner(*args, **kwargs, scraper_factory=LocalScraper)

            with patch('lieta_automator.config.BASE_DIR', folder), \
                 patch('lieta_automator.config.TEMP_DOWNLOAD_DIR_NAME', str(Path(folder) / 'downloads')), \
                 patch('lieta_automator.config.LIETA_AUTOMATION_URL', f'http://127.0.0.1:{server.server_port}/'), \
                 patch('lieta_automator.chrome_launcher.prepare_profiles'), \
                 patch('lieta_automator.chrome_launcher.launch_chrome_in_debug_mode', return_value=True), \
                 patch('lieta_automator.chrome_launcher.wait_for_chrome', return_value=True):
                plan = AgentPlan(Path(folder) / 'plan.json', spec={'version': 1, 'jobs': jobs}, batch_factory=factory)
                plan.dispatch.interval = .1  # Local fixture; production remains at least 5 seconds.
                worker = threading.Thread(target=plan.run)
                worker.start()
                try:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        states, _ = plan.snapshot()
                        if states['Gamma']['state'] == '等待登入' and all(states[m]['success'] == 2 for m in MODELS[1:]):
                            break
                        time.sleep(.1)
                    self.assertEqual(states['Gamma']['state'], '等待登入', states)
                    self.assertTrue(all(states[m]['success'] == 2 for m in MODELS[1:]), states)
                    self.assertFalse(plan.done.is_set())
                    server.block_gamma = False
                    plan.continue_model('Gamma')
                    worker.join(25)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(plan.data['summary']['completed'], 10)
                    self.assertEqual(plan.data['summary']['failed'], 0)
                    self.assertTrue(all(e['extra_retries'] == 0 for j in plan.data['jobs'] for e in j['runs'].values()))
                    for index, ticker in enumerate(('VRT', 'SPY')):
                        tables = list((Path(folder) / f'output{index}' / 'Table' / ticker).glob('*.html'))
                        self.assertEqual(len(tables), 1)
                        LietaScraper._validate_html(tables[0], ticker, 'Table')
                finally:
                    plan.request_stop()
                    worker.join(35)


if __name__ == '__main__':
    unittest.main()
