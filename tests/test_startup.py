from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from lieta_automator.chrome_launcher import prepare_profiles, launch_chrome_in_debug_mode
from lieta_automator.instance_lock import InstanceLock


class StartupTests(unittest.TestCase):
    def test_mutex_rejects_duplicate_and_releases_after_exit(self):
        # Exercise a real mutex without conflicting with the user's running app.
        namespace = patch('lieta_automator.instance_lock.MUTEX_NAME', 'Local\\LietaTest.' + uuid.uuid4().hex)
        namespace.start()
        self.addCleanup(namespace.stop)
        with InstanceLock():
            with self.assertRaises(RuntimeError):
                with InstanceLock():
                    self.fail("Duplicate acquired mutex")
        with InstanceLock():
            pass

    def test_foreign_debug_port_is_not_killed_or_attached(self):
        with patch("lieta_automator.chrome_launcher.is_port_in_use", return_value=True), \
                patch("lieta_automator.chrome_launcher.owns_debug_port", return_value=False), \
                patch("lieta_automator.chrome_launcher.subprocess.Popen") as launch, \
                patch("lieta_automator.chrome_launcher.subprocess.run") as kill:
            self.assertFalse(launch_chrome_in_debug_mode(9222, "profile"))
            launch.assert_not_called()
            kill.assert_not_called()

    def test_incomplete_copy_is_not_marked_synced(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("lieta_automator.config.BASE_DIR", folder), \
                patch("lieta_automator.chrome_launcher.is_port_in_use", return_value=False), \
                patch("lieta_automator.chrome_launcher._copy_tree_ignore_locked", return_value=["Cookies"]):
            (Path(folder) / "automation_profile").mkdir()
            with self.assertRaises(RuntimeError):
                prepare_profiles([9222, 9223])
            target = Path(folder) / "automation_profile_2"
            self.assertTrue((target / ".profile_sync_incomplete").exists())
            self.assertFalse((target / ".profile_synced").exists())

    def test_open_primary_is_never_copied(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("lieta_automator.config.BASE_DIR", folder), \
                patch("lieta_automator.chrome_launcher.is_port_in_use", side_effect=lambda port: port == 9222), \
                patch("lieta_automator.chrome_launcher.owns_debug_port", return_value=True), \
                patch("lieta_automator.chrome_launcher._copy_tree_ignore_locked") as copy:
            (Path(folder) / "automation_profile").mkdir()
            prepare_profiles([9222, 9223])
            copy.assert_not_called()
