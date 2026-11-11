"""The build host applies the matching repository's target-name rule (tools/names_guard.check) before it compiles or
imports a site submission. A stub guard stands in for the private repository's."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
import host

VA = '0x00401000'
GOOD = '// 0x00401000\nint sub_00401000_Get(){return 0;}\n'
BAD = '// 0x00401000\nint FOO_Invented(){return 0;}\n'

# A stand-in for <repo>/tools/names_guard.py with the same interface: check(va_int, src, names, allowed) -> None | reason.
STUB_GUARD = r'''
import re
LOADS = []
def load_allowed(root=None):
    LOADS.append(root)
    return {}, {"UNITS_GetRoom"}
def target_name(va, src):
    m = re.search(r"//\s*0x%08X\s*\n[^(]*?([A-Za-z_]\w*)\s*\(" % va, src, re.I)
    return (m.group(1), False) if m else (None, False)
def check(va, src, names=None, allowed=None):
    n = target_name(va, src)[0]
    if n is None or n in (allowed or set()) or re.match(r"^sub_%08X(_\w+)?$" % va, n):
        return None
    return "target %s at 0x%08X is not in names.json (e.g. sub_%08X_Invented), then run match.py again" % (n, va, va)
'''


def stub_repo(td, guard=STUB_GUARD):
    root = Path(td)
    (root / 'tools').mkdir(parents=True, exist_ok=True)
    (root / 'config.json').write_text(json.dumps({'rules': {}, 'profile': {'flags': ['/c']}}))
    (root / 'names.json').write_text('{}')
    if guard is not None:
        (root / 'tools' / 'names_guard.py').write_text(guard)
    return host.Repo(td)


class Runner:
    def __init__(self):
        self.compiled = []

    def compile(self, va, length, flags, source):
        self.compiled.append(source)
        return {'status': 'NO_MARKER', 'detail': 'fixture'}


class NameGateTests(unittest.TestCase):
    def run_items(self, repo, items):
        posted, runner = [], Runner()
        with patch.object(host, 'api', side_effect=lambda method, path, body=None: posted.append(body) or {}):
            host.process_items(repo, runner, {VA: 4}, items)
        return posted, runner

    def test_refused_name_fails_with_the_rule_in_words_and_is_never_compiled(self):
        with tempfile.TemporaryDirectory() as td:
            posted, runner = self.run_items(stub_repo(td), [dict(id=1, va=VA, source=BAD, login='a')])
            self.assertEqual(runner.compiled, [])
            self.assertEqual(posted[0]['verdict'], 'failed')
            note = posted[0]['note']
            self.assertTrue(note.startswith('NAME_POLICY:'))
            for words in ('FOO_Invented', 'names.json', 'D2MOO', 'Blizzard', 'sub_<address>', 'sub_<address>_<Verb>',
                          'sub_00401000_Invented', 'submit again'):
                self.assertIn(words, note)
            # the guard's own wording names the private tools; the contributor's agent never sees that
            self.assertNotIn('match.py', note)
            record = json.loads((Path(td) / 'work/host/0x00401000-r1/verdict.json').read_text())
            self.assertEqual(record['host']['status'], 'NAME_POLICY')

    def test_allowed_and_sub_names_go_on_to_compile(self):
        with tempfile.TemporaryDirectory() as td:
            ok = '// 0x00401000\nint __fastcall UNITS_GetRoom(int a){return a;}\n'
            posted, runner = self.run_items(stub_repo(td), [dict(id=1, va=VA, source=GOOD, login='a'),
                                                            dict(id=2, va=VA, source=ok, login='a')])
            self.assertEqual(runner.compiled, [GOOD, ok])
            self.assertTrue(all(not p['note'].startswith('NAME_POLICY') for p in posted))

    def test_guard_is_the_repository_file_loaded_once_and_leaves_the_host_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            repo = stub_repo(td)
            path_before = list(sys.path)
            self.assertIsNotNone(repo.name_check(VA, BAD))
            self.assertIsNone(repo.name_check(VA, GOOD))
            self.assertEqual(repo._guard.LOADS, [repo.root])   # allowed names are loaded from this repository, once
            self.assertEqual(sys.path, path_before)

    def test_repository_without_the_guard_has_no_rule_to_apply(self):
        with tempfile.TemporaryDirectory() as td:
            repo = stub_repo(td, guard=None)
            self.assertIsNone(repo.name_check(VA, BAD))
            posted, runner = self.run_items(repo, [dict(id=1, va=VA, source=BAD, login='a')])
            self.assertEqual(runner.compiled, [BAD])

    def test_a_broken_guard_holds_the_item_for_review_not_a_strike(self):
        with tempfile.TemporaryDirectory() as td:
            repo = stub_repo(td, guard='def load_allowed(root=None):\n    return {}, set()\n'
                                       'def check(va, src, names=None, allowed=None):\n    raise RuntimeError("x")\n')
            posted, runner = self.run_items(repo, [dict(id=1, va=VA, source=BAD, login='a')])
            self.assertEqual(runner.compiled, [])
            self.assertEqual(posted[0]['verdict'], 'review')

    def test_import_refuses_a_refused_name_even_when_verified(self):
        with tempfile.TemporaryDirectory() as td:
            repo = stub_repo(td)
            repo.cfg['rules']['share_objects'] = False
            root = Path(td)
            (root / 'queue/open').mkdir(parents=True)
            q = root / 'queue/open/00401000.json'
            q.write_text(json.dumps(dict(va=VA, length=4, attempts_used=0)))
            wd = root / 'work/host/0x00401000-r1'
            wd.mkdir(parents=True)
            obj = b'object fixture'
            (wd / 'candidate.obj').write_bytes(obj)
            h = hashlib.sha256
            res = dict(status='RAW_EQUAL', compiled_body_sha256='b' * 64, source_sha256=h(BAD.encode()).hexdigest(),
                       object_sha256=h(obj).hexdigest(), symbol='_f', command_argv=['cl.exe', '/c'], compiled_length=4,
                       claimed_length=4, original_body_sha256='a' * 64, relocations=[], cl_sha256='c' * 64)
            record = dict(id=1, va=VA, source=BAD, login='alice', github_id=7, show_name=False, host=res,
                          verdict='verified', delivered=True)
            vp = wd / 'verdict.json'
            vp.write_text(json.dumps(record))
            host.cmd_integrate(repo)
            self.assertTrue(q.exists())
            self.assertFalse((root / 'queue/submitted').exists())
            self.assertNotIn('integrated', json.loads(vp.read_text()))
            # renamed and re-verified: the same import goes through
            res2 = dict(res, source_sha256=h(GOOD.encode()).hexdigest())
            vp.write_text(json.dumps(dict(record, source=GOOD, host=res2)))
            host.cmd_integrate(repo)
            self.assertTrue((root / 'queue/submitted/00401000.iblis-7.json').exists())


if __name__ == '__main__':
    unittest.main()
