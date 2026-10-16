"""Filesystem regressions: untrusted inbox writers must not escape submissions."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from library import Library
from watcher import Watcher
from safe_files import open_directory


class WatcherFilesystemSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.library = Library(self.base / 'data', self.base / 'storage')
        self.inbox = self.base / 'inbox'
        self.inbox.mkdir()
        self.library.add_watcher(str(self.inbox))
        self.library.set_setting('watching_enabled', True)
        self.watcher = Watcher(self.library, stable_seconds=0)

    def test_symlink_swap_between_scan_and_read_never_imports_outside_data(self):
        package = self.inbox / 'report'
        package.mkdir()
        (package / 'index.html').write_text('<title>Safe report</title>')
        asset = package / 'asset.txt'
        asset.write_text('normal')
        outside = self.base / 'private.txt'
        outside.write_text('SECRET CANARY')
        self.watcher.scan()
        original_open = os.open
        swapped = False

        def swap_before_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == 'asset.txt' and not swapped:
                asset.unlink()
                asset.symlink_to(outside)
                swapped = True
            return original_open(path, flags, *args, **kwargs)

        with patch('watcher.os.open', side_effect=swap_before_open):
            self.watcher.scan()
        self.assertTrue(swapped)
        self.assertEqual(self.library.search({})['total'], 0)
        self.assertEqual(outside.read_text(), 'SECRET CANARY')
        self.assertTrue(asset.is_symlink())

    def test_folder_swap_after_import_cannot_delete_outside_files(self):
        package = self.inbox / 'report'
        package.mkdir()
        (package / 'index.html').write_text('<title>Safe report</title>')
        outside = self.base / 'private'
        outside.mkdir()
        secret = outside / 'index.html'
        secret.write_text('PRIVATE CANARY')
        original_import = self.library.import_report

        def swap_after_import(*args, **kwargs):
            report = original_import(*args, **kwargs)
            package.rename(self.inbox / 'saved')
            package.symlink_to(outside, target_is_directory=True)
            return report

        self.watcher.scan()
        with patch.object(self.library, 'import_report', side_effect=swap_after_import):
            self.watcher.scan()
        self.assertEqual(secret.read_text(), 'PRIVATE CANARY')
        self.assertEqual(self.library.search({})['total'], 1)
        self.assertIn('changed during import', self.library.settings()['watchers'][0]['error'])

    def test_swapped_watcher_root_is_not_followed(self):
        (self.inbox / 'report.html').write_text('<title>Safe</title>')
        outside = self.base / 'private'
        outside.mkdir()
        secret = outside / 'report.html'
        secret.write_text('<title>PRIVATE CANARY</title>')
        self.watcher.scan()

        def swap_before_open(path):
            self.inbox.rename(self.base / 'saved-inbox')
            self.inbox.symlink_to(outside, target_is_directory=True)
            return open_directory(path)

        with patch('watcher.open_directory', side_effect=swap_before_open):
            self.watcher.scan()
        self.assertEqual(self.library.search({})['total'], 0)
        self.assertTrue(secret.exists())

    def test_fifo_in_bundle_is_rejected_without_blocking(self):
        package = self.inbox / 'report'
        package.mkdir()
        (package / 'index.html').write_text('<title>Safe</title>')
        os.mkfifo(package / 'pipe')
        self.watcher.scan()
        self.watcher.scan()
        self.assertEqual(self.library.search({})['total'], 0)
        self.assertTrue((package / 'pipe').exists())
        self.assertIn('special files', self.library.settings()['watchers'][0]['error'])

    def test_file_growing_during_read_is_bounded_and_retained(self):
        report = self.inbox / 'report.html'
        report.write_text('<h1>Safe</h1>')
        self.watcher.scan()
        original_read = os.read
        changed = False

        def grow_before_read(fd, maximum):
            nonlocal changed
            if not changed:
                report.write_bytes(b'x' * 100)
                changed = True
            return original_read(fd, maximum)

        with patch('watcher.MAX_UPLOAD', 32), patch('watcher.os.read', side_effect=grow_before_read):
            self.watcher.scan()
        self.assertTrue(report.exists())
        self.assertEqual(self.library.search({})['total'], 0)
        self.assertIn('exceeds', self.library.settings()['watchers'][0]['error'])

    def test_bundle_entry_count_is_bounded(self):
        package = self.inbox / 'report'
        package.mkdir()
        (package / 'index.html').write_text('<title>Safe</title>')
        (package / 'extra').write_text('data')
        with patch('watcher.MAX_FILES', 1):
            self.watcher.scan()
        self.assertEqual(self.library.search({})['total'], 0)
        self.assertTrue((package / 'index.html').exists())
        self.assertIn('entries', self.library.settings()['watchers'][0]['error'])
