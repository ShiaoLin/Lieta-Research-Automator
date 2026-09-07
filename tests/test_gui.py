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
