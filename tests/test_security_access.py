"""Concurrent share access must respect changes made by the owner."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from library import Library


class ShareAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.library = Library(root / 'data', root / 'storage')
        report = self.library.import_report(b'<title>Security test</title>Report', 'report.html')
        self.share = self.library.save_share({'report_id': report['id'], 'password': 'old-password'})

    def concurrent_unlock_and_change(self, change):
        checked = threading.Event()
        release = threading.Event()
        change_started = threading.Event()
        change_done = threading.Event()
        sessions = []
        errors = []
        original = self.library.check_password

        def check_password(password, encoded):
            valid = original(password, encoded)
            checked.set()
            if not release.wait(5):
                raise RuntimeError('Password check was not released')
            return valid

        def unlock():
            try:
                sessions.append(self.library.unlock(self.share['token'], 'old-password'))
            except Exception as exc:
                errors.append(exc)

        def update():
            change_started.set()
            try:
                change()
            except Exception as exc:
                errors.append(exc)
            finally:
                change_done.set()

        with patch.object(self.library, 'check_password', side_effect=check_password):
            reader = threading.Thread(target=unlock, daemon=True)
            writer = threading.Thread(target=update, daemon=True)
            reader.start()
            try:
                self.assertTrue(checked.wait(5))
                writer.start()
                self.assertTrue(change_started.wait(5))
                # Before the fix, the owner change finishes while the old
                # password check is paused, leaving a newly valid old session.
                self.assertFalse(change_done.wait(0.1))
            finally:
                release.set()
                reader.join(5)
                if writer.ident is not None:
                    writer.join(5)
            self.assertFalse(reader.is_alive())
            self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(sessions), 1)
        self.assertTrue(sessions[0])
        self.assertFalse(self.library.unlocked(self.share['token'], sessions[0]))

    def test_password_rotation_invalidates_in_flight_unlock(self):
        self.concurrent_unlock_and_change(
            lambda: self.library.save_share({'password': 'new-password'}, self.share['token']))
        self.assertIsNone(self.library.unlock(self.share['token'], 'old-password'))
        session = self.library.unlock(self.share['token'], 'new-password')
        self.assertTrue(self.library.unlocked(self.share['token'], session))

    def test_revocation_removes_in_flight_unlock_session(self):
        self.concurrent_unlock_and_change(lambda: self.library.revoke(self.share['token']))
        with self.library.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM share_sessions').fetchone()[0], 0)
        with self.assertRaises(LookupError):
            self.library.share(self.share['token'])
