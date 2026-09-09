from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lieta_automator.batch import BatchRunner, Deferred, MODELS
from lieta_automator.dispatch import Dispatcher, Stopped
from lieta_automator.journal import RunJournal
from lieta_automator.request_flow import PageState, LoginRequired
from lieta_automator.scraper import LietaScraper


class Clock:
    now = 0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class DispatchTests(unittest.TestCase):
    def test_spacing_limit_and_round_robin(self):
        clock = Clock()
        d = Dispatcher(2, clock=clock)
        self.assertTrue(d._grant("Gamma"))
        d.submitted("Gamma")
        self.assertFalse(d._grant("Term"))
        clock.sleep(5)
        self.assertTrue(d._grant("Term"))
        d.submitted("Term")
        clock.sleep(5)
        self.assertFalse(d._grant("Smile"))
        self.assertFalse(d._grant("TV Code"))
        d.release("Gamma")
        self.assertFalse(d._grant("Gamma"))
        self.assertTrue(d._grant("Smile"))
        d.submitted("Smile")
        d.release("Term")
        clock.sleep(5)
        self.assertTrue(d._grant("TV Code"))

    def test_cooldown_and_reset_both_required(self):
        clock = Clock()
        d = Dispatcher(clock=clock)
        self.assertTrue(d._grant("Gamma"))
        d.submitted("Gamma")
        self.assertEqual(d.fail("Gamma", needs_reset=True), 10)
        d.release("Gamma")
        clock.sleep(20)
        self.assertFalse(d._grant("Term"))
        d.reset_done("Gamma")
        self.assertTrue(d._grant("Term"))
        self.assertEqual([d.fail("Term") for _ in range(5)], [20, 40, 80, 120, 120])
        d.success()
        self.assertEqual(d.fail("Term"), 120)

    def test_completion_does_not_release_another_threads_submission_gate(self):
        clock = Clock()
        d = Dispatcher(2, clock=clock)
        d.acquire("Gamma")
        d.submitted("Gamma")
        clock.sleep(5)
        d.acquire("Term")
        d.submitted("Gamma")
        d.release("Gamma")
        self.assertFalse(d._grant("Smile"))
        d.submitted("Term")
        clock.sleep(5)
        self.assertTrue(d._grant("Smile"))

    def test_stop_wakes_queued_worker(self):
        d = Dispatcher()
        d.acquire("Gamma")
        errors = []
        def wait():
            try:
                d.acquire("Term")
            except Stopped:
                errors.append(True)
        thread = threading.Thread(target=wait)
        thread.start()
        d.request_stop()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [True])
        self.assertNotIn("Term", d.queue)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        p = patch("lieta_automator.config.BASE_DIR", str(self.root))
        p.start()
        self.addCleanup(p.stop)

    def runner(self, models=None):
        return BatchRunner(["VRT", "SPY"], models or ["Term"], self.root)

    def flow(self, scenario):
        runner = self.runner()
        clock = Clock()
        runner.dispatch = Dispatcher(clock=clock)
        runner.dispatch.wait = clock.sleep
        # Deterministic admission advances the fake clock through shared cooldown.
        def acquire(owner):
            clock.now = max(clock.now, runner.dispatch.next_submit, runner.dispatch.cooldown_until)
            self.assertTrue(runner.dispatch._grant(owner))
        runner.dispatch.acquire = acquire
        scraper = Mock()
        counts = {"submit": 0, "reset": 0}
        scraper._submit.side_effect = lambda: counts.update(submit=counts["submit"] + 1)
        scraper._select_model.side_effect = lambda *a, **k: counts.update(reset=counts["reset"] + 1)
        return runner, scraper, counts, clock, patch("lieta_automator.batch.read_page_state",
                         side_effect=lambda *a: scenario(counts, clock)), patch("lieta_automator.batch.time",
                         SimpleNamespace(monotonic=clock))

    def test_busy_timeout_defers_and_resets_before_releasing_recovery_barrier(self):
        r, scraper, counts, clock, state, time = self.flow(lambda n, c: PageState(busy=n["submit"] > 0))
        with state, time, self.assertRaises(Deferred):
            r._fetch(scraper, "Term", "VRT")
        self.assertEqual(clock.now, 90)
        self.assertEqual(counts, {"submit": 1, "reset": 1})
        self.assertEqual(r.metrics["abandoned"], 1)
        self.assertFalse(r.dispatch.active)
        self.assertFalse(r.dispatch.recovering)
        self.assertEqual(r.dispatch.cooldown_until, 100)

    def test_try_again_retries_once_then_defers(self):
        def observe(n, c):
            return PageState(errors=frozenset({str(n["submit"])}) if n["submit"] > n["reset"] else frozenset())
        r, scraper, counts, clock, state, time = self.flow(observe)
        with state, time, self.assertRaises(Deferred):
            r._fetch(scraper, "Term", "VRT")
        self.assertEqual(counts["submit"], 2)
        self.assertEqual(counts["reset"], 2)

    def test_unauthorized_resends_separate_from_normal_retry(self):
        def observe(n, c):
            if n["submit"] <= n["reset"]:
                return PageState()
            if n["submit"] <= 2:
                return PageState(session_expired=True)
            return PageState(result_key="fresh", ready=True, matches=True)
        r, scraper, counts, clock, state, time = self.flow(observe)
        with state, time:
            self.assertEqual(r._fetch(scraper, "Term", "VRT").result_key, "fresh")
        self.assertEqual(counts, {"submit": 3, "reset": 2})
        self.assertFalse(r.dispatch.active)

    def test_permanent_unauthorized_raises_only_for_worker(self):
        def observe(n, c):
            return PageState(session_expired=n["submit"] > n["reset"])
        r, scraper, counts, clock, state, time = self.flow(observe)
        with state, time, self.assertRaises(LoginRequired):
            r._fetch(scraper, "Term", "VRT")
        self.assertEqual(counts, {"submit": 3, "reset": 2})
        self.assertFalse(r.dispatch.stop.is_set())
        self.assertFalse(r.dispatch.active)

    def test_failed_items_only_get_one_second_pass(self):
        r = self.runner()
        scraper = Mock()
        output = self.root / "SPY.html"
        output.write_text("<html>SPY</html>")
        scraper._download_html.return_value = output
        calls = []
        def fetch(scraper, model, ticker):
            calls.append(ticker)
            if ticker == "VRT":
                raise Deferred("failed")
            return PageState(result_key="fresh")
        with patch.object(r, "_fetch", side_effect=fetch):
            r._model(scraper, "Term")
        self.assertEqual(calls, ["VRT", "SPY", "VRT"])
        self.assertTrue(r.journals["Term"].completed("SPY"))
        self.assertFalse(r.journals["Term"].completed("VRT"))

    def test_batch_resume_validates_files_and_legacy_multiline_tv(self):
        r = self.runner(["TV Code"])
        text = "VRT: line one\nline two"
        path = LietaScraper._save_tv_code("VRT", text, self.root)
        LietaScraper._save_tv_code("VRT", text, self.root)
        self.assertEqual(path.read_text(encoding="utf-8").count("line one"), 1)
        r.journals["TV Code"].record("VRT", path=path, content=text)
        resumed = BatchRunner(resume=r.path)
        self.assertTrue(resumed.journals["TV Code"].completed("VRT"))
        legacy = BatchRunner(resume=r.journals["TV Code"].path)
        self.assertTrue(legacy.journals["TV Code"].completed("VRT"))
        path.write_text("corrupt")
        self.assertFalse(legacy.journals["TV Code"].completed("VRT"))

    def test_final_summary_lists_only_remaining_failures_and_detects_missing_files(self):
        r = self.runner()
        output = self.root / 'SPY.html'
        output.write_text('<html>SPY Term</html>')
        r.journals['Term'].record('SPY', path=output)
        r.journals['Term'].record('VRT', error='Try Again：本輪兩次一般提交均失敗。')
        r.data['status'] = 'incomplete'
        summary = r.final_summary()
        self.assertEqual(summary['failed'], 1)
        self.assertEqual(summary['failures'][0]['ticker'], 'VRT')
        self.assertIn('Term / VRT', summary['text'])
        output.unlink()
        self.assertEqual(r.final_summary()['failed'], 2)

    def test_retry_resume_requests_only_failed_model_ticker_pairs(self):
        r = self.runner(['Gamma', 'Term'])
        for model in r.models:
            for ticker in r.tickers:
                path = self.root / f'{model}_{ticker}.html'
                path.write_text(f'<html>{ticker} {model}</html>')
                r.journals[model].record(ticker, path=path)
        r.journals['Term'].record('VRT', error='Try Again')
        resumed = BatchRunner(resume=r.path)
        scraper = Mock()
        output = self.root / 'retry.html'
        output.write_text('<html>VRT Term</html>')
        scraper._download_html.return_value = output
        with patch.object(resumed, '_fetch', return_value=PageState(result_key='fresh')) as fetch:
            for model in resumed.models:
                resumed._model(scraper, model)
        self.assertEqual([(c.args[1], c.args[2]) for c in fetch.call_args_list], [('Term', 'VRT')])

    def test_paused_model_does_not_prevent_other_model_completion(self):
        r = self.runner(["Gamma", "TV Code"])
        def factory(folder, port):
            scraper = Mock()
            scraper.setup_driver.return_value = True
            scraper.check_login_status.return_value = port != config_port()
            scraper._save_tv_code.side_effect = LietaScraper._save_tv_code
            return scraper
        def config_port():
            return 9222
        r.scraper_factory = factory
        with patch("lieta_automator.batch.chrome_launcher.prepare_profiles"), \
                patch("lieta_automator.batch.chrome_launcher.launch_chrome_in_debug_mode", return_value=True), \
                patch("lieta_automator.batch.chrome_launcher.wait_for_chrome", return_value=True), \
                patch.object(r, "_fetch", side_effect=lambda scraper, model, ticker: PageState(text=ticker + ": code")):
            thread = threading.Thread(target=r.run)
            thread.start()
            import time
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if r.snapshot()[0]["TV Code"]["state"] == "完成":
                    break
                time.sleep(.02)
            self.assertEqual(r.snapshot()[0]["Gamma"]["state"], "等待登入")
            self.assertEqual(r.snapshot()[0]["TV Code"]["state"], "完成")
            r.request_stop()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(r.data["status"], "stopped")
