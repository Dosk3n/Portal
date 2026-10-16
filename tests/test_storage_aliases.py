"""A second bind mount must not let watchers consume managed report storage."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from safe_files import paths_overlap
from library import Library


class StorageAliasTests(unittest.TestCase):
    def setUp(self):
        self.mounts='''1 0 8:1 / / rw - ext4 /dev/root rw
2 1 0:55 / /mnt/union rw - fuse.mergerfs mergerfs rw
3 1 0:55 /Media/Portal /storage rw - fuse.mergerfs mergerfs rw'''

    def test_managed_aliases_and_ancestors_overlap(self):
        for candidate in ['/mnt/union/Media/Portal/reports','/mnt/union/Media/Portal/reports/sub','/mnt/union/Media/Portal','/mnt/union','/mnt']:
            self.assertTrue(paths_overlap(candidate,'/storage/reports',self.mounts),candidate)
        self.assertFalse(paths_overlap('/mnt/union/Media/Portal/watched','/storage/reports',self.mounts))

    def test_same_path_on_different_filesystems_is_not_an_alias(self):
        self.assertFalse(paths_overlap('/mnt/union/other','/storage/reports',self.mounts))
        self.assertFalse(paths_overlap('/different/reports','/storage/reports',self.mounts))

    def test_mount_paths_with_spaces_are_decoded(self):
        mounts=self.mounts + '\n' + r'4 1 0:55 /Media/Portal /mounted\040portal rw - fuse.mergerfs mergerfs rw'
        self.assertTrue(paths_overlap('/mounted portal/reports','/storage/reports',mounts))

    def test_watcher_registration_checks_mount_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);lib=Library(root/'data',root/'storage');alias=root/'mounted-reports';alias.mkdir()
            mounts=self.mounts+f'\n4 1 0:99 /reports {alias} rw - ext4 none rw\n5 1 0:99 /reports {lib.reports} rw - ext4 none rw'
            with patch('safe_files.Path.read_text',return_value=mounts):
                with self.assertRaisesRegex(ValueError,'overlap'):lib.add_watcher(str(alias))
