"""Durable multi-list plans; one sequential worker per model shares request pacing."""
import json
from pathlib import Path
import re
import tempfile
import threading
import time

from . import config, chrome_launcher
from .batch import BatchRunner
from .dispatch import Dispatcher, Stopped
from .journal import RunJournal
from .agent_protocol import atomic_json, identifier, fingerprint


def validate_spec(spec):
    if not isinstance(spec, dict) or spec.get('version') != 1:
        raise ValueError('任務清單必須使用 version: 1。')
    jobs = spec.get('jobs')
    if not isinstance(jobs, list) or not jobs:
        raise ValueError('請至少提供一份任務。')
    result, seen = [], set()
    for job in jobs:
        key = identifier(job['id'])
        if key in seen:
            raise ValueError('清單內的任務 ID 不可重複。')
        seen.add(key)
        source, destination = Path(job['tickers_file']), Path(job['destination'])
        if not source.is_absolute() or not destination.is_absolute():
            raise ValueError('清單及目的地必須使用完整絕對路徑。')
        tickers = list(dict.fromkeys(t.strip().upper() for t in source.read_text(encoding='utf-8-sig').splitlines() if t.strip()))
        if not tickers or any(t in ('.', '..') or not re.fullmatch(r'[A-Z0-9.^_-]+', t) for t in tickers):
            raise ValueError(f'{key} 的 ticker 清單空白或有不支援字元。')
        models = job.get('models')
        if not isinstance(models, list) or not models or any(m not in config.MODELS for m in models):
            raise ValueError(f'{key} 必須明確指定有效模型。')
        if not destination.is_dir():
            raise ValueError(f'{key} 的存放資料夾不存在。')
        # Verify writes without retaining probe files or touching existing output.
        with tempfile.TemporaryFile(dir=destination) as probe:
            probe.write(b'lieta-write-check')
            probe.flush()
        result.append({'id': key, 'name': str(job.get('name') or key),
                       'tickers_file': str(source.resolve()), 'tickers': tickers,
                       'destination': str(destination.resolve()), 'models': list(dict.fromkeys(models)),
                       'runs': {}})
    return result


class AgentPlan:
    def __init__(self, path, *, spec=None, dispatcher=None, retry=False, batch_factory=BatchRunner):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.dispatch = dispatcher if dispatcher is not None else Dispatcher(1)
        self.done = threading.Event()
        self.threads, self.current = [], {}
        self.batch_factory = batch_factory
        self.multi = True
        if spec is not None:
            if self.path.exists():
                raise ValueError('此任務已存在，請查詢狀態或明確續跑。')
            jobs = validate_spec(spec)
            self.data = {'kind': 'agent_plan', 'version': 1, 'id': self.path.stem,
                         'spec_hash': fingerprint(spec), 'jobs': jobs, 'status': 'prepared',
                         'created_at': time.time(), 'extra_retry_limit': 1}
        else:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if self.data.get('kind') != 'agent_plan' or self.data.get('version') != 1:
                raise ValueError('不是支援的 AI 任務紀錄。')
            self.dispatch.restore_pacing(self.data.get('pacing', {}))
            for job in self.data['jobs']:
                if not Path(job['destination']).is_dir():
                    raise ValueError(f"{job['id']} 的原存放路徑無法使用。")
                for entry in job['runs'].values():
                    if retry:
                        entry['extra_retries'] = 0
                        entry['phase'] = 'initial'
            self.data.pop('summary', None)
            self.data.pop('errors', None)
            self.data['status'] = 'prepared'
        self.models = [m for m in config.MODELS if any(m in j['models'] for j in self.data['jobs'])]
        self._save()

    def _save(self):
        with self.lock:
            self.data['pacing'] = self.dispatch.pacing()
            self.data['updated_at'] = time.time()
            atomic_json(self.path, self.data)

    def request_stop(self):
        self.dispatch.request_stop()
        with self.lock:
            for _, runner in self.current.values():
                runner.request_stop()

    def continue_model(self, model):
        with self.lock:
            pair = self.current.get(model)
        if pair is None:
            raise ValueError('此模型沒有執行中的任務。')
        pair[1].continue_model(model)

    def _make_batch(self, job, model):
        with self.lock:
            entry = job['runs'].setdefault(model, {'batch': None, 'phase': 'initial', 'extra_retries': 0})
        runner = self.batch_factory(job['tickers'], [model], job['destination'],
                    resume=entry['batch'], dispatcher=self.dispatch,
                    port=config.REMOTE_DEBUGGING_PORTS[config.MODELS.index(model)], prepare_profiles=False)
        with self.lock:
            entry['batch'] = str(runner.path.resolve())
            self.current[model] = (job['id'], runner)
            self._save()  # Persist association before the first browser action.
        return runner, entry

    def _run_model(self, model):
        try:
            for job in self.data['jobs']:
                if model not in job['models']:
                    continue
                self.dispatch.check_stop()
                previous = job['runs'].get(model, {})
                if previous.get('phase') == 'finished' and previous.get('summary', {}).get('failed'):
                    continue  # Resume does not replenish an exhausted retry budget.
                if previous.get('batch') and previous.get('phase') in ('initial', 'extra'):
                    saved = json.loads(Path(previous['batch']).read_text(encoding='utf-8'))
                    if saved.get('status') in ('complete', 'incomplete'):
                        with self.lock:
                            if previous['phase'] == 'extra':
                                previous.update(phase='finished', summary=saved.get('summary'), states=saved['states'])
                                self._save()
                                continue  # Child finished before the parent's last checkpoint.
                            if saved['status'] == 'incomplete':
                                previous.update(phase='extra', extra_retries=1)
                                self._save()
                runner, entry = self._make_batch(job, model)
                runner.run()
                self.dispatch.check_stop()
                if runner.data['status'] == 'incomplete' and entry['extra_retries'] < 1:
                    with self.lock:
                        entry['extra_retries'] += 1
                        entry['phase'] = 'extra'
                        self._save()  # A crash cannot replenish the extra retry budget.
                    runner, entry = self._make_batch(job, model)
                    runner.run()
                    self.dispatch.check_stop()
                with self.lock:
                    entry['phase'] = 'finished'
                    entry['summary'] = runner.data.get('summary')
                    entry['states'] = runner.snapshot()[0]
                    self.current.pop(model, None)
                    self._save()
        except Stopped:
            pass
        except Exception as exc:
            with self.lock:
                self.data.setdefault('errors', {})[model] = str(exc)
                self._save()

    def snapshot(self):
        with self.lock:
            states = {}
            for model in self.models:
                total = sum(len(j['tickers']) for j in self.data['jobs'] if model in j['models'])
                success, deferred = 0, 0
                current_state = None
                pair = self.current.get(model)
                for job in self.data['jobs']:
                    entry = job['runs'].get(model, {})
                    state = entry.get('states', {}).get(model, {})
                    if pair and pair[0] == job['id']:
                        state = pair[1].snapshot()[0].get(model, {})
                        current_state = dict(state, task=job['name'])
                    success += state.get('success', 0)
                    deferred += state.get('deferred', 0)
                state = current_state or {'state': ('已停止' if self.dispatch.stop.is_set() else
                         '完成' if success == total else '完成，仍有失敗' if self.done.is_set() else '準備中'), 'ticker': ''}
                states[model] = dict(state, success=success, pending=total-success, deferred=deferred)
            return states, self.dispatch.snapshot()

    def report(self):
        with self.lock:
            value = json.loads(json.dumps(self.data))
            states, dispatch = self.snapshot()
            value.update(states=states, dispatch=dispatch, terminal=self.done.is_set(),
                         needs_login=[m for m, s in states.items() if s['state'] == '等待登入'])
            value['pacing'] = self.dispatch.pacing()
            return value

    def _summarize(self):
        failures, completed, total, lines = [], 0, 0, []
        for job in self.data['jobs']:
            for model in job['models']:
                entry = job['runs'].get(model, {})
                report = entry.get('summary')
                pair = self.current.get(model)
                if pair and pair[0] == job['id']:
                    report = pair[1].data.get('summary')
                if entry.get('batch'):
                    try:
                        batch = json.loads(Path(entry['batch']).read_text(encoding='utf-8'))
                        journal = RunJournal.read_only(batch['journals'][model])
                        missing = []
                        for ticker in job['tickers']:
                            if not journal.completed(ticker):
                                item = journal.data['items'].get(ticker, {})
                                missing.append({'model': model, 'ticker': ticker,
                                    'reason': item.get('error') or '檔案缺失或驗證失敗，等待續跑'})
                        report = {'completed': len(job['tickers']) - len(missing), 'failures': missing, 'failed': len(missing)}
                        with self.lock:
                            entry['summary'] = report
                            entry['states'] = {model: {'state': '完成' if not missing else '完成，仍有失敗',
                                'ticker': '', 'success': report['completed'], 'pending': len(missing)}}
                    except (OSError, ValueError, KeyError):
                        report = None
                total += len(job['tickers'])
                completed += report.get('completed', 0) if report else 0
                missing = report['failures'] if report else [
                    {'model': model, 'ticker': t, 'reason': self.data.get('errors', {}).get(model, '尚未完成，等待續跑')}
                    for t in job['tickers']]
                failures.extend(dict(item, job=job['id'], destination=job['destination']) for item in missing)
                lines.append(f"{job['name']} / {model}：{len(job['tickers']) - len(missing)}/{len(job['tickers'])}")
        heading = f'任務結束：成功 {completed}/{total}，未完成 {len(failures)} 項'
        details = [f"{f['job']} / {f['model']} / {f['ticker']}：{f['reason']}" for f in failures]
        return {'completed': completed, 'total': total, 'failed': len(failures), 'failures': failures,
                'text': '\n'.join([heading, *lines, *details, f'任務紀錄：{self.path}'])}

    def run(self):
        self.data['status'] = 'running'
        try:
            self._save()
            ports = [config.REMOTE_DEBUGGING_PORTS[config.MODELS.index(m)] for m in self.models]
            chrome_launcher.prepare_profiles(ports)
            for model in self.models:
                thread = threading.Thread(target=self._run_model, args=(model,), daemon=False)
                self.threads.append(thread)
                thread.start()
            for thread in self.threads:
                thread.join()
        except Exception as exc:
            self.data.setdefault('errors', {})['plan'] = str(exc)
            self.request_stop()
            for thread in self.threads:
                thread.join()
        finally:
            try:
                summary = self._summarize()
                with self.lock:
                    self.data['summary'] = summary
                    self.current.clear()
                    self.data['status'] = ('stopped' if self.dispatch.stop.is_set() else
                                           'complete' if self.data['summary']['failed'] == 0 else 'incomplete')
                    self._save()
            except Exception as exc:
                self.data['status'] = 'error'
                self.data['error'] = str(exc)
            finally:
                self.done.set()
