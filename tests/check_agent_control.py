"""Separate GUI/client processes with a local fake downloader, no browser/site."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def configure(folder):
    from lieta_automator import config
    config.BASE_DIR = str(folder)
    config.TEMP_DOWNLOAD_DIR_NAME = str(folder / 'downloads')


def host(folder):
    configure(folder)
    import tkinter as tk
    from lieta_automator.agent_control import ControlHost
    from lieta_automator.agent_plan import AgentPlan
    from lieta_automator.batch import BatchRunner
    from lieta_automator.dispatch import Stopped
    from lieta_automator.gui import TickerApp
    from lieta_automator.request_flow import PageState

    class LocalBatch(BatchRunner):
        def _fetch(self, scraper, model, ticker):
            while not (folder / 'release').exists():
                self.dispatch.wait(.05)
            return PageState(result_key='fresh')

        def _worker(self, models, port):
            scraper = Mock()
            scraper.check_login_status.return_value = True
            def save(ticker, model, destination, key):
                target = Path(destination) / (ticker + '.html')
                target.write_text('<html>' + ticker + '</html>')
                return target
            scraper._download_html.side_effect = save
            try:
                self._model(scraper, models[0])
            except Stopped:
                self.state(models[0], '已停止')

    class LocalPlan(AgentPlan):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, batch_factory=LocalBatch)

    with patch('lieta_automator.gui.AgentPlan', LocalPlan), patch('lieta_automator.chrome_launcher.prepare_profiles'):
        root = tk.Tk()
        root.withdraw()
        app = TickerApp(root)
        control = ControlHost(app)
        def check_close():
            if (folder / 'close').exists():
                app.on_closing()
            else:
                root.after(50, check_close)
        root.after(50, check_close)
        try:
            root.mainloop()
        finally:
            control.close()


class ProcessTests(unittest.TestCase):
    def test_gui_survives_client_exit_and_same_request_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, script = Path(directory), str(Path(__file__).resolve())
            process = subprocess.Popen([sys.executable, script, '--host', directory],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP |
                              subprocess.CREATE_BREAKAWAY_FROM_JOB)
            try:
                deadline = time.monotonic() + 15
                state_path = folder / 'agent_control' / 'state.json'
                while not state_path.exists() and time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), 'GUI host exited during startup')
                    time.sleep(.1)
                self.assertTrue(state_path.exists())
                source = folder / 'list.txt'
                source.write_text('VRT')
                output = folder / 'output'
                output.mkdir()
                plan = folder / 'plan.json'
                plan.write_text(json.dumps({'version': 1, 'jobs': [{'id': 'first', 'tickers_file': str(source),
                    'destination': str(output), 'models': ['Gamma']}]}), encoding='utf-8')
                response = folder / 'response.json'
                def client(*args):
                    result = subprocess.run([sys.executable, script, '--client', directory, *args,
                        '--output', str(response)], capture_output=True, timeout=35)
                    if result.returncode:
                        diagnostic = result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
                        for log in folder.glob('log_*.jsonl'):
                            diagnostic += log.read_text(encoding='utf-8')[-6000:]
                        self.fail(diagnostic)
                    return json.loads(response.read_text(encoding='utf-8'))
                arguments = ['--agent', 'start', '--plan', str(plan), '--request-id', 'start1', '--task-id', 'daily']
                self.assertTrue(client(*arguments)['ok'])
                self.assertIsNone(process.poll())
                self.assertTrue(client(*arguments)['ok'])
                state = client('--agent', 'status', '--task-id', 'daily')
                self.assertEqual(state['task']['id'], 'daily')
                self.assertFalse(state['task']['terminal'])
                (folder / 'release').touch()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    state = json.loads(state_path.read_text(encoding='utf-8'))
                    if state['task']['terminal']:
                        break
                    time.sleep(.1)
                self.assertEqual(state['task']['summary']['completed'], 1)
                self.assertEqual(len(list((folder / 'agent_runs').glob('*.json'))), 1)
                self.assertEqual(len(list((folder / 'runs').glob('batch_*.json'))), 1)
                self.assertTrue((output / 'VRT.html').is_file())
                self.assertEqual(len(list(folder.glob('log_*.jsonl'))), 1)
            finally:
                (folder / 'close').touch()
                process.wait(timeout=15)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--host':
        host(Path(sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == '--client':
        configure(Path(sys.argv[2]))
        from lieta_automator.main import main
        sys.exit(main(sys.argv[3:]))
    else:
        unittest.main()
