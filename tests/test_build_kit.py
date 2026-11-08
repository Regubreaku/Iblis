import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import build_kit


class BuildKitTests(unittest.TestCase):
    def test_release_contains_only_contributor_materials_and_matching_digest(self):
        self.assertTrue(hasattr(build_kit, 'package_kit'), 'Standalone contributor packaging is missing')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kit = root / 'kit'
            allowed = ['README.md', 'CONVENTIONS.md', 'MATCHING.md', 'iblis_check.py', 'kit.json', 'names.json',
                       'headers/README.md', 'headers/types.h', 'selftest/candidate.cpp']
            private = ['.env', 'HOST_KEY.txt', 'work/candidate.cpp', 'toolchain/bin/cl.exe',
                       'headers/OLD_WORKFLOW.md', 'selftest/private-notes.md', '__pycache__/checker.pyc']
            for name in allowed + private:
                target = kit / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(name, encoding='utf-8')
            release = Path(build_kit.package_kit(kit, root / 'release'))
            with zipfile.ZipFile(release) as archive:
                self.assertEqual(set(archive.namelist()), {'iblis-check/' + name for name in allowed})
            metadata = json.loads((release.parent / 'latest.json').read_text())
            self.assertEqual(metadata['file'], release.name)
            self.assertEqual(metadata['bytes'], release.stat().st_size)
            self.assertEqual(metadata['sha256'], hashlib.sha256(release.read_bytes()).hexdigest())
            self.assertEqual(release.with_suffix('.zip.sha256').read_text().split()[0], metadata['sha256'])

    def test_missing_checker_fails_before_publishing_a_release(self):
        self.assertTrue(hasattr(build_kit, 'package_kit'), 'Standalone contributor packaging is missing')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kit = root / 'kit'
            kit.mkdir()
            with self.assertRaises(FileNotFoundError):
                build_kit.package_kit(kit, root / 'release')
            self.assertFalse((root / 'release' / 'latest.json').exists())


if __name__ == '__main__':
    unittest.main()
