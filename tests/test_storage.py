import unittest
from unittest.mock import patch

from lieta_automator.storage import read_bytes, replace_file


class StorageTests(unittest.TestCase):
    def test_read_verification_waits_for_lock_after_successful_rename(self):
        now = [0]
        with patch("lieta_automator.storage.Path.read_bytes",
                   side_effect=[PermissionError("scanner"), b"<html>SPY</html>"]) as read:
            result = read_bytes("saved.html", clock=lambda: now[0],
                                sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        self.assertEqual(result, b"<html>SPY</html>")
        self.assertEqual(read.call_count, 2)
        self.assertEqual(now[0], 0.25)

    def test_transient_windows_lock_retries_the_file_operation(self):
        now = [0]
        messages = []
        with patch("lieta_automator.storage.os.replace",
                   side_effect=[PermissionError("locked"), PermissionError("locked"), None]) as rename:
            replace_file("partial", "final", clock=lambda: now[0],
                         sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
                         on_retry=messages.append)
        self.assertEqual(rename.call_count, 3)
        self.assertEqual(now[0], 0.75)
        self.assertEqual(len(messages), 1)

    def test_persistent_permission_failure_has_a_deadline(self):
        now = [0]
        with patch("lieta_automator.storage.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                replace_file("partial", "final", timeout=3, clock=lambda: now[0],
                             sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        self.assertEqual(now[0], 3)
