import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from lieta_automator.agent_plan import AgentPlan, atomic_json, validate_spec
from lieta_automator.agent_control import ControlHost, send, status
from lieta_automator.batch import BatchRunner, Deferred
from lieta_automator.dispatch import Dispatcher, Stopped
from lieta_automator.request_flow import PageState


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        patcher = patch('lieta_automator.config.BASE_DIR', str(self.folder))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('lieta_automator.chrome_launcher.prepare_profiles')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.login = threading.Event()
        self.login.set()
        self.calls, self.ports = [], []
        self.permanent_failure = False
        self.first_failure = False
        owner = self

        class ScriptBatch(BatchRunner):
            def _fetch(batch, scraper, model, ticker):
                batch.dispatch.acquire(model)
                try:
                    owner.calls.append((model, ticker))
                    batch.dispatch.submitted(model)
                    if model == 'Gamma' and (owner.permanent_failure or (owner.first_failure and len(owner.calls) <= 2)):
                        raise Deferred('fixture failure')
                    return PageState(text=ticker + ': code', result_key='fresh')
                finally:
                    batch.dispatch.release(model)

            def _worker(batch, models, port):
                owner.ports.append((models[0], port))
                scraper = Mock()
                scraper.check_login_status.side_effect = lambda: models[0] != 'Term' or owner.login.is_set()
                def save(ticker, model, destination, key):
                    path = Path(destination) / (model + '_' + ticker + '.html')
                    path.write_text('<html>' + model + ' ' + ticker + '</html>')
                    return path
                scraper._download_html.side_effect = save
                try:
                    batch._model(scraper, models[0])
                except Stopped:
                    batch.state(models[0], '已停止')
        self.factory = ScriptBatch

    def spec(self, models=('Gamma',), count=2):
        jobs = []
        for index in range(count):
            ticker = self.folder / f'list{index}.txt'
            ticker.write_text(['VRT', 'SPY'][index])
            destination = self.folder / f'output{index}'
            destination.mkdir(exist_ok=True)
            jobs.append({'id': f'job{index}', 'tickers_file': str(ticker),
                         'destination': str(destination), 'models': list(models)})
        return {'version': 1, 'jobs': jobs}

    def plan(self, spec=None, **kwargs):
        return AgentPlan(self.folder / 'task.json', spec=spec,
                         dispatcher=Dispatcher(interval=0), batch_factory=self.factory, **kwargs)

    def wait_for(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.01)
        self.fail('Condition did not become true')

    def test_lists_keep_destinations_ports_and_source_snapshot(self):
        plan = self.plan(self.spec(models=('Gamma', 'Table')))
        (self.folder / 'list0.txt').write_text('WRONG')
        plan.run()
        self.assertEqual(plan.data['status'], 'complete')
        self.assertEqual(plan.data['summary']['completed'], 4)
        self.assertNotIn(('Gamma', 'WRONG'), self.calls)
        self.assertTrue((self.folder / 'output1' / 'Table_SPY.html').exists())
        self.assertEqual(set(self.ports), {('Gamma', 9222), ('Table', 9226)})

    def test_one_extra_retry_is_bounded_and_resume_does_not_reset_it(self):
        self.permanent_failure = True
        plan = self.plan(self.spec(count=1))
        plan.run()
        self.assertEqual(len(self.calls), 4)  # Two internal passes, then one extra batch with two passes.
        self.assertEqual(plan.data['jobs'][0]['runs']['Gamma']['extra_retries'], 1)
        self.assertEqual(plan.data['summary']['failures'][0]['ticker'], 'VRT')
        # Emulate a crash after the extra batch finished but before the parent checkpoint.
        plan.data['jobs'][0]['runs']['Gamma']['phase'] = 'extra'
        plan._save()
        resumed = self.plan()
        resumed.run()
        self.assertEqual(len(self.calls), 4)
        self.permanent_failure = False
        retried = self.plan(retry=True)
        retried.run()
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(retried.data['status'], 'complete')

    def test_extra_retry_recovers_then_moves_to_next_list(self):
        self.first_failure = True
        plan = self.plan(self.spec())
        plan.run()
        self.assertEqual(self.calls, [('Gamma', 'VRT')] * 3 + [('Gamma', 'SPY')])
        self.assertEqual(plan.data['status'], 'complete')

    def test_login_pause_does_not_block_other_models_next_list(self):
        self.login.clear()
        plan = self.plan(self.spec(models=('Gamma', 'Term')))
        thread = threading.Thread(target=plan.run)
        thread.start()
        try:
            self.wait_for(lambda: ('Gamma', 'SPY') in self.calls and 'Term' in plan.report()['needs_login'])
            self.assertFalse(plan.done.is_set())
            self.assertEqual(plan.snapshot()[0]['Term']['task'], 'job0')
            self.login.set()
            plan.continue_model('Term')
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(plan.data['status'], 'complete')
            self.assertTrue(all(e['extra_retries'] == 0 for j in plan.data['jobs'] for e in j['runs'].values()))
        finally:
            plan.request_stop()
            thread.join(5)

    def test_stop_during_login_saves_and_resume_preserves_completed_outputs(self):
        self.login.clear()
        plan = self.plan(self.spec(models=('Gamma', 'Term')))
        thread = threading.Thread(target=plan.run)
        thread.start()
        try:
            self.wait_for(lambda: ('Gamma', 'SPY') in self.calls and 'Term' in plan.report()['needs_login'])
        finally:
            plan.request_stop()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(plan.data['status'], 'stopped')
        before = self.calls.count(('Gamma', 'VRT'))
        self.login.set()
        resumed = self.plan()
        resumed.run()
        self.assertEqual(resumed.data['status'], 'complete')
        self.assertEqual(self.calls.count(('Gamma', 'VRT')), before)

    def test_invalid_plan_is_rejected_before_any_job_is_saved(self):
        spec = self.spec()
        spec['jobs'][1]['destination'] = str(self.folder / 'missing')
        with self.assertRaises(ValueError):
            self.plan(spec)
        self.assertFalse((self.folder / 'task.json').exists())
        self.assertEqual(self.calls, [])

    def test_cooldown_survives_continuation(self):
        dispatcher = Dispatcher()
        dispatcher.fail('Gamma')
        dispatcher.request_stop()
        successor = dispatcher.continuation()
        self.assertFalse(successor.stop.is_set())
        self.assertGreater(successor.snapshot()['cooldown'], 9)
        self.assertEqual(successor.fail('Table'), 20)

    def host(self):
        app = Mock()
        app.agent_status.return_value = {'state': 'idle', 'task': None}
        app.agent_command.return_value = {'accepted': True}
        host = ControlHost(app, self.folder / 'control')
        host.publish()
        return host, app

    def test_mailbox_repeated_request_runs_once_and_conflicts_fail(self):
        host, app = self.host()
        command = {'action': 'stop', 'task_id': 'test'}
        replies = []
        client = threading.Thread(target=lambda: replies.append(send(command, 'op1', root=host.root, timeout=2)))
        client.start()
        self.wait_for(lambda: bool(list((host.root / 'commands').glob('*.json'))))
        host.poll()
        client.join(3)
        self.assertTrue(replies[0]['ok'])
        self.assertTrue(send(command, 'op1', root=host.root)['ok'])
        app.agent_command.assert_called_once_with(command)
        with self.assertRaises(ValueError):
            send({'action': 'retry', 'task_id': 'test'}, 'op1', root=host.root)

    def test_old_session_command_is_not_replayed_and_dead_heartbeat_is_offline(self):
        host, app = self.host()
        atomic_json(host.root / 'commands' / 'old.json', {'request_id': 'old', 'session': 'old-session',
                    'command': {'action': 'start', 'task_id': 'test'}})
        host.poll()
        app.agent_command.assert_not_called()
        self.assertFalse(json.loads((host.root / 'receipts' / 'old.json').read_text(encoding='utf-8'))['ok'])
        state = status(host.root)
        self.assertTrue(state['connected'])
        state['heartbeat'] -= 20
        atomic_json(host.root / 'state.json', state)
        self.assertFalse(status(host.root)['connected'])

    def test_ack_timeout_is_unknown_not_safe_to_start_again(self):
        host, app = self.host()
        result = send({'action': 'stop', 'task_id': 'test'}, 'pending', root=host.root, timeout=.01)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertTrue((host.root / 'commands' / 'pending.json').exists())
        host.poll()
        self.assertTrue(send({'action': 'stop', 'task_id': 'test'}, 'pending', root=host.root)['ok'])
        app.agent_command.assert_called_once()

    def test_interrupted_command_receipt_prevents_replay(self):
        host, app = self.host()
        command = {'action': 'retry', 'task_id': 'test'}
        from lieta_automator.agent_protocol import fingerprint
        atomic_json(host.root / 'commands' / 'op.json', {'request_id': 'op', 'session': host.session, 'command': command})
        atomic_json(host.root / 'receipts' / 'op.json', {'ok': False, 'pending': True, 'outcome': 'unknown',
                    'request_id': 'op', 'command_hash': fingerprint(command)})
        host.poll()
        app.agent_command.assert_not_called()
        self.assertEqual(send(command, 'op', root=host.root)['outcome'], 'unknown')

    def test_abrupt_host_restart_adds_conservative_cooldown(self):
        host, app = self.host()
        app.agent_status.return_value = {'state': 'running', 'task': {'terminal': False, 'pacing': {}}}
        host.publish()
        replacement_app = Mock()
        ControlHost(replacement_app, host.root)
        self.assertGreater(replacement_app.pacing_state['not_before'], time.time() + 119)
