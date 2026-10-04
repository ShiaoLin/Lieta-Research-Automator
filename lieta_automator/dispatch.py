"""Fair, process-local request admission. Browser I/O never holds this lock."""
from collections import deque
import threading
import time


class Stopped(Exception):
    pass


class Dispatcher:
    def __init__(self, limit=1, interval=5, clock=time.monotonic):
        if limit not in (1, 2):
            raise ValueError("等待上限僅支援 1 或 2。")
        self.limit, self.interval, self.clock = limit, interval, clock
        self.condition = threading.Condition()
        self.queue = deque()
        self.active = set()
        self.recovering = set()
        self.last_granted = None
        self.submitting = None
        self.next_submit = 0
        self.cooldown_until = 0
        self.failures = 0
        self.stop = threading.Event()

    def request_stop(self):
        self.stop.set()
        with self.condition:
            self.condition.notify_all()

    def check_stop(self):
        if self.stop.is_set():
            raise Stopped("已停止，進度可續跑。")

    def wait(self, seconds):
        if self.stop.wait(max(0, seconds)):
            raise Stopped("已停止，進度可續跑。")

    def _grant(self, owner):
        """Called under condition; separated for deterministic clock tests."""
        if owner not in self.queue:
            self.queue.append(owner)
        # Let an already-ready different model precede a repeat from the last one.
        if len(self.queue) > 1 and self.queue[0] == self.last_granted:
            self.queue.rotate(-1)
        now = self.clock()
        if (self.queue[0] != owner or len(self.active) >= self.limit or self.recovering or self.submitting
                or now < max(self.next_submit, self.cooldown_until)):
            return False
        self.queue.popleft()
        self.active.add(owner)
        self.last_granted = owner
        self.submitting = owner
        return True

    def acquire(self, owner):
        with self.condition:
            try:
                while True:
                    self.check_stop()
                    if self._grant(owner):
                        return
                    self.condition.wait(.1)
            except BaseException:
                if owner in self.queue:
                    self.queue.remove(owner)
                raise

    def submitted(self, owner):
        with self.condition:
            if self.submitting == owner:
                self.next_submit = self.clock() + self.interval
                self.submitting = None
            self.condition.notify_all()

    def release(self, owner):
        with self.condition:
            self.active.discard(owner)
            self.condition.notify_all()

    def fail(self, owner, needs_reset=False):
        with self.condition:
            delay = min(120, 10 * 2 ** min(self.failures, 4))
            self.failures += 1
            self.cooldown_until = max(self.cooldown_until, self.clock() + delay)
            if needs_reset:
                self.recovering.add(owner)
            self.condition.notify_all()
            return delay

    def reset_done(self, owner):
        with self.condition:
            self.recovering.discard(owner)
            self.condition.notify_all()

    def success(self):
        with self.condition:
            self.failures = max(0, self.failures - 1)

    def snapshot(self):
        with self.condition:
            return {"active": len(self.active), "limit": self.limit,
                    "cooldown": max(0, self.cooldown_until - self.clock())}

    def pacing(self):
        with self.condition:
            now = self.clock()
            return {"not_before": time.time() + max(0, self.next_submit - now, self.cooldown_until - now),
                    "failures": self.failures}

    def restore_pacing(self, saved):
        with self.condition:
            remaining = max(0, saved.get('not_before', 0) - time.time())
            self.cooldown_until = max(self.cooldown_until, self.clock() + remaining)
            self.failures = max(self.failures, int(saved.get('failures', 0)))

    def continuation(self):
        """Carry rate limits across completed/stopped batches, with a fresh stop flag."""
        successor = Dispatcher(self.limit, self.interval, self.clock)
        successor.restore_pacing(self.pacing())
        return successor
