from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lieta_automator import config, main
from lieta_automator.batch import BatchRunner, MODELS
from lieta_automator.page_state import read_page_state
from lieta_automator.request_flow import PageState
from lieta_automator.scraper import LietaScraper
from lieta_automator.table_result import valid_table_export


def export(columns=None):
    trace = {'type': 'table', 'header': {'values': ['Expiration', 'Gex', 'Dex']},
             'cells': {'values': columns if columns is not None else [['Total', '2026-10-05'], [100, 60], [200, 70]]}}
    return '<html><script>Plotly.newPlot("id", ' + json.dumps([trace]) + ', {});</script></html>'


class TableTests(unittest.TestCase):
    def test_export_without_ticker_is_valid_but_empty_or_unrelated_exports_are_rejected(self):
        self.assertTrue(valid_table_export(export()))
        for content in (export([[], [], []]), export([['Total'], [1, 2], [3]]),
                        '<html>Expiration Gex Dex Total 123</html>',
                        '<html><script>Plotly.newPlot("id", [{"type":"table","header":null}]);</script></html>',
                        export().replace('"type": "table"', '"type": "scatter"')):
            self.assertFalse(valid_table_export(content))

    def test_native_table_requires_data_rows(self):
        headers = '<tr><th>Expiration</th><th>Gex</th><th>Dex</th></tr>'
        self.assertFalse(valid_table_export('<html><table>' + headers + '</table></html>'))
        self.assertTrue(valid_table_export('<html><table>' + headers +
                         '<tr><td>Total</td><td>100</td><td>200</td></tr></table></html>'))

    def test_table_page_uses_selected_model_and_input_and_real_rows(self):
        part = {'node': SimpleNamespace(id='new-result'), 'html': '<svg>table</svg>',
                'text': 'Expiration\nGex\nDex\nTotal\n100\n200'}
        snapshot = dict(authenticated=True, sessionExpired=False, selectedModel='Table', inputTicker='NVDA',
                        busy=False, downloadable=True, charts=[part], tables=[], paragraphs=[], errors=[])
        driver = Mock()
        driver.execute_script.return_value = snapshot
        self.assertTrue(read_page_state(driver, 'NVDA', 'Table').matches)
        self.assertFalse(read_page_state(driver, 'AAPL', 'Table').matches)
        snapshot['selectedModel'] = 'Gamma'
        self.assertFalse(read_page_state(driver, 'NVDA', 'Table').matches)
        snapshot.update(selectedModel='Table', charts=[], tables=[part])
        self.assertTrue(read_page_state(driver, 'NVDA', 'Table').ready)
        part['text'] = 'Expiration Gex Dex'
        self.assertFalse(read_page_state(driver, 'NVDA', 'Table').ready)

    def test_table_download_preserves_content_and_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'NVDA_ltable.html'
            source.write_text(export(), encoding='utf-8')
            scraper = LietaScraper(root / 'downloads', 9226)
            scraper.driver = Mock()
            state = PageState(ready=True, matches=True, result_key='fresh')
            with patch('lieta_automator.scraper.read_page_state', return_value=state), \
                    patch('lieta_automator.scraper.datetime') as clock, \
                    patch.object(scraper, '_wait', return_value=Mock()), \
                    patch.object(scraper, '_wait_for_download', return_value=source):
                clock.now.return_value = datetime(2026, 10, 4, 9, 9)
                target = scraper._download_html('NVDA', 'Table', root / 'output', 'fresh')
            self.assertEqual(target.relative_to(root / 'output').as_posix(), 'Table/NVDA/2026-10-04_09;09_NVDA_Table.html')
            self.assertEqual(source.read_bytes(), target.read_bytes())
            with self.assertRaises(ValueError):
                scraper._validate_html(source, 'NVDA', 'Gamma')

    def test_five_models_have_ports_and_legacy_indices_stay_unchanged(self):
        self.assertEqual(MODELS[:4], ('Gamma', 'Term', 'Smile', 'TV Code'))
        self.assertEqual(MODELS[4], 'Table')
        self.assertGreaterEqual(len(config.REMOTE_DEBUGGING_PORTS), len(MODELS))
        self.assertEqual(config.REMOTE_DEBUGGING_PORTS[4], 9226)

    def test_table_reload_before_each_ticker_and_resume_skips_completed_files(self):
        with tempfile.TemporaryDirectory() as folder, patch('lieta_automator.config.BASE_DIR', folder):
            r = BatchRunner(['NVDA', 'AAPL'], ['Table'], folder)
            scraper = Mock()
            calls = []
            scraper._select_model.side_effect = lambda *a, **k: calls.append('reset' if k.get('refresh') else 'select')
            scraper._fill_ticker.side_effect = lambda t: calls.append(t)
            def save(ticker, model, destination, key):
                path = Path(folder) / (ticker + '.html')
                path.write_text(export())
                return path
            scraper._download_html.side_effect = save
            with patch.object(r, '_fetch', return_value=PageState(result_key='fresh')):
                r._model(scraper, 'Table')
            self.assertEqual(calls, ['select', 'reset', 'NVDA', 'reset', 'AAPL'])
            resumed = BatchRunner(resume=r.path)
            with patch.object(resumed, '_fetch') as fetch:
                resumed._model(scraper, 'Table')
            fetch.assert_not_called()

    def test_table_cli_is_accepted_with_default_wait_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            tickers = Path(folder) / 'tickers.txt'
            tickers.write_text('NVDA')
            with patch('lieta_automator.batch.BatchRunner') as runner, patch('lieta_automator.instance_lock.InstanceLock'):
                runner.return_value.run.return_value = 0
                self.assertEqual(main.main(['--run-automated', '--tickers-file', str(tickers), '--destination', folder,
                                           '--models', 'Table']), 0)
                self.assertEqual(runner.call_args.args[1], ['Table'])
                self.assertEqual(runner.call_args.kwargs['limit'], 1)
