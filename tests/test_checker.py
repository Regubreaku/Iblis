"""Synthetic PE/COFF regression tests; no game or Microsoft compiler is used."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('checker_under_test', ROOT / 'kit/iblis_check.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
MATCH = {'RAW_EQUAL', 'RELOCATION_RESOLVED_EQUAL'}


def coff(body, relocs=(), symbol='_wrong', scn=0, value=0, extra=None, extra_symbols=()):
    """One target section, one dependency symbol; explicit i386 COFF records."""
    sections = [(b'.text', body, relocs)] + ([extra] if extra else [])
    cursor = 20 + 40 * len(sections)
    headers, payload = [], b''
    for name, raw, rels in sections:
        rp = cursor
        payload += raw
        cursor += len(raw)
        relocptr = cursor
        records = b''.join(struct.pack('<IIH', off, 1, ty) for off, ty in rels)
        payload += records
        cursor += len(records)
        headers.append(struct.pack('<8sIIIIIIHHI', name, 0, 0, len(raw), rp, relocptr, 0, len(rels), 0, 0x60000020))
    strings = b''
    symbols = b''
    symbol_specs = [('_f', 0, 1), (symbol, value, scn)] + list(extra_symbols)
    for name, val, section in symbol_specs:
        encoded = name.encode('ascii')
        if len(encoded) > 8:
            nm = struct.pack('<II', 0, 4 + len(strings))
            strings += encoded + b'\0'
        else:
            nm = encoded.ljust(8, b'\0')
        symbols += nm + struct.pack('<IhHBB', val, section, 0x20 if name == '_f' else 0, 2, 0)
    return struct.pack('<HHIIIHH', 0x14c, len(sections), 0, cursor, len(symbol_specs), 0, 0) + b''.join(headers) + payload + symbols + struct.pack('<I', 4 + len(strings)) + strings


def pe(body):
    image = bytearray(0x200)
    image[:2] = b'MZ'
    struct.pack_into('<I', image, 0x3c, 0x80)
    image[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', image, 0x84, 0x14c, 1, 0, 0, 0, 224, 0)
    struct.pack_into('<H', image, 0x98, 0x10b)
    struct.pack_into('<I', image, 0x98 + 28, 0x400000)
    struct.pack_into('<8sIIIIIIHHI', image, 0x98 + 224, b'.text', len(body), 0x1000, len(body), 0x200, 0, 0, 0, 0, 0x60000020)
    return bytes(image) + body


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'image.exe'
        self.fixture = self.root / 'fixture.obj'
        self.script = self.root / 'compiler.py'
        self.script.write_text("import pathlib,sys\npathlib.Path('candidate.obj').write_bytes(pathlib.Path(sys.argv[1]).read_bytes())\n", encoding='utf-8')
        self.settings = dict(cl=sys.executable, runner='native', image=str(self.image))

    def check(self, body=b'\xe8\0\0\0\0\xc3', original=b'\xe8\xfb\x0f\0\0\xc3', decl='', relocs=((1, 0x14),), **kw):
        self.fixture.write_bytes(coff(body, relocs, **kw))
        self.image.write_bytes(pe(original))
        src = decl + '\n// 0x00401000\nvoid f() { wrong(); }\n'
        return checker.compile_and_compare(self.settings, '0x00401000', len(original), [str(self.script), str(self.fixture)], src, str(self.root / 'work'))

    def assert_rejected(self, result):
        self.assertNotIn(result['status'], MATCH, result)
        self.assertTrue(result.get('target_problems') or result.get('detail'), result)

    def test_unmarked_wrong_callee_cannot_match(self):
        self.assert_rejected(self.check())

    def test_c_identifier_leading_underscore_is_not_stripped_away(self):
        self.assert_rejected(self.check(symbol='__wrong', decl='extern void wrong(); // 0x00402000'))
        self.assertEqual(self.check(symbol='__wrong', decl='extern void _wrong(); // 0x00402000')['status'], 'RELOCATION_RESOLVED_EQUAL')

    def test_scoped_cpp_symbol_does_not_borrow_an_unqualified_marker(self):
        self.assert_rejected(self.check(symbol='?wrong@Other@@YAXXZ', decl='extern void wrong(); // 0x00402000'))

    def test_import_symbol_resolves_to_its_dllimport_declaration(self):
        decl = 'extern "C" __declspec(dllimport) void __stdcall wrong(int n); // 0x00402000'
        self.assertEqual(self.check(symbol='__imp__wrong@4', decl=decl)['status'], 'RELOCATION_RESOLVED_EQUAL')

    def test_import_symbol_needs_its_own_marker(self):
        self.assert_rejected(self.check(symbol='__imp__wrong@4', decl='extern void other(); // 0x00402000'))

    def test_member_symbol_resolves_to_the_marker_inside_its_struct(self):
        decl = 'struct Other\n{\n    void wrong(); // 0x00402000\n};'
        self.assertEqual(self.check(symbol='?wrong@Other@@QAEXXZ', decl=decl)['status'], 'RELOCATION_RESOLVED_EQUAL')

    def test_member_symbol_does_not_borrow_a_marker_from_another_struct(self):
        decl = 'struct Elsewhere\n{\n    void wrong(); // 0x00402000\n};'
        self.assert_rejected(self.check(symbol='?wrong@Other@@QAEXXZ', decl=decl))

    def test_dllimport_declaration_is_named_by_its_function_not_declspec(self):
        defs, decls = checker.parse_markers('extern "C" __declspec(dllimport) void __stdcall Enter(void* p); // 0x006CC218')
        self.assertEqual(decls, {'Enter': 0x006CC218})

    def helper_check(self, marker):
        # the call goes to a helper defined in this object (its own .text section), as a `static` function would
        extra = (b'.text$h', b'\xc3', ())
        decl = (('// %s\n' % marker) if marker else '') + 'static void wrong() {}'
        return self.check(decl=decl, scn=2, extra=extra)

    def test_call_into_a_marked_helper_in_the_same_file_resolves(self):
        self.assertEqual(self.helper_check('0x00402000')['status'], 'RELOCATION_RESOLVED_EQUAL')

    def test_helper_marked_at_another_address_is_rejected(self):
        self.assert_rejected(self.helper_check('0x00403000'))

    def test_unmarked_helper_is_still_an_unverified_dependency(self):
        self.assert_rejected(self.helper_check(None))

    def literal_check(self, literal):
        # mov eax, offset "ab" ; ret  -- the literal sits right after the function in the original image
        body = b'\xb8\0\0\0\0\xc3'
        original = b'\xb8\x06\x10\x40\x00\xc3' + b'ab\0'
        name = '??_C@_02ABCD@ab?$AA@'
        self.fixture.write_bytes(coff(body, ((1, 6),), symbol=name, scn=2, extra=(b'.rdata', literal, ())))
        self.image.write_bytes(pe(original))
        src = '\n// 0x00401000\nconst char* f() { return "ab"; }\n'
        return checker.compile_and_compare(self.settings, '0x00401000', 6, [str(self.script), str(self.fixture)], src, str(self.root / 'work'))

    def test_string_literal_equal_to_the_original_data_resolves(self):
        self.assertEqual(self.literal_check(b'ab\0')['status'], 'RELOCATION_RESOLVED_EQUAL')

    def test_string_literal_with_other_content_is_rejected(self):
        self.assert_rejected(self.literal_check(b'xy\0'))

    def test_explicit_simple_global_cpp_symbol_is_supported(self):
        self.assertEqual(self.check(symbol='?wrong@@YAXXZ', decl='extern void wrong(); // 0x00402000')['status'], 'RELOCATION_RESOLVED_EQUAL')

    def test_cpp_overloads_cannot_share_an_ambiguous_marker(self):
        self.assert_rejected(self.check(symbol='?wrong@@YAXXZ', decl='extern void wrong(); // 0x00402000', extra_symbols=[('?wrong@@YAXH@Z', 0, 0)]))

    def test_conflicting_markers_do_not_silently_use_the_last_one(self):
        self.assert_rejected(self.check(decl='extern void wrong(); // 0x00500000\nextern void wrong(); // 0x00402000'))

    def test_raw_equal_cannot_bypass_conflicting_marker(self):
        self.assert_rejected(self.check(original=b'\xe8\0\0\0\0\xc3', decl='extern void wrong(); // 0x00700000'))

    def test_explicit_external_call_matches_when_target_is_correct(self):
        self.assertEqual(self.check(decl='extern void wrong(); // 0x00402000')['status'], 'RELOCATION_RESOLVED_EQUAL')

    def test_rel32_nonzero_addend_is_checked(self):
        self.assertEqual(self.check(body=b'\xe8\x04\0\0\0\xc3', original=b'\xe8\xff\x0f\0\0\xc3', decl='extern void wrong(); // 0x00402000')['status'], 'RELOCATION_RESOLVED_EQUAL')
        self.assert_rejected(self.check(body=b'\xe8\x04\0\0\0\xc3', decl='extern void wrong(); // 0x00402000'))

    def test_absolute_external_addend_is_checked(self):
        args = dict(body=b'\xa1\x04\0\0\0\xc3', original=b'\xa1\x04\x20\x40\0\xc3', decl='extern int wrong; // 0x00402000', relocs=((1, 6),))
        self.assertEqual(self.check(**args)['status'], 'RELOCATION_RESOLVED_EQUAL')
        args['original'] = b'\xa1\x08\x20\x40\0\xc3'
        self.assert_rejected(self.check(**args))

    def test_unsupported_relocation_never_matches_even_raw_equal(self):
        self.assert_rejected(self.check(original=b'\xe8\0\0\0\0\xc3', relocs=((1, 7),)))

    def test_internal_data_is_not_proven_by_code_or_marker(self):
        self.assert_rejected(self.check(body=b'\xa1\0\0\0\0\xc3', original=b'\xa1\0\x20\x40\0\xc3', decl='extern int wrong; // 0x00402000', relocs=((1, 6),), scn=2, extra=(b'.rdata', b'WRONG', ())))

    def test_common_symbol_is_not_external_declaration(self):
        self.assert_rejected(self.check(decl='extern void wrong(); // 0x00402000', value=4))

    def test_same_section_label_resolves_to_its_actual_offset(self):
        self.assertEqual(self.check(original=b'\xe8\0\0\0\0\xc3', scn=1, value=5, symbol='$label')['status'], 'RAW_EQUAL')

    def test_same_section_label_cannot_ignore_conflicting_markers(self):
        self.assert_rejected(self.check(original=b'\xe8\0\0\0\0\xc3', scn=1, value=5,
            decl='extern void wrong(); // 0x00401005\nextern void wrong(); // 0x00700000'))

    def test_same_section_addend_cannot_escape_verified_body(self):
        self.assert_rejected(self.check(body=b'\xe8\0\x10\0\0\xc3', original=b'\xe8\0\x10\0\0\xc3', scn=1, value=5, symbol='$label'))

    def test_result_contains_complete_relocation_evidence_for_host(self):
        result = self.check(decl='extern void wrong(); // 0x00402000')
        self.assertEqual(result['claimed_length'], 6)
        self.assertEqual(result.get('command_argv'), [sys.executable, str(self.script), str(self.fixture), '/Focandidate.obj', 'candidate.cpp'])
        self.assertIs(result.get('extent_agrees'), True)
        self.assertEqual(result.get('diff_bytes'), 2)
        self.assertEqual(result['relocation_records'], [dict(offset=1, symbol='_wrong', type='REL32',
                         inplace=0, declared=0x402000, original_target=0x402000)])
        import hashlib
        self.assertEqual(result['object_sha256'], hashlib.sha256(self.fixture.read_bytes()).hexdigest())
        self.assertEqual(result['original_body_sha256'], hashlib.sha256(b'\xe8\xfb\x0f\0\0\xc3').hexdigest())

    def test_size_difference_is_recorded_in_verifier_metadata(self):
        result = self.check(body=b'\xc3', original=b'\xc3\x90', relocs=())
        self.assertIs(result.get('extent_agrees'), False)
        self.assertEqual(result.get('diff_bytes'), 0)  # overlap is identical; extent records the missing byte

    def test_selftest_surfaces_unresolved_runtime_reason(self):
        import contextlib
        import io
        output = io.StringIO()
        with mock.patch.object(checker, 'settings', return_value=self.settings), mock.patch.object(checker, 'compile_and_compare', return_value=dict(status='DIFF', target_problems=['unresolved relocation target __aulldiv'])):
            with contextlib.redirect_stderr(output), self.assertRaises(SystemExit):
                checker.cmd_selftest()
        self.assertIn('__aulldiv', output.getvalue())

    def test_relocation_overrun_is_reported_without_exception(self):
        self.assert_rejected(self.check(relocs=((4, 0x14),)))

    def test_truncated_original_cannot_match(self):
        self.assert_rejected(self.check(body=b'\xc3', original=b'', relocs=()))

    def test_plain_exact_body_still_matches(self):
        self.assertEqual(self.check(body=b'\xc3', original=b'\xc3', relocs=())['status'], 'RAW_EQUAL')

    def test_unicode_is_a_predictable_source_error(self):
        wd = self.root / 'unicode'
        result = checker.compile_and_compare(self.settings, '0x00401000', 1, [], '// 0x00401000\nvoid f() {} // 中', str(wd))
        self.assertEqual(result['status'], 'SOURCE_ERROR')
        self.assertTrue(wd.is_dir())

    def test_missing_marker_still_creates_workdir(self):
        wd = self.root / 'missing'
        result = checker.compile_and_compare(self.settings, '0x00401000', 1, [], 'int x;', str(wd))
        self.assertEqual(result['status'], 'NO_MARKER')
        self.assertTrue(wd.is_dir())

    def test_compiler_cannot_read_inherited_secret_or_cl_options(self):
        self.script.write_text("import os,sys,pathlib\nif any(k in os.environ for k in ('IBLIS_TEST_SECRET','CL','_CL_','PYTHONPATH')): sys.exit(17)\npathlib.Path('candidate.obj').write_bytes(pathlib.Path(sys.argv[1]).read_bytes())\n")
        with mock.patch.dict(os.environ, {'IBLIS_TEST_SECRET': 'private', 'CL': '/bad', '_CL_': '/bad', 'PYTHONPATH': str(self.root)}):
            self.assertEqual(self.check(body=b'\xc3', original=b'\xc3', relocs=())['status'], 'RAW_EQUAL')

    def test_compiler_timeout_has_distinct_status(self):
        self.script.write_text('import time\ntime.sleep(2)\n')
        with mock.patch.object(checker, 'COMPILE_TIMEOUT_SECONDS', 0.1, create=True):
            self.assertEqual(self.check()['status'], 'COMPILE_TIMEOUT')

    def compiler_with_child(self, overflow=False):
        started, sentinel = self.root / 'child-started', self.root / 'child-finished'
        child = "import pathlib,time; pathlib.Path(%r).touch(); time.sleep(1.0); pathlib.Path(%r).touch()" % (str(started), str(sentinel))
        parent = "import subprocess,sys,time,pathlib\nsubprocess.Popen([sys.executable,'-c',%r],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n" % child
        parent += "while not pathlib.Path(%r).exists(): time.sleep(.01)\n" % str(started)
        if overflow:
            parent += "sys.stdout.write('x'*200000); sys.stdout.flush()\n"
        parent += 'time.sleep(3)\n'
        self.script.write_text(parent)
        return started, sentinel

    def test_compiler_timeout_stops_child_process(self):
        started, sentinel = self.compiler_with_child()
        with mock.patch.object(checker, 'COMPILE_TIMEOUT_SECONDS', 0.4):
            self.assertEqual(self.check()['status'], 'COMPILE_TIMEOUT')
        self.assertTrue(started.exists(), 'fixture must actually start the child')
        time.sleep(1.1)
        self.assertFalse(sentinel.exists(), 'compiler child survived timeout')

    def test_compiler_output_limit_stops_child_process(self):
        started, sentinel = self.compiler_with_child(overflow=True)
        with mock.patch.object(checker, 'COMPILE_OUTPUT_BYTES', 4096):
            self.assertEqual(self.check()['status'], 'COMPILE_OUTPUT_LIMIT')
        self.assertTrue(started.exists(), 'fixture must actually start the child')
        time.sleep(1.1)
        self.assertFalse(sentinel.exists(), 'compiler child survived output limit')

    def test_compiler_output_is_bounded_and_reported(self):
        self.script.write_text("import sys\nsys.stdout.write('x'*200000)\nsys.stdout.flush()\n")
        with mock.patch.object(checker, 'COMPILE_OUTPUT_BYTES', 4096, create=True):
            result = self.check()
        self.assertEqual(result['status'], 'COMPILE_OUTPUT_LIMIT')
        self.assertLessEqual(len(result['detail']), 4096)

    def test_missing_compiler_is_environment_error(self):
        self.settings['cl'] = str(self.root / 'missing.exe')
        self.assertEqual(self.check()['status'], 'ENVIRONMENT_ERROR')


if __name__ == '__main__':
    unittest.main()
