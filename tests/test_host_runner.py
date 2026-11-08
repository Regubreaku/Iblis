import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'host'))
import isolated_runner as runner

class RunnerTests(unittest.TestCase):
    def fixture(self, td):
        p=Path(td); cl=p/'compiler'; cl.mkdir(); (cl/'cl.exe').write_bytes(b'compiler')
        game=p/'Game.exe'; game.write_bytes(b'game')
        h=lambda x:hashlib.sha256(x).hexdigest()
        cfg=dict(container_image='sha256:'+'a'*64, compiler_dir=str(cl), compiler_files={'cl.exe':h(b'compiler')}, cl='cl.exe', game=str(game), game_sha256=h(b'game'))
        f=p/'runner.json'; f.write_text(json.dumps(cfg)); return cfg,f
    def test_refuses_mutable_image_and_changed_compiler(self):
        with tempfile.TemporaryDirectory() as td:
            cfg,f=self.fixture(td)
            cfg['container_image']='runner:latest'; f.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(runner.RunnerError,'sha256'): runner.DockerRunner(f,cfg['game_sha256'])
            cfg['container_image']='sha256:'+'a'*64; f.write_text(json.dumps(cfg))
            (Path(td)/'compiler/cl.exe').write_bytes(b'tampered')
            with self.assertRaisesRegex(runner.RunnerError,'compiler'): runner.DockerRunner(f,cfg['game_sha256'])
    def test_container_has_only_disposable_readonly_inputs_and_caps(self):
        with tempfile.TemporaryDirectory() as td:
            cfg,f=self.fixture(td); r=runner.DockerRunner(f,cfg['game_sha256'])
            cmd=r.command(Path(td)/'sanitized', 'test-run')
            for flag in ('--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64','--memory=512m','--cpus=1','--user=65532:65532','--pull=never'):
                self.assertIn(flag,cmd)
            self.assertIn('type=bind,source='+str(Path(td)/'sanitized')+',target=/input,readonly',cmd)
            self.assertEqual(sum(x.startswith('type=bind') for x in cmd),1)
            self.assertNotIn('--privileged',cmd)
    def test_rejects_source_hash_or_object_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            cfg,f=self.fixture(td); r=runner.DockerRunner(f,cfg['game_sha256'])
            payload={'protocol':1,'result':{'status':'RAW_EQUAL','source_sha256':'0'*64},'object_b64':base64.b64encode(b'obj').decode()}
            with self.assertRaises(runner.RunnerError): r.decode(json.dumps(payload).encode(),'source')
    def test_entry_protocol_invalid_source_never_invokes_compiler(self):
        import runner_entry
        value=runner_entry.execute({'protocol':1,'source':'\u2603','va':'0x00401000','length':1,'flags':['/c']})
        self.assertEqual(value['result']['status'],'SOURCE_ERROR')


class LaunchBoundaryTests(unittest.TestCase):
    def test_compile_mounts_sanitized_snapshot_and_passes_no_controller_secret(self):
        import io
        import os
        with tempfile.TemporaryDirectory() as td:
            cfg,f=RunnerTests().fixture(td)
            # An unlisted adjacent compiler file must never enter the container.
            (Path(td)/'compiler/secret.txt').write_text('private')
            r=runner.DockerRunner(f,cfg['game_sha256'])
            observed={}
            class Process:
                returncode=0
                stdout=io.BytesIO(b'{"protocol":1,"result":{"status":"ENVIRONMENT_ERROR"}}')
                def wait(self,timeout=None): return 0
                def poll(self): return 0
            def launch(cmd,**kw):
                mount=next(x for x in cmd if x.startswith('type=bind,'))
                snapshot=Path(mount.split('source=',1)[1].split(',target=',1)[0])
                observed['files']={str(p.relative_to(snapshot)).replace('\\','/') for p in snapshot.rglob('*') if p.is_file()}
                observed['secret']=kw['env'].get('IBLIS_HOST_KEY')
                observed['request']=json.loads((snapshot/'request.json').read_text())
                observed['mode']=snapshot.stat().st_mode & 0o777
                return Process()
            with patch.dict(os.environ,{'IBLIS_HOST_KEY':'never-pass'}),patch.object(runner.subprocess,'Popen',side_effect=launch),patch.object(runner.subprocess,'run'):
                res=r.compile('0x00401000',1,['/c'],'int f();')
            self.assertEqual(res['status'],'ENVIRONMENT_ERROR')
            self.assertNotIn('compiler/secret.txt',observed['files'])
            self.assertIn('compiler/cl.exe',observed['files'])
            self.assertIsNone(observed['secret'])
            self.assertEqual(observed['request']['source'],'int f();')
            if os.name!='nt': self.assertEqual(observed['mode'],0o755)

    def test_decode_rejects_wrong_object_hash_with_correct_source_hash(self):
        with tempfile.TemporaryDirectory() as td:
            cfg,f=RunnerTests().fixture(td); r=runner.DockerRunner(f,cfg['game_sha256'])
            payload=dict(protocol=1,result=dict(status='RAW_EQUAL',source_sha256=hashlib.sha256(b'source').hexdigest(),object_sha256='0'*64),object_b64=base64.b64encode(b'obj').decode())
            with self.assertRaises(runner.RunnerError): r.decode(json.dumps(payload).encode(),'source')

if __name__ == '__main__': unittest.main()
