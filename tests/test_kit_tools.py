"""Contributor tools in the kit (starter, lookups, history/best, sweep, aligned sxs) and the context index builder.
Synthetic inputs only: no game, no Microsoft compiler."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('kit_under_test', ROOT / 'kit/iblis_check.py')
ck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ck)
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'host'))
import build_context  # noqa: E402

CTX = dict(
    decls={
        '0x00401000': dict(decl='int __fastcall UNITS_GetRoom(D2UnitStrc* pUnit);', conv='__fastcall', sources=9, how='verified definition'),
        '0x00402000': dict(decl='extern int gnCount_00402000;', conv=None, sources=2, how='declaration'),
        '0x00403000': dict(decl='int __thiscall Get(int n);', conv='__thiscall', sources=1, how='member of D2ListStrc', struct='D2ListStrc'),
        '0x00404000': dict(decl='int __fastcall UNITS_GetRoom(D2UnitStrc* pUnit);', conv='__fastcall', sources=1, how='declaration'),
        '0x00405000': dict(decl='void __stdcall Target(D2GameStrc* pGame, CALLBACK pfn);', conv='__stdcall', sources=3, how='declaration'),
    },
    fields={'D2UnitStrc': {'+0x0': [['int', 'dwUnitType', 10]], '+0x14': [['D2MonsterDataStrc*', 'pMonsterData', 5]],
                           '+0x20': [['D2SeedStrc', 'tSeed', 3]], '+0x30': [['char', 'szName[16]', 2]]}},
    sizes={'D2SeedStrc': '0x8'}, typedefs={'BOOL': 'typedef int BOOL;'}, enums={})


class StarterTests(unittest.TestCase):
    def test_known_unknown_member_clash_and_by_value(self):
        calls = {0x00401000: 'a', 0x00403000: 'b', 0x00404000: 'c', 0x00409000: 'sub_00409000'}
        src = ck.starter_source('0x00405000', {}, calls, {0x00402000: 'g'}, CTX, [])
        self.assertIn('int __fastcall UNITS_GetRoom(D2UnitStrc* pUnit); // 0x00401000', src)
        self.assertIn('extern int gnCount_00402000; // 0x00402000', src)
        self.assertIn('struct D2ListStrc\n{\n    int __thiscall Get(int n); // 0x00403000\n};', src)
        self.assertIn('// TODO 0x00404000 is declared as UNITS_GetRoom, a name already used above for 0x00401000', src)
        self.assertIn('// TODO declare sub_00409000 at 0x00409000', src)
        self.assertIn('struct D2UnitStrc;', src)
        self.assertIn('typedef void* CALLBACK; // TODO', src)  # by value in the definition: a forward struct cannot serve
        self.assertIn('// 0x00405000\nvoid __stdcall Target(D2GameStrc* pGame, CALLBACK pfn)\n{', src)
        self.assertNotIn('return 0;', src)  # void target

    def test_unknown_target_gets_a_named_stub_with_the_hinted_convention(self):
        src = ck.starter_source('0x00406000', {'name': 'sub_00406000_Do'}, {}, {}, CTX, ['reads ECX/EDX early: x'])
        self.assertIn('// 0x00406000\nint __fastcall sub_00406000_Do()\n{', src)
        self.assertIn('return 0;', src)

    def test_starter_markers_parse_as_the_checker_reads_them(self):
        src = ck.starter_source('0x00405000', {}, {0x00401000: 'a'}, {0x00402000: 'g'}, CTX, [])
        defs, decls = ck.parse_markers(src)
        self.assertEqual(defs, {'Target': 0x00405000})
        self.assertEqual(decls['UNITS_GetRoom'], 0x00401000)
        self.assertEqual(decls['gnCount_00402000'], 0x00402000)

    def test_decl_name(self):
        self.assertEqual(ck.decl_name('extern "C" __declspec(noreturn) void __cdecl exit(int nCode);'), 'exit')
        self.assertEqual(ck.decl_name('extern char gsz[16];'), 'gsz')


class LayoutTests(unittest.TestCase):
    def test_padding_follows_member_sizes(self):
        out = ck.layout_source(CTX, 'D2UnitStrc', [0x0, 0x14, 0x20, 0x30])
        self.assertIn('struct D2MonsterDataStrc;', out)
        lines = [l.split('//')[0].strip() for l in out.splitlines()]
        self.assertEqual(lines[lines.index('struct D2UnitStrc') + 2:-1], [
            'int dwUnitType;', 'char _pad_0x4[0x10];', 'D2MonsterDataStrc* pMonsterData;', 'char _pad_0x18[0x8];',
            'D2SeedStrc tSeed;', 'char _pad_0x28[0x8];', 'char szName[16];'])

    def test_offset_without_a_verified_member_is_marked(self):
        self.assertIn('_unknown_0x8', ck.layout_source(CTX, 'D2UnitStrc', [0x8]))


class HistoryTests(unittest.TestCase):
    def test_score_orders_match_then_length_then_bytes(self):
        r = lambda **k: dict(dict(status='DIFF', compiled_length=10, length=10, unmasked_diff_bytes=0, target_problems=[]), **k)
        ranked = sorted([r(status='COMPILE_ERROR'), r(compiled_length=12, unmasked_diff_bytes=1), r(unmasked_diff_bytes=5),
                         r(unmasked_diff_bytes=2), r(status='RAW_EQUAL')], key=ck.score)
        self.assertEqual([x['status'] for x in ranked][0], 'RAW_EQUAL')
        self.assertEqual([x.get('unmasked_diff_bytes') for x in ranked[1:4]], [2, 5, 1])
        self.assertEqual(ranked[-1]['status'], 'COMPILE_ERROR')

    def test_best_restore_keeps_unchecked_work(self):
        with tempfile.TemporaryDirectory() as t:
            wd = Path(t, '0x00401000'); (wd / 'history').mkdir(parents=True)
            (wd / 'history/001.cpp').write_text('one'); (wd / 'history/002.cpp').write_text('two')
            hist = [dict(status='DIFF', compiled_length=8, length=8, unmasked_diff_bytes=1, snapshot='history/001.cpp', source_sha256='a'),
                    dict(status='DIFF', compiled_length=8, length=8, unmasked_diff_bytes=6, snapshot='history/002.cpp', source_sha256='b')]
            (wd / 'checks.json').write_text(json.dumps(hist))
            (wd / 'candidate.cpp').write_text('my unchecked edit')
            with mock.patch.object(ck, 'WORK', t):
                ck.cmd_best('0x00401000', restore=True)
            self.assertEqual((wd / 'candidate.cpp').read_text(), 'one')
            kept = [p for p in (wd / 'history').iterdir() if p.name.startswith('unchecked-')]
            self.assertEqual([p.read_text() for p in kept], ['my unchecked edit'])


class SweepTests(unittest.TestCase):
    def test_sweep_ranks_and_groups_identical_bodies_without_touching_candidate(self):
        with tempfile.TemporaryDirectory() as t:
            wd = Path(t, '0x00401000'); (wd / 'variants').mkdir(parents=True)
            for n in 'abc':
                (wd / 'variants' / (n + '.cpp')).write_text(n)
            (wd / 'candidate.cpp').write_text('keep')
            outcomes = {'a': dict(status='DIFF', compiled_length=8, length=8, unmasked_diff_bytes=4, compiled_body_sha256='x'),
                        'b': dict(status='DIFF', compiled_length=8, length=8, unmasked_diff_bytes=1, compiled_body_sha256='y'),
                        'c': dict(status='DIFF', compiled_length=8, length=8, unmasked_diff_bytes=4, compiled_body_sha256='x')}
            with mock.patch.object(ck, 'WORK', t), mock.patch.object(ck, 'settings', return_value={}), \
                    mock.patch.object(ck, 'load_packet', return_value=dict(length=8, flags='/O2')), \
                    mock.patch.object(ck, 'compile_and_compare', side_effect=lambda s, va, n, f, src, wd: outcomes[src]):
                ck.cmd_sweep('0x00401000')
            rows = json.loads((wd / 'variants/SWEEP.json').read_text())
            self.assertEqual([r['variant'] for r in rows], ['b.cpp', 'a.cpp', 'c.cpp'])
            self.assertEqual((wd / 'candidate.cpp').read_text(), 'keep')


class AlignTests(unittest.TestCase):
    def test_one_extra_instruction_does_not_shift_the_rest(self):
        mine = [(0, 1, 'push', 'esi'), (1, 2, 'mov', 'esi, ecx'), (3, 1, 'push', 'edi'), (4, 2, 'xor', 'eax, eax'), (6, 1, 'ret', '')]
        orig = [(0, 1, 'push', 'esi'), (1, 2, 'mov', 'esi, ecx'), (3, 2, 'xor', 'eax, eax'), (5, 1, 'ret', '')]
        body = bytes([0x56, 0x8B, 0xF1, 0x57, 0x33, 0xC0, 0xC3]); ob = bytes([0x56, 0x8B, 0xF1, 0x33, 0xC0, 0xC3])
        rows = ck.align_rows(mine, orig, body, ob, bytes(len(body)))
        self.assertEqual([r[0] for r in rows], [' ', ' ', '<', ' ', ' '])

    def test_register_only_and_relocation_only_differences(self):
        mine = [(0, 2, 'mov', 'eax, ecx'), (2, 5, 'call', '0x10')]
        orig = [(0, 2, 'mov', 'edx, ecx'), (2, 5, 'call', '0x99')]
        body = bytes([0x8B, 0xC1, 0xE8, 1, 2, 3, 4]); ob = bytes([0x8B, 0xD1, 0xE8, 9, 9, 9, 9])
        mask = bytes([0, 0, 0, 1, 1, 1, 1])
        self.assertEqual([r[0] for r in ck.align_rows(mine, orig, body, ob, mask)], ['r', '~'])


class ContextBuilderTests(unittest.TestCase):
    def test_index_prefers_verified_definitions_and_reads_layouts(self):
        with tempfile.TemporaryDirectory() as t:
            m = Path(t, 'queue/matched/00401000'); m.mkdir(parents=True)
            (m / 'candidate.cpp').write_text('''
struct D2UnitStrc
{
    int dwUnitType;                     // +0x00
    char unk0x04[0x10];                 // +0x04
    D2MonsterDataStrc* pMonsterData;    // +0x14
};
typedef int (__stdcall *CALLBACK)(int);
int __stdcall Other(D2UnitStrc* pUnit,
                    int n); // 0x00402000
extern int gnCount; // 0x00403000
// 0x00401000
int __fastcall Target(D2UnitStrc* pUnit)
{
    for (int i = 0; i < 3; i++) { gnCount++; }
    return Other(pUnit, 1);
}
''')
            o = Path(t, 'queue/matched/00402000'); o.mkdir(parents=True)
            (o / 'candidate.cpp').write_text('int __fastcall Target(D2UnitStrc* pUnit, int bogus); // 0x00401000\n')
            idx = build_context.build(t)
        self.assertEqual(idx['decls']['0x00401000']['decl'], 'int __fastcall Target(D2UnitStrc* pUnit);')
        self.assertEqual(idx['decls']['0x00401000']['how'], 'verified definition')
        self.assertEqual(idx['decls']['0x00402000']['decl'], 'int __stdcall Other(D2UnitStrc* pUnit, int n);')
        self.assertEqual(idx['decls']['0x00403000']['decl'], 'extern int gnCount;')
        self.assertEqual(idx['fields']['D2UnitStrc']['+0x14'][0][:2], ['D2MonsterDataStrc*', 'pMonsterData'])
        self.assertNotIn('+0x4', idx['fields']['D2UnitStrc'])  # padding is not a member
        self.assertIn('CALLBACK', idx['typedefs'])
        self.assertFalse([d for d in idx['decls'].values() if 'i++' in d['decl']])


class HostContextTests(unittest.TestCase):
    def test_examples_prefer_shared_callees_and_never_the_function_itself(self):
        import host
        pool = [dict(va='0x00401000', length=40, module='m', source='self', marks={0x00409000}),
                dict(va='0x00402000', length=40, module='m', source='near', marks=set()),
                dict(va='0x00403000', length=90, module='x', source='shares', marks={0x00409000, 0x00403000})]
        ex = host.pick_examples(pool, '0x00401000', 40, 'm', 'int f(); // 0x00409000\n', 2)
        self.assertEqual([e['va'] for e in ex], ['0x00403000', '0x00402000'])
        self.assertIn('shares 1 declared callee', ex[0]['why'])

    def test_sync_batches_stay_under_the_service_limit(self):
        import host
        fs = [dict(va='0x%08X' % i, best_source='x' * 40000) for i in range(100)]
        batches = list(host.sync_batches(fs))
        self.assertEqual(sum(len(b) for b in batches), 100)
        self.assertTrue(all(len(json.dumps({'functions': b})) < 2 * 1024 * 1024 for b in batches))

    def test_host_never_sends_text_the_service_would_reject(self):
        import host
        self.assertTrue(host.service_text_ok('// 0x00401000\nint f() { return 0; }\n'))
        self.assertFalse(host.service_text_ok('int f() { __asm { nop } }'))
        self.assertFalse(host.service_text_ok('mov eax, 1\n' + ''.join('\n00401%03X: mov eax, 1' % i for i in range(12))))


class CompilerEnvironmentTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Wine environment')
    def test_wine_gets_temp_and_prefix_but_never_tmpdir(self):
        seen = {}

        class Done(Exception):
            pass

        def fake_popen(cmd, **kw):
            seen.update(kw['env']); raise Done()
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {'WINEPREFIX': '/tmp/wine', 'TMPDIR': '/x'}), \
                mock.patch.object(ck.subprocess, 'Popen', side_effect=fake_popen):
            with self.assertRaises(Done):
                ck.run_compiler(['/usr/bin/wine', 'cl.exe'], t, 'Z:\\h')
        self.assertNotIn('TMPDIR', seen)  # Wine 9 crashes at start-up with it set
        self.assertEqual((seen['TEMP'], seen['TMP'], seen['WINEPREFIX']), (t, t, '/tmp/wine'))


if __name__ == '__main__':
    unittest.main()
