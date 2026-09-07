"""Finish local writes without repeating a successful request to Lieta."""

import os
from pathlib import Path
import time


def _retry_file_access(operation, *, timeout=30, clock=time.monotonic,
                       sleep=time.sleep, on_retry=None):
    """Windows scanners/cloud sync may briefly lock a freshly written file."""
    deadline = clock() + timeout
    delay = 0.25
    notified = False
    while True:
        try:
            return operation()
        except PermissionError:
            remaining = deadline - clock()
            if remaining <= 0:
                raise
            if not notified and on_retry:
                on_retry("檔案暫時被 Windows 鎖定，等待釋放後完成存檔，無須重新獲取模型。")
                notified = True
            sleep(min(delay, remaining))
            delay = min(delay * 2, 2)


def replace_file(source, target, **retry_options):
    return _retry_file_access(lambda: os.replace(source, target), **retry_options)


def read_bytes(path, **retry_options):
    return _retry_file_access(Path(path).read_bytes, **retry_options)
