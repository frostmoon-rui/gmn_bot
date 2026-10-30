"""Exercise real process locks without importing or starting the live bot."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bot_instance import InstanceLock


class InstanceTests(unittest.TestCase):
    def test_other_process_blocked_until_owner_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'instance.lock'
            lock = InstanceLock(path)
            self.assertTrue(lock.acquire())
            code = ('from bot_instance import InstanceLock; import sys; '
                    'lock=InstanceLock(sys.argv[1]); print(lock.acquire())')
            def probe():
                return subprocess.check_output([sys.executable, '-c', code, str(path)],
                    cwd=Path(__file__).resolve().parents[1], timeout=10, text=True).strip()
            self.assertEqual(probe(), 'False')
            lock.close()
            self.assertEqual(probe(), 'True')

    def test_lock_is_released_after_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'instance.lock'
            code = ('from bot_instance import InstanceLock; import sys,os; '
                    'lock=InstanceLock(sys.argv[1]); assert lock.acquire(); os._exit(0)')
            subprocess.run([sys.executable, '-c', code, str(path)], check=True,
                           cwd=Path(__file__).resolve().parents[1], timeout=10)
            lock = InstanceLock(path)
            self.assertTrue(lock.acquire())
            lock.close()
