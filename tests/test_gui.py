import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from lieta_automator.gui import TickerApp


class GuiTests(unittest.TestCase):
    def test_status_continue_and_safe_close_use_worker_control(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(lambda: root.destroy() if root.winfo_exists() else None)
        with tempfile.TemporaryDirectory() as folder, \
                patch("lieta_automator.gui.settings.load_settings", return_value={}), \
                patch("lieta_automator.config.TEMP_DOWNLOAD_DIR_NAME", folder), \
                patch.object(TickerApp, "_setup_logging"):
            app = TickerApp(root)
            runner = Mock()
            runner.done = threading.Event()
            runner.dispatch.stop = threading.Event()
            runner.snapshot.return_value = ({"Gamma": {"state": "等待登入", "ticker": "VRT", "success": 2,
                                                        "pending": 3, "deferred": 1}},
                                            {"cooldown": 9, "active": 1, "limit": 2})
            app.runner = runner
            app.automation_running = True
            app._poll_batch()
            self.assertIn("等待登入", app.model_status["Gamma"][0].cget("text"))
            app.model_status["Gamma"][1].invoke()
            runner.continue_model.assert_called_once_with("Gamma")
            app.on_closing()
            runner.request_stop.assert_called_once()
            self.assertTrue(root.winfo_exists())
            self.assertTrue(app.closing)
            runner.close_driver.assert_not_called()

    def make_app(self, folder):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        for patcher in (patch("lieta_automator.gui.settings.load_settings", return_value={}),
                        patch("lieta_automator.config.TEMP_DOWNLOAD_DIR_NAME", folder),
                        patch.object(TickerApp, "_setup_logging")):
            patcher.start()
            self.addCleanup(patcher.stop)
        return TickerApp(root)

    def test_inputs_locked_during_download_and_stop_preserves_window(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.make_app(folder)
            self.assertEqual(str(app.start_button['state']), 'disabled')
            app.tickers, app.destination_path = ['VRT'], folder
            app.selected_models['Term'].set(True)
            app.validate_inputs()
            self.assertEqual(str(app.start_button['state']), 'normal')
            runner = Mock(done=threading.Event())
            app.runner = runner
            app.automation_running = True
            app.toggle_ui_state(False)
            self.assertTrue(all(str(cb['state']) == 'disabled' for cb in app.model_checks))
            self.assertEqual(str(app.settings_button['state']), 'disabled')
            app.stop_button.invoke()
            runner.request_stop.assert_called_once()
            self.assertTrue(app.root.winfo_exists())
            self.assertFalse(app.closing)

    def test_resume_form_uses_journal_destination_and_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.make_app(folder)
            runner = Mock(tickers=['VRT'], models=['Term'], destination=folder)
            with patch('lieta_automator.gui.BatchRunner', return_value=runner), \
                    patch('lieta_automator.gui.settings.save_settings'), \
                    patch('lieta_automator.gui.threading.Thread'):
                app._start_batch('saved.json')
            self.assertEqual(app.destination_path, folder)
            self.assertEqual(app.dest_label['text'], folder)
            self.assertEqual(app.tickers, ['VRT'])
            self.assertEqual([m for m, v in app.selected_models.items() if v.get()], ['Term'])
            self.assertEqual(app.input_hint['text'], '1 個 ticker × 1 個模型')

    def test_finished_incomplete_batch_is_not_shown_as_all_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.make_app(folder)
            runner = Mock(done=threading.Event(), data={'status': 'incomplete'})
            runner.done.set()
            runner.snapshot.return_value = ({'Term': {'state': '完成，仍有失敗', 'ticker': '',
                'success': 36, 'pending': 1, 'deferred': 1}}, {'cooldown': 0, 'active': 0, 'limit': 1})
            app.runner, app.automation_running = runner, True
            app._poll_batch()
            self.assertIn('未完成 1', app.overall_label['text'])
            self.assertIn('36 / 37', app.model_counts['Term']['text'])
            self.assertEqual(app.model_status['Gamma'][0]['text'], '本批次未選擇')
            self.assertEqual(str(app.resume_button['state']), 'normal')

    def test_retry_button_uses_finished_batch_not_current_form(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.make_app(folder)
            runner = Mock(done=threading.Event(), path='original-batch.json')
            runner.done.set()
            app.runner = runner
            with patch.object(app, '_start_batch') as start:
                app.retry_button.invoke()
                start.assert_called_once_with('original-batch.json')
                app.automation_running = True
                app._retry_failed()
                self.assertEqual(start.call_count, 1)

    def test_final_summary_exposes_failed_ticker_and_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self.make_app(folder)
            runner = Mock(done=threading.Event(), data={'status': 'incomplete', 'summary': {
                'failed': 1, 'text': '成功 147/148\nSmile / LITE：Try Again'}})
            runner.done.set()
            runner.snapshot.return_value = ({'Smile': {'state': '完成，仍有失敗', 'ticker': '',
                'success': 36, 'pending': 1}}, {'cooldown': 0, 'active': 0, 'limit': 1})
            app.runner, app.automation_running = runner, True
            app._poll_batch()
            self.assertIn('Smile / LITE', app.summary_text.get('1.0', 'end'))
            self.assertEqual(str(app.retry_button['state']), 'normal')
