"""Shared GUI/CLI batch runner. Each worker exclusively owns its WebDriver."""
from datetime import datetime
import json
from pathlib import Path
import threading
import time
import uuid

from . import config, chrome_launcher
from .dispatch import Dispatcher, Stopped
from .journal import RunJournal
from .logger import logger
from .page_state import read_page_state
from .request_flow import LoginRequired
from .scraper import LietaScraper
from .storage import replace_file

MODELS = ("Gamma", "Term", "Smile", "TV Code")


class Deferred(Exception):
    pass


class BatchRunner:
    def __init__(self, tickers=None, models=None, destination=None, *, multi=True,
                 limit=1, resume=None, response_timeout=90, scraper_factory=LietaScraper):
        self.lock = threading.RLock()
        self.dispatch = Dispatcher(limit)
        self.response_timeout = response_timeout
        self.scraper_factory = scraper_factory
        self.threads = []
        self.resume_events = {model: threading.Event() for model in MODELS}
        self.done = threading.Event()
        self.resume = resume
        if resume:
            original = json.loads(Path(resume).read_text(encoding="utf-8-sig"))
            tickers, destination = original["tickers"], original["destination"]
            models = original.get("models") or [original["model"]]
            multi = original.get("multi", multi)
            if original.get("kind") == "batch":
                self.data = original
                self.path = Path(resume).resolve()
            else:
                self.data = None
        else:
            self.data = None
        tickers = list(dict.fromkeys(t.strip().upper() for t in (tickers or []) if t.strip()))
        if not tickers or any(t in (".", "..") or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.^_-"
                                                     for c in t) for t in tickers):
            raise ValueError("Ticker 清單空白或含有不支援字元。")
        models = list(dict.fromkeys(models or []))
        if not models or any(m not in MODELS for m in models):
            raise ValueError("請選擇有效模型。")
        if not destination or not Path(destination).is_dir():
            raise ValueError("儲存目的地必須存在。")
        self.tickers, self.models, self.destination, self.multi = tickers, models, str(Path(destination).resolve()), multi
        if self.data is None:
            self.path = Path(config.BASE_DIR) / "runs" / f"batch_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json"
            self.data = {"kind": "batch", "version": 1, "tickers": tickers, "models": models,
                         "destination": self.destination, "multi": multi, "journals": {}, "states": {}}
            if resume:
                self.data["journals"][models[0]] = str(Path(resume).resolve())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.journals = {}
        self.deferred = {model: set() for model in models}
        for model in models:
            command = self.path.parent / (self.path.stem + f".continue-{MODELS.index(model)}")
            command.unlink(missing_ok=True)
            old = self.data["journals"].get(model)
            journal = RunJournal(tickers, model, self.destination, old)
            self.journals[model] = journal
            self.data["journals"][model] = str(journal.path.resolve())
            self.data["states"][model] = {"state": "準備中", "ticker": "", "success": 0,
                                         "pending": len(tickers)}
        self.data["limit"] = limit
        self.data["status"] = "prepared"
        self.data.pop("summary", None)
        self.data.setdefault("history", []).append({"started": datetime.now().isoformat(), "limit": limit})
        self.metrics = {"submissions": 0, "try_again": 0, "timeouts": 0, "unauthorized": 0,
                        "abandoned": 0}
        self._save()

    def _save(self):
        with self.lock:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            replace_file(temporary, self.path)

    def state(self, model, status, ticker=""):
        with self.lock:
            completed = sum(item.get("status") == "complete" for item in self.journals[model].data["items"].values())
            self.data["states"][model] = {"state": status, "ticker": ticker, "success": completed,
                                         "pending": len(self.tickers) - completed,
                                         "deferred": len(self.deferred[model])}
            self._save()

    def event(self, model, ticker, name, **details):
        with self.lock:
            if name in self.metrics:
                self.metrics[name] += 1
        logger.info(json.dumps({"batch": str(self.path), "model": model, "ticker": ticker,
                                "event": name, **details}, ensure_ascii=False))

    def snapshot(self):
        with self.lock:
            value = json.loads(json.dumps(self.data["states"]))
        return value, self.dispatch.snapshot()

    def final_summary(self):
        """Revalidate outputs on the worker thread, never during Tk polling."""
        failures, lines, completed = [], [], 0
        for model, journal in self.journals.items():
            missing = []
            for ticker in self.tickers:
                if journal.completed(ticker):
                    completed += 1
                    continue
                item = journal.data['items'].get(ticker, {})
                reason = ('已完成檔案缺失或內容驗證失敗' if item.get('status') == 'complete'
                          else item.get('error') or self.data.get('error') or '尚未完成，等待續跑')
                failures.append({'model': model, 'ticker': ticker, 'reason': reason})
                missing.append(ticker)
            lines.append(f"{model}：{len(self.tickers) - len(missing)}/{len(self.tickers)}" +
                         (f"；未完成：{', '.join(missing)}" if missing else '；全部完成'))
        total = len(self.models) * len(self.tickers)
        heading = f"批次結束：成功 {completed}/{total}，未完成 {len(failures)} 項"
        details = [f"{item['model']} / {item['ticker']}：{item['reason']}" for item in failures]
        text = '\n'.join([heading, *lines, *details, f'續跑紀錄：{self.path}'])
        return {'completed': completed, 'total': total, 'failed': len(failures),
                'failures': failures, 'text': text}

    def request_stop(self):
        self.dispatch.request_stop()
        for event in self.resume_events.values():
            event.set()

    def continue_model(self, model):
        self.resume_events[model].set()

    def _pause_login(self, scraper, model, ticker):
        event = self.resume_events[model]
        event.clear()
        self.state(model, "等待登入", ticker)
        logger.warning(f"[{model}] 請在此視窗登入後按繼續。批次: {self.path}")
        while True:
            self.dispatch.check_stop()
            command = self.path.parent / (self.path.stem + f".continue-{MODELS.index(model)}")
            if command.exists():
                command.unlink()
                event.set()
            if event.wait(.25):
                event.clear()
                self.dispatch.check_stop()
                if scraper.check_login_status():
                    scraper._select_model(model)
                    self.state(model, "執行中", ticker)
                    return
                self.state(model, "等待登入", ticker)

    def _reset(self, scraper, model, ticker, *, timeout=False):
        if timeout:
            self.event(model, ticker, "abandoned", reason="重整不保證伺服器已取消運算")
        try:
            scraper._select_model(model, refresh=True)
            scraper._fill_ticker(ticker)
        finally:
            self.dispatch.reset_done(model)

    def _fetch(self, scraper, model, ticker):
        ordinary, auth_resends, refreshes = 0, 0, 0
        auth_retry = False
        while True:
            self.dispatch.check_stop()
            baseline = read_page_state(scraper.driver, ticker, model)
            if not baseline.authenticated:
                raise LoginRequired("請登入後繼續。")
            if baseline.session_expired:
                if refreshes >= 2:
                    raise LoginRequired("重整兩次仍 Unauthorized。")
                refreshes += 1
                self._reset(scraper, model, ticker)
                continue
            self.state(model, "等待提交", ticker)
            self.dispatch.acquire(model)
            outcome = None
            try:
                self.dispatch.check_stop()
                # A queued window may have changed while waiting for admission.
                baseline = read_page_state(scraper.driver, ticker, model)
                if not baseline.authenticated:
                    raise LoginRequired("請登入後繼續。")
                if baseline.session_expired:
                    outcome = "auth"
                else:
                    if auth_retry:
                        auth_resends += 1
                    else:
                        ordinary += 1
                    scraper._submit()
                    self.dispatch.submitted(model)
                    self.event(model, ticker, "submissions", ordinary=ordinary, auth_resends=auth_resends)
                    self.state(model, "等待結果", ticker)
                    deadline = time.monotonic() + self.response_timeout
                    errors, candidate, since = set(baseline.errors), "", 0
                    while True:
                        self.dispatch.check_stop()
                        current = read_page_state(scraper.driver, ticker, model)
                        if not current.authenticated:
                            raise LoginRequired("請登入後繼續。")
                        if current.session_expired:
                            outcome = "auth"
                            break
                        if current.errors - errors:
                            outcome = "retry"
                            self.event(model, ticker, "try_again")
                            delay = self.dispatch.fail(model, needs_reset=True)
                            self.event(model, ticker, "cooldown", seconds=delay)
                            break
                        now = time.monotonic()
                        fresh = current.ready and current.matches and not current.busy and current.result_key != baseline.result_key
                        if fresh and current.result_key:
                            if candidate != current.result_key:
                                candidate, since = current.result_key, now
                            elif now - since >= 1:
                                self.dispatch.success()
                                return current
                        else:
                            candidate = ""
                        if now >= deadline:
                            outcome = "timeout"
                            self.event(model, ticker, "timeouts")
                            delay = self.dispatch.fail(model, needs_reset=True)
                            self.event(model, ticker, "cooldown", seconds=delay)
                            break
                        self.dispatch.wait(min(.25, max(0, deadline - now)))
            finally:
                # Also unblock admission when an exception occurs before a click.
                self.dispatch.submitted(model)
                self.dispatch.release(model)
            if outcome == "auth":
                self.event(model, ticker, "unauthorized")
                if refreshes >= 2 or auth_resends >= 2:
                    raise LoginRequired("重整兩次仍 Unauthorized。")
                refreshes += 1
                self._reset(scraper, model, ticker)
                auth_retry = True
            else:
                self._reset(scraper, model, ticker, timeout=outcome == "timeout")
                if outcome == "timeout":
                    raise Deferred(f"逾時：{self.response_timeout} 秒未取得有效結果。")
                if ordinary >= 2:
                    raise Deferred("Try Again：本輪兩次一般提交均失敗。")
                auth_retry = False

    def _model(self, scraper, model):
        journal = self.journals[model]
        pending = [t for t in self.tickers if not journal.completed(t)]
        # Correct stale complete statuses before displaying or resuming.
        for ticker in pending:
            journal.record(ticker, error="等待執行")
        self.state(model, "準備中")
        if not pending:
            self.state(model, "完成")
            return
        try:
            if not scraper.check_login_status():
                self._pause_login(scraper, model, pending[0])
            else:
                scraper._select_model(model)
        except LoginRequired:
            self._pause_login(scraper, model, pending[0])
        for pass_number in range(2):
            failed = []
            for ticker in pending:
                while True:
                    self.dispatch.check_stop()
                    try:
                        scraper._fill_ticker(ticker)
                        result = self._fetch(scraper, model, ticker)
                        self.state(model, "存檔中", ticker)
                        if model == "TV Code":
                            path = scraper._save_tv_code(ticker, result.text, self.destination)
                        else:
                            path = scraper._download_html(ticker, model, self.destination, result.result_key)
                        journal.record(ticker, path=path, content=result.text if model == "TV Code" else None)
                        self.deferred[model].discard(ticker)
                        self.event(model, ticker, "complete", path=str(path), pass_number=pass_number + 1)
                        break
                    except LoginRequired as exc:
                        journal.record(ticker, error=str(exc))
                        self._pause_login(scraper, model, ticker)
                    except Stopped:
                        journal.record(ticker, error="已停止，等待續跑")
                        raise
                    except Exception as exc:
                        logger.exception(f"[{model}|{ticker}] 延後補抓: {exc}")
                        journal.record(ticker, error=str(exc))
                        failed.append(ticker)
                        self.deferred[model].add(ticker)
                        if not isinstance(exc, Deferred):
                            try:
                                scraper._select_model(model, refresh=True)
                            except LoginRequired:
                                self._pause_login(scraper, model, ticker)
                        break
                self.state(model, "執行中", ticker)
            pending = failed
            if not pending:
                break
        self.state(model, "完成" if not pending else "完成，仍有失敗")

    def _worker(self, models, port):
        scraper = None
        try:
            self.dispatch.check_stop()
            profile = config.get_chrome_user_data_dir(port)
            if not chrome_launcher.launch_chrome_in_debug_mode(port, profile):
                raise RuntimeError("Chrome 啟動失敗或偵錯埠衝突。")
            if not chrome_launcher.wait_for_chrome(port):
                raise RuntimeError("Chrome 未就緒。")
            scraper = self.scraper_factory(config.get_temp_download_path_for_port(port), port)
            scraper.stop_check = self.dispatch.check_stop
            if not scraper.setup_driver():
                raise RuntimeError("WebDriver 連線失敗。")
            for model in models:
                self._model(scraper, model)
        except Stopped:
            for model in models:
                if self.data["states"][model]["state"] not in ("完成", "完成，仍有失敗"):
                    self.state(model, "已停止")
        except Exception as exc:
            logger.exception("模型視窗停止")
            for model in models:
                if self.data["states"][model]["state"] != "完成":
                    self.state(model, f"錯誤: {exc}")
        finally:
            if scraper:
                scraper.close_driver()

    def run(self):
        started = time.monotonic()
        self.data["status"] = "running"
        self._save()
        logger.info(f"整批續跑紀錄: {self.path}")
        try:
            groups = [[m] for m in self.models] if self.multi else [self.models]
            chrome_launcher.prepare_profiles(config.REMOTE_DEBUGGING_PORTS[:len(groups)])
            for index, models in enumerate(groups):
                thread = threading.Thread(target=self._worker, args=(models, config.REMOTE_DEBUGGING_PORTS[index]))
                self.threads.append(thread)
                thread.start()
            for thread in self.threads:
                thread.join()
            failed = sum(not journal.completed(t) for journal in self.journals.values() for t in self.tickers)
            self.data["status"] = "stopped" if self.dispatch.stop.is_set() else "complete" if failed == 0 else "incomplete"
            self.data["history"][-1].update({"finished": datetime.now().isoformat(),
                                            "elapsed_seconds": time.monotonic() - started,
                                            "failed": failed, **self.metrics})
            self._save()
            return 0 if failed == 0 else 1
        except BaseException as exc:
            self.request_stop()
            for thread in self.threads:
                thread.join()
            self.data["status"] = "stopped" if isinstance(exc, KeyboardInterrupt) else "error"
            self.data["error"] = str(exc)
            self._save()
            if isinstance(exc, KeyboardInterrupt):
                return 1
            logger.exception("批次無法完成")
            return 2
        finally:
            try:
                self.data['summary'] = self.final_summary()
                self._save()
                logger.info(self.data['summary']['text'])
            except Exception:
                logger.exception('無法寫入結束摘要，請核對批次紀錄。')
            finally:
                self.done.set()
