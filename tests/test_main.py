import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lieta_automator import main


class MainTests(unittest.TestCase):
    def test_cli_and_saved_settings_use_shared_runner(self):
        with tempfile.TemporaryDirectory() as folder:
            tickers = Path(folder) / "tickers.txt"
            tickers.write_text("\ufeffSPY\nQQQ\n", encoding="utf-8")
            with patch("lieta_automator.batch.BatchRunner") as runner, \
                    patch("lieta_automator.instance_lock.InstanceLock"), \
                    patch("lieta_automator.settings.load_settings", return_value={"enable_multi_window": True}):
                runner.return_value.run.return_value = 1
                self.assertEqual(main.main(["--run-automated", "--tickers-file", str(tickers),
                                            "--destination", folder, "--models", "Gamma", "Term"]), 1)
                args, kwargs = runner.call_args
                self.assertEqual(args[0], ["SPY", "QQQ"])
                self.assertTrue(kwargs["multi"])
                self.assertEqual(kwargs["limit"], 1)

    def test_resume_batch_and_explicit_limit(self):
        with patch("lieta_automator.batch.BatchRunner") as runner, \
                patch("lieta_automator.instance_lock.InstanceLock"):
            runner.return_value.run.return_value = 0
            self.assertEqual(main.main(["--resume-batch", "batch.json", "--max-inflight", "2"]), 0)
            self.assertEqual(runner.call_args.kwargs["resume"], "batch.json")
            self.assertEqual(runner.call_args.kwargs["limit"], 2)

    def test_legacy_resume_forwarded(self):
        with patch("lieta_automator.batch.BatchRunner") as runner, \
                patch("lieta_automator.instance_lock.InstanceLock"):
            runner.return_value.run.return_value = 0
            self.assertEqual(main.main(["--resume", "model.json"]), 0)
            self.assertEqual(runner.call_args.kwargs["resume"], "model.json")

    def test_conflicting_resume_arguments_rejected(self):
        with self.assertRaises(SystemExit):
            main.main(["--resume", "a", "--resume-batch", "b"])

    def test_background_entry_never_initializes_gui(self):
        with patch.object(main, "_run_automated", return_value=0) as runner:
            self.assertEqual(main.main(["--run-automated"]), 0)
            runner.assert_called_once()
