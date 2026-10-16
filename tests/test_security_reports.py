"""Regression checks for report package boundaries using synthetic files only."""
import os
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from library import Library
from packages import validate_package
from safe_files import copy_tree, iter_files, open_regular


class ReportFilesSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = Library(self.root / 'data', self.root / 'storage')
        self.report = self.library.import_report(b'<title>Safe example</title><p>Example</p>', 'example.html')
        self.package = self.library.reports / self.report['id']
        self.outside = self.root / 'private.txt'
        self.outside.write_text('synthetic private marker')

    def test_snapshot_rejects_unreferenced_symlink(self):
        (self.package / 'extra.txt').symlink_to(self.outside)
        with self.assertRaises((OSError, ValueError)):
            self.library.save_share({'report_id': self.report['id']})
        self.assertEqual(self.library.list_shares(), [])
        self.assertFalse(any(p.name == 'extra.txt' for p in self.library.shares.rglob('*')))

    def test_failed_replacement_preserves_existing_snapshot(self):
        share = self.library.save_share({'report_id': self.report['id']})
        second = self.library.import_report(b'<title>Second</title><p>Second</p>', 'second.html')
        (self.library.reports / second['id'] / 'extra.txt').symlink_to(self.outside)
        before = self.library.share(share['token'])
        with self.assertRaises((OSError, ValueError)):
            self.library.save_share({'report_id': second['id']}, share['token'])
        self.assertEqual(self.library.share(share['token']), before)
        self.assertTrue((self.library.shares / share['token'] / before['version'] / 'index.html').is_file())

    def test_validation_rejects_symlink_and_special_file(self):
        linked = self.package / 'extra.txt'
        linked.symlink_to(self.outside)
        with self.assertRaises(ValueError):
            validate_package(self.package, 'index.html')
        linked.unlink()
        os.mkfifo(linked)
        with self.assertRaises(ValueError):
            validate_package(self.package, 'index.html')

    def test_file_open_rejects_ancestor_and_final_symlinks(self):
        (self.package / 'linked.txt').symlink_to(self.outside)
        (self.root / 'linked-package').symlink_to(self.package, target_is_directory=True)
        for root, relative in [(self.package, 'linked.txt'), (self.root / 'linked-package', 'index.html')]:
            with self.subTest(root=root, relative=relative), self.assertRaises(OSError):
                with open_regular(root, relative):
                    self.fail('Symlink was opened')
        with self.assertRaises(ValueError):
            with open_regular(self.package, '../private.txt'):
                self.fail('Traversal was opened')

    def test_open_root_remains_pinned_after_rename(self):
        with open_regular(self.package, 'index.html') as stream:
            original = stream.read()
            moved = self.package.with_name('moved-package')
            self.package.rename(moved)
            self.package.symlink_to(self.root, target_is_directory=True)
            stream.seek(0)
            self.assertEqual(stream.read(), original)

    def test_nested_copy_preserves_assets(self):
        nested = self.package / 'assets'
        nested.mkdir()
        (nested / 'style.css').write_text('body { color: green; }')
        target = self.root / 'copied'
        copy_tree(self.package, target)
        self.assertEqual((target / 'assets/style.css').read_text(), 'body { color: green; }')

    def test_directory_index_is_opened_without_following_links(self):
        nested = self.package / 'nested'
        nested.mkdir()
        (nested / 'index.html').write_text('nested index')
        for relative in ('nested', 'nested/'):
            with open_regular(self.package, relative, index=True) as stream:
                self.assertEqual(stream.read(), b'nested index')
        (nested / 'index.html').unlink()
        (nested / 'index.html').symlink_to(self.outside)
        with self.assertRaises(OSError):
            with open_regular(self.package, 'nested/', index=True):
                self.fail('Directory index symlink was opened')
        with open_regular(self.package, '', index=True) as stream:
            self.assertIn(b'Safe example', stream.read())

    def test_copy_rejects_file_swapped_to_symlink_after_inspection(self):
        (self.package / 'asset.txt').write_text('ordinary asset')
        original_stat = os.stat
        swapped = False
        def inspect_then_swap(name, *args, **kwargs):
            nonlocal swapped
            result = original_stat(name, *args, **kwargs)
            if name == 'asset.txt' and kwargs.get('dir_fd') is not None and not swapped:
                swapped = True
                descriptor = kwargs['dir_fd']
                os.unlink(name, dir_fd=descriptor)
                os.symlink(self.outside, name, dir_fd=descriptor)
            return result
        with patch('safe_files.os.stat', side_effect=inspect_then_swap):
            with self.assertRaises(OSError):
                copy_tree(self.package, self.root / 'raced-copy')
        self.assertTrue(swapped)
        self.assertFalse((self.root / 'raced-copy' / 'asset.txt').exists())

    def test_package_size_entry_and_depth_limits(self):
        nested = self.package / 'nested'
        nested.mkdir()
        (nested / 'deeper').mkdir()
        (nested / 'deeper' / 'asset.txt').write_text('asset')
        for options in ({'maximum': 1}, {'max_files': 1}, {'max_depth': 1}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    iter_files(self.package, **options)
                target = self.root / ('bounded-' + next(iter(options)))
                with self.assertRaises(ValueError):
                    copy_tree(self.package, target, **options)

    def test_copy_rejects_file_growing_after_size_check(self):
        asset = self.package / 'asset.txt'
        asset.write_text('small')
        @contextmanager
        def growing_open(root, relative, **kwargs):
            with open_regular(root, relative, **kwargs) as stream:
                if relative != 'asset.txt':
                    yield stream
                    return
                class GrowingReader:
                    def fileno(self):
                        return stream.fileno()
                    def read(self, size):
                        with asset.open('ab') as output:
                            output.write(b'x' * 128)
                        return stream.read(size)
                yield GrowingReader()
        limit = sum(p.stat().st_size for p in self.package.iterdir()) + 10
        with patch('safe_files.open_regular', side_effect=growing_open):
            with self.assertRaises(ValueError):
                copy_tree(self.package, self.root / 'growing-copy', maximum=limit)
        self.assertLessEqual(sum(p.stat().st_size for p in (self.root / 'growing-copy').iterdir()), limit)

    def test_successful_package_copy_syncs_files_and_directories(self):
        original_fsync = os.fsync
        synced = []
        def record_sync(descriptor):
            synced.append(os.fstat(descriptor).st_mode)
            return original_fsync(descriptor)
        with patch('safe_files.os.fsync', side_effect=record_sync):
            copy_tree(self.package, self.root / 'durable-copy')
        import stat
        self.assertTrue(any(stat.S_ISREG(mode) for mode in synced))
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in synced))
