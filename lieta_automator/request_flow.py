"""Bounded, state-driven retries, independent of Selenium and wall-clock time."""

from dataclasses import dataclass
from contextlib import contextmanager
import threading
import time


@dataclass(frozen=True)
class RetryPolicy:
    minimum_interval: float = 5
    initial_backoff: float = 10
    maximum_backoff: float = 120
    response_timeout: float = 90
    ticker_timeout: float = 600
    max_attempts: int = 6
    max_session_refreshes: int = 2
    poll_interval: float = 0.5
    stable_for: float = 1


@dataclass(frozen=True)
class PageState:
    result_key: str = ""
    text: str = ""
    matches: bool = False
    ready: bool = False
    busy: bool = False
    errors: frozenset = frozenset()
    authenticated: bool = True
    session_expired: bool = False


class ModelTimeout(TimeoutError):
    pass


class LoginRequired(RuntimeError):
    pass


class RequestCoordinator:
    """All browser windows in this process share one request slot and cooldown."""

    def __init__(self, policy=None, clock=time.monotonic):
        self.policy = policy or RetryPolicy()
        self.clock = clock
        self.lock = threading.RLock()
        self.next_allowed = 0
        self.failures = 0
        self.login_error = None

    def require_login(self, message):
        # Set while holding the request lock, before another window can submit.
        self.login_error = str(message)
        raise LoginRequired(self.login_error)

    def check_login(self):
        if self.login_error:
            raise LoginRequired(self.login_error)

    @contextmanager
    def request_slot(self):
        with self.lock:
            self.check_login()
            try:
                yield
            except LoginRequired as exc:
                self.login_error = str(exc)
                raise

    def sent(self):
        self.next_allowed = max(self.next_allowed, self.clock() + self.policy.minimum_interval)

    def failed(self):
        delay = min(self.policy.maximum_backoff,
                    self.policy.initial_backoff * 2 ** min(self.failures, 16))
        self.failures += 1
        self.next_allowed = max(self.next_allowed, self.clock() + delay)
        return delay

    def succeeded(self):
        # Recover gradually after an outage, including between different tickers.
        self.failures = max(0, self.failures - 1)
        self.next_allowed = max(self.next_allowed, self.clock() + self.policy.minimum_interval)


def fetch_result(observe, submit, coordinator, log, *, policy=None,
                 clock=time.monotonic, sleep=time.sleep, recover_session=None):
    """Caller holds coordinator.lock through submission, verification and saving.

    A timeout does not cause a second request while the UI still reports busy.
    During backoff, keep observing so a late result can finish without a retry.
    Error DOM identities distinguish a new toast from one left by an older attempt.
    """
    policy = policy or coordinator.policy
    deadline = clock() + policy.ticker_timeout
    attempts = 0
    session_refreshes = 0
    baseline = None
    response_deadline = 0
    retry_pending = True
    candidate = ""
    candidate_since = 0
    seen_errors = set()
    last_reason = "未取得本次 ticker 的新結果"

    while clock() < deadline:
        coordinator.check_login()
        state = observe()
        now = clock()
        if not state.authenticated:
            coordinator.require_login("已回到登入頁，批次停止。請重新登入 Lieta 後續跑。")
        if state.session_expired:
            if recover_session is None or session_refreshes >= policy.max_session_refreshes:
                coordinator.require_login("重新整理後仍收到 Unauthorized，批次停止。請確認 Lieta 登入後續跑。")
            if session_refreshes and now < coordinator.next_allowed:
                sleep(min(policy.poll_interval, max(0, deadline - clock())))
                continue
            session_refreshes += 1
            log(f"偵測到 Unauthorized，重新整理頁面並還原本項模型與 ticker "
                f"({session_refreshes}/{policy.max_session_refreshes})。")
            # Do not reuse the old chart, toast identities or busy state after navigation.
            # The original request count, overall deadline and shared cooldown survive.
            baseline, candidate, seen_errors = None, "", set()
            retry_pending = True
            try:
                recover_session()
            except LoginRequired as exc:
                coordinator.require_login(str(exc))
            coordinator.sent()  # Pace requests (and another refresh) after navigation.
            sleep(min(policy.poll_interval, max(0, deadline - clock())))
            continue

        if baseline is not None:
            new_errors = state.errors - seen_errors
            seen_errors.update(state.errors)
            if new_errors and not retry_pending:
                last_reason = "網站顯示暫時失敗提示"
                delay = coordinator.failed()
                log(f"{last_reason}，至少冷卻 {delay:g} 秒，期間持續檢查結果。")
                retry_pending = True
                candidate = ""

            fresh = (state.ready and state.matches and not state.busy
                     and state.result_key and state.result_key != baseline.result_key)
            if fresh and not new_errors:
                if candidate != state.result_key:
                    candidate, candidate_since = state.result_key, now
                elif now - candidate_since >= policy.stable_for:
                    coordinator.succeeded()
                    return state
            else:
                candidate = ""

            if not retry_pending and now >= response_deadline and not state.busy:
                last_reason = "等待結果逾時"
                delay = coordinator.failed()
                log(f"{last_reason}，至少冷卻 {delay:g} 秒後再試。")
                retry_pending = True

        if retry_pending and now >= coordinator.next_allowed and not state.busy and not candidate:
            if attempts >= policy.max_attempts:
                break
            baseline = state
            seen_errors = set(state.errors)
            candidate = ""
            submit()
            attempts += 1
            coordinator.sent()
            response_deadline = clock() + policy.response_timeout
            retry_pending = False
            log(f"已提交第 {attempts}/{policy.max_attempts} 次請求，等待本次結果。")

        sleep(min(policy.poll_interval, max(0, deadline - clock())))

    # Preserve an outage cooldown even if the deadline expired with an active request.
    if not retry_pending:
        coordinator.failed()
    raise ModelTimeout(f"{last_reason}；已提交 {attempts} 次，達到次數或時間上限。")


SHARED_COORDINATOR = RequestCoordinator()
