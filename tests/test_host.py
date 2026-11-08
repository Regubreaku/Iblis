import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
import host

class HostTests(unittest.TestCase):
    def test_unconfigured_runner_fails_closed(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'IBLIS_RUNNER_CONFIG'):
                host.load_runner({})

    def test_infrastructure_result_never_becomes_contributor_failure(self):
        self.assertEqual(host.verdict_for({'status': 'INFRA_ERROR', 'detail': 'engine down'})[0], 'review')

    def test_bad_unicode_does_not_stop_next_result(self):
        with tempfile.TemporaryDirectory() as td:
            repo = type('Repo', (), {'root': td, 'flags': lambda s, va: ['/c']})()
            class Runner:
                def compile(self, va, length, flags, source):
                    return {'status': 'NO_MARKER', 'detail': 'missing'}
            posted = []
            items = [dict(id=1, va='0x00401000', source='\u2603', login='a'), dict(id=2, va='0x00401000', source='int x;', login='b')]
            with patch.object(host, 'api', side_effect=lambda method, path, body=None: posted.append(body) or {}):
                host.process_items(repo, Runner(), {'0x00401000': 1}, items)
            self.assertEqual([p['id'] for p in posted], [1, 2])
            self.assertEqual([p['verdict'] for p in posted], ['failed', 'failed'])

    def test_review_list_and_recheck_use_review_endpoint(self):
        repo = type('Repo', (), {'queue': lambda s: []})()
        calls = []
        with patch.object(host, 'api', side_effect=lambda method, path, body=None: calls.append((method,path,body)) or {'review': []}):
            host.cmd_review(repo, 'list')
        self.assertEqual(calls, [('GET', '/api/v1/host/review', None)])

    def test_import_writes_verifier_contract_without_overwriting(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'queue/open').mkdir(parents=True)
            q = root/'queue/open/00401000.json'
            q.write_text(json.dumps(dict(va='0x00401000', length=1, attempts_used=0)))
            wd = root/'work/host/0x00401000-r1'
            wd.mkdir(parents=True)
            source = '// 0x00401000\nint f(){return 0;}\n'
            obj = b'object fixture'
            (wd/'candidate.obj').write_bytes(obj)
            h = hashlib.sha256
            res = dict(status='RAW_EQUAL', compiled_body_sha256='b'*64, source_sha256=h(source.encode()).hexdigest(), object_sha256=h(obj).hexdigest(), symbol='_f', command='/c', command_argv=['wine','/input/compiler/bin/cl.exe','/c','/Focandidate.obj','candidate.cpp'], compiled_length=1, length=1, claimed_length=1, original_body_sha256='a'*64, relocations=[], cl_sha256='c'*64)
            record = dict(id=1, va='0x00401000', source=source, login='alice', github_id=7, show_name=True, host=res, verdict='verified', delivered=True)
            vp=wd/'verdict.json'; vp.write_text(json.dumps(record))
            repo=type('Repo', (), {'root':td, 'cfg':{'rules':{'share_objects':False}}})()
            host.cmd_integrate(repo)
            sd=root/'queue/submitted/00401000'
            self.assertTrue((root/'queue/submitted/00401000.iblis-7.json').exists())
            cmp=json.loads((sd/'COMPARE.json').read_text())
            self.assertEqual(cmp['claimed_length'],1)
            self.assertEqual(cmp['relocations'],[])
            att=json.loads((sd/'attempts.json').read_text())
            self.assertEqual(att[-1]['cl_sha256'],'c'*64)
            self.assertEqual(att[-1]['exit_code'],0)
            self.assertEqual(att[-1]['command'],['wine','/input/compiler/bin/cl.exe','/c','/Focandidate.obj','candidate.cpp'])
            for line in (sd/'SUBMISSION_MANIFEST.sha256').read_text().splitlines():
                digest,name=line.split('  ')
                self.assertEqual(digest,h((sd/name).read_bytes()).hexdigest())
            self.assertFalse(q.exists())
            self.assertTrue(json.loads(vp.read_text())['integrated'])
            self.assertEqual((root/'work/host/objects/00401000/candidate.obj').read_bytes(),obj)
            # Another contributor's existing submission is never replaced.
            vp.write_text(json.dumps(dict(record,id=2)))
            before=(sd/'candidate.cpp').read_bytes()
            host.cmd_integrate(repo)
            self.assertEqual((sd/'candidate.cpp').read_bytes(),before)
            self.assertNotIn('integrated',json.loads(vp.read_text()))



class ErrorIsolationTests(unittest.TestCase):
    def test_runner_failure_and_delivery_failure_do_not_stop_batch(self):
        with tempfile.TemporaryDirectory() as td:
            repo=type('Repo',(),{'root':td,'flags':lambda s,va:[]})()
            class BrokenRunner:
                def compile(self,*args): raise RuntimeError('offline')
            items=[dict(id=i,va='0x00401000',source='int x;') for i in (1,2)]
            calls=[]
            def transport(method,path,body):
                calls.append(body)
                if body['id']==1: raise OSError('offline')
                return {}
            with patch.object(host,'api',side_effect=transport):
                host.process_items(repo,BrokenRunner(),{'0x00401000':1},items)
            self.assertEqual([x['verdict'] for x in calls],['review','review'])
            self.assertFalse(json.loads((Path(td)/'work/host/0x00401000-r1/verdict.json').read_text())['delivered'])
            self.assertTrue(json.loads((Path(td)/'work/host/0x00401000-r2/verdict.json').read_text())['delivered'])

    def test_source_encoding_failure_is_contributor_error(self):
        self.assertEqual(host.verdict_for({'status':'SOURCE_ERROR','detail':'encoding'})[0],'failed')

    def test_compile_diagnostics_not_sent_to_public_service(self):
        v,note=host.verdict_for({'status':'COMPILE_ERROR','detail':'PRIVATE_COMPILER_DIAGNOSTIC'})
        self.assertEqual(v,'failed')
        self.assertNotIn('PRIVATE_COMPILER_DIAGNOSTIC',note)

    def test_review_recheck_and_adjudication(self):
        rows=[dict(id=8,va='0x00401000',source='int x;')]
        calls=[]
        def transport(method,path,body=None):
            calls.append((method,path,body)); return {'review':rows}
        repo=type('Repo',(),{'root':'unused','cfg':{},'queue':lambda s:[{'va':'0x00401000','length':1}]})()
        with patch.object(host,'api',side_effect=transport),patch.object(host,'load_runner',return_value='runner'),patch.object(host,'process_items') as process:
            host.cmd_review(repo,'recheck',8)
            process.assert_called_once_with(repo,'runner',{'0x00401000':1},rows)
            host.cmd_review(repo,'adjudicate',8,'failed','confirmed source error')
        self.assertEqual(calls[-1],('POST','/api/v1/host/verdict',{'id':8,'verdict':'failed','note':'owner: confirmed source error'}))

class PublicNoteTests(unittest.TestCase):
    def test_review_details_from_image_or_runtime_remain_local(self):
        for status in ('DIFF','ENVIRONMENT_ERROR','OBJECT_ERROR','COMPILE_TIMEOUT','COMPILE_OUTPUT_LIMIT'):
            v,note=host.verdict_for(dict(status=status,detail='IMAGE_DERIVED_WORD',target_problems=['PRIVATE_DWORD']))
            self.assertEqual(v,'review')
            self.assertNotIn('IMAGE_DERIVED_WORD',note)
            self.assertNotIn('PRIVATE_DWORD',note)


class ManifestNormalizationTests(unittest.TestCase):
    def test_json_artifacts_are_lf_before_manifest_hashing(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'COMPARE.json'
            host.save_json(p,{'status':'RAW_EQUAL','length':1})
            raw=p.read_bytes()
            self.assertNotIn(b'\r\n',raw)
            self.assertEqual(hashlib.sha256(raw).hexdigest(),hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest())


class SourceNormalizationTests(unittest.TestCase):
    def test_crlf_source_is_compiled_and_recorded_as_same_lf_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            repo=type('Repo',(),{'root':td,'flags':lambda s,va:[]})()
            class HashRunner:
                def compile(self,va,length,flags,source):
                    return dict(status='RAW_EQUAL',source_sha256=hashlib.sha256(source.encode('latin-1')).hexdigest(),compiled_body_sha256='a'*64)
            p=dict(id=1,va='0x00401000',source='// 0x00401000\r\nint f(){}\r\n')
            with patch.object(host,'api',return_value={}): host.process_items(repo,HashRunner(),{'0x00401000':1},[p])
            record=json.loads((Path(td)/'work/host/0x00401000-r1/verdict.json').read_text())
            self.assertNotIn('\r',record['source'])
            self.assertEqual(record['host']['source_sha256'],hashlib.sha256(record['source'].encode('latin-1')).hexdigest())
            self.assertEqual(record['original_source'],p['source'])


class ReviewPagingTests(unittest.TestCase):
    def test_review_list_fetches_later_pages(self):
        import contextlib
        import io
        responses=[{'review':[{'id':20}],'next_cursor':20},{'review':[{'id':21}],'next_cursor':None}]
        calls=[]
        def transport(method,path,body=None):
            calls.append(path);return responses.pop(0)
        output=io.StringIO()
        with patch.object(host,'api',side_effect=transport),contextlib.redirect_stdout(output):
            host.cmd_review(object(),'list')
        self.assertEqual(json.loads(output.getvalue()),[{'id':20},{'id':21}])
        self.assertEqual(calls,['/api/v1/host/review','/api/v1/host/review?after=20&limit=100'])

    def test_review_by_id_does_not_depend_on_first_page(self):
        calls=[]
        def transport(method,path,body=None):
            calls.append(path)
            return {'review':[{'id':700}] if path.endswith('?id=700') else []}
        with patch.object(host,'api',side_effect=transport):
            host.cmd_review(object(),'adjudicate',700,'failed','reviewed')
        self.assertEqual(calls,['/api/v1/host/review?id=700','/api/v1/host/verdict'])


class SourceLimitTests(unittest.TestCase):
    def test_api_sized_source_reaches_isolated_runner(self):
        with tempfile.TemporaryDirectory() as td:
            repo=type('Repo',(),{'root':td,'flags':lambda s,va:[]})()
            runner=type('Runner',(),{'compile':lambda s,*args:{'status':'NO_MARKER','detail':'missing'}})()
            p=dict(id=1,va='0x00401000',source='// comment\n'*19091)
            with patch.object(host,'api',return_value={}): host.process_items(repo,runner,{'0x00401000':1},[p])
            record=json.loads((Path(td)/'work/host/0x00401000-r1/verdict.json').read_text())
            self.assertEqual(record['host']['status'],'NO_MARKER')

if __name__ == '__main__': unittest.main()
