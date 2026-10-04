"""Local, durable command mailbox. Only the GUI process controls the browser."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from . import config
from .agent_protocol import atomic_json, fingerprint, identifier


def control_dir():
    return Path(config.BASE_DIR) / 'agent_control'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def status(root=None, task_id=None):
    root = Path(root) if root is not None else control_dir()
    try:
        state = read_json(root / 'state.json')
    except (OSError, ValueError):
        state = {'connected': False, 'state': 'not_running', 'schema_version': 1}
    state['connected'] = state.get('online', False) and 0 <= time.time() - state.get('heartbeat', 0) <= 8
    if not state['connected']:
        state['state'] = 'offline'
        state['next_action'] = 'open_app_then_resume' if state.get('task') else 'open_app'
    if task_id and (state.get('task') or {}).get('id') != identifier(task_id):
        saved = read_json(root.parent / 'agent_runs' / (task_id + '.json'))
        saved['terminal'] = saved['status'] in ('complete', 'incomplete', 'stopped', 'error')
        state['task'] = saved
        state['state'] = 'interrupted' if saved['status'] in ('running', 'prepared') else saved['status']
        state['next_action'] = 'resume' if state['state'] == 'interrupted' else 'review_summary'
    return state


class ControlHost:
    def __init__(self, app, root=None):
        self.app = app
        self.root = Path(root) if root is not None else control_dir()
        for name in ('commands', 'receipts'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.session = uuid.uuid4().hex
        self.stopped = False
        self.last_publish = 0
        previous = status(self.root).get('task') or {}
        self.app.pacing_state = previous.get('pacing', {})
        if previous and not previous.get('terminal', False):
            # An abrupt crash may lose the latest 1-second heartbeat. Do not
            # assume that outstanding server work was cancelled by that crash.
            self.app.pacing_state = dict(self.app.pacing_state,
                not_before=max(self.app.pacing_state.get('not_before', 0), time.time() + 120))
        self.app.root.after(50, self.poll)

    def publish(self, online=True):
        state = self.app.agent_status()
        state.update(schema_version=1, session=self.session, pid=os.getpid(),
                     heartbeat=time.time(), online=online)
        atomic_json(self.root / 'state.json', state)
        self.last_publish = time.monotonic()

    def poll(self):
        if self.stopped:
            return
        try:
            for path in sorted((self.root / 'commands').glob('*.json'))[:4]:
                request = None
                entered = False
                try:
                    request = read_json(path)
                    key = identifier(request['request_id'])
                    if path.stem != key:
                        raise ValueError('命令檔名與請求 ID 不符。')
                    receipt = self.root / 'receipts' / (key + '.json')
                    if receipt.exists():
                        path.unlink()
                        continue
                    if request['session'] != self.session:
                        raise ValueError('程式已重新啟動；此命令未執行。請先查詢任務狀態。')
                    atomic_json(receipt, {'ok': False, 'outcome': 'unknown', 'pending': True,
                        'request_id': key, 'command_hash': fingerprint(request['command']),
                        'error': '操作已開始，尚未確認結果；請查詢狀態，勿改用新 ID 重複送出。'})
                    entered = True
                    result = self.app.agent_command(request['command'])
                    self.publish()
                    reply = {'ok': True, 'result': result}
                except Exception as exc:
                    reply = {'ok': False, 'outcome': 'unknown' if entered else 'rejected', 'error': str(exc)}
                # The task file is saved before browser activity, making a lost
                # start acknowledgement recoverable with the same task ID.
                reply.update(request_id=path.stem, command_hash=fingerprint(request.get('command')) if isinstance(request, dict) else None)
                atomic_json(self.root / 'receipts' / path.name, reply)
                path.unlink(missing_ok=True)
            if time.monotonic() - self.last_publish >= 1:
                self.publish()
        except Exception:
            from .logger import logger
            logger.exception('AI 控制入口暫時無法更新；下載工作仍獨立執行。')
        finally:
            self.app.root.after(250, self.poll)

    def close(self):
        self.stopped = True
        self.publish(online=False)


def launch_gui():
    executable = [sys.executable]
    if not getattr(sys, 'frozen', False):
        executable.append(str(Path(__file__).resolve().parents[1] / 'run.py'))
    executable.append('--agent-host')
    # The downloader outlives the short command client and its console/job.
    subprocess.Popen(executable, cwd=config.BASE_DIR, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP |
                                   subprocess.CREATE_BREAKAWAY_FROM_JOB)


def send(command, request_id, *, root=None, auto_open=False, timeout=30):
    root = Path(root) if root is not None else control_dir()
    key = identifier(request_id)
    receipt = root / 'receipts' / (key + '.json')
    signature = fingerprint(command)
    if receipt.exists():
        reply = read_json(receipt)
        if reply.get('command_hash') != signature:
            raise ValueError('相同請求 ID 已用於不同指令，請勿重複使用。')
        return reply
    current = status(root)
    if not current['connected'] and auto_open:
        launch_gui()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            current = status(root)
            if current['connected']:
                break
            time.sleep(.2)
    if not current['connected']:
        raise RuntimeError('控制入口未就緒；請開啟新版程式。若舊版正在執行，請先正常結束它。')
    commands = root / 'commands'
    commands.mkdir(parents=True, exist_ok=True)
    path = commands / (key + '.json')
    message = {'request_id': key, 'session': current['session'], 'command': command}
    if path.exists():
        if fingerprint(read_json(path)['command']) != signature:
            raise ValueError('相同請求 ID 已有不同指令等待執行。')
    else:
        # Rename never overwrites another client's same-ID request on Windows.
        temporary = commands / (key + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(message, ensure_ascii=False), encoding='utf-8')
            try:
                os.rename(temporary, path)
            except FileExistsError:
                if fingerprint(read_json(path)['command']) != signature:
                    raise ValueError('相同請求 ID 發生衝突。')
        finally:
            temporary.unlink(missing_ok=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if receipt.exists():
            reply = read_json(receipt)
            if reply.get('command_hash') != signature:
                raise ValueError('回覆的請求 ID 與內容不符。')
            if not reply.get('pending'):
                return reply
        time.sleep(.1)
    return {'ok': False, 'outcome': 'unknown', 'request_id': key,
            'error': '尚未收到確認。先查詢狀態，或使用同一請求 ID 重送，避免重複下載。'}


def run_client(args):
    try:
        if args.agent == 'status':
            reply = status(task_id=args.task_id)
            code = 0 if reply['connected'] else 2
        else:
            if not args.request_id:
                raise ValueError('變更指令必須提供 --request-id；重送相同操作時請使用同一 ID。')
            command = {'action': args.agent}
            if args.agent == 'start':
                if not args.plan:
                    raise ValueError('start 必須提供 --plan 任務清單.json。')
                command.update(task_id=identifier(args.task_id or args.request_id), spec=read_json(args.plan))
            else:
                if not args.task_id:
                    raise ValueError('請以 --task-id 明確指定任務，避免操作錯誤批次。')
                command['task_id'] = identifier(args.task_id)
                if args.agent == 'continue':
                    if not args.continue_model:
                        raise ValueError('continue 必須提供 --continue-model。')
                    command['model'] = args.continue_model
            reply = send(command, args.request_id, auto_open=args.agent in ('start', 'resume', 'retry'))
            code = 0 if reply.get('ok') else 2
    except Exception as exc:
        reply, code = {'ok': False, 'error': str(exc)}, 2
    atomic_json(args.output, reply)
    if sys.stdout is not None:
        print(json.dumps(reply, ensure_ascii=False))
    return code
