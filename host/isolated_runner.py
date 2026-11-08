"""Controller-side Docker adapter. Never executes submitted source on the controller."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import uuid

class RunnerError(RuntimeError):
    pass


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


class DockerRunner:
    def __init__(self, config_path, image_sha256, compiler_prefix=None):
        self.cfg = json.loads(Path(config_path).read_text(encoding='utf-8'))
        self.image_sha256 = image_sha256
        self.compiler_prefix = compiler_prefix
        self.validate()

    def validate(self):
        c = self.cfg
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', c.get('container_image','')):
            raise RunnerError('container_image must be an immutable local sha256 image ID')
        if c.get('game_sha256') != self.image_sha256 or digest(c['game']) != self.image_sha256:
            raise RunnerError('Game.exe authentication failed')
        files = c.get('compiler_files', {})
        if not files or c.get('cl') not in files:
            raise RunnerError('compiler manifest must include cl')
        base = Path(c['compiler_dir']).resolve()
        for name, want in files.items():
            path = base / name
            if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.resolve().is_relative_to(base):
                raise RunnerError('compiler path escapes dedicated input directory')
            if not re.fullmatch('[a-f0-9]{64}',want) or digest(path) != want:
                raise RunnerError('compiler manifest hash mismatch: ' + name)
        if self.compiler_prefix and not files[c['cl']].startswith(self.compiler_prefix):
            raise RunnerError('compiler differs from repository toolchain')

    def command(self, input_dir, name):
        return ['docker','run','--rm','--pull=never','--name',name,'--network=none','--read-only',
                '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64','--memory=512m',
                '--memory-swap=512m','--cpus=1','--user=65532:65532','--log-driver=none',
                '--ulimit=nofile=128:128','--ulimit=fsize=16777216:16777216',
                '--tmpfs=/tmp:rw,nosuid,nodev,size=256m,mode=1777',
                '--tmpfs=/work:rw,nosuid,nodev,size=64m,mode=1777',
                '--env=HOME=/tmp','--env=WINEPREFIX=/tmp/wine','--env=WINEDEBUG=-all',
                '--workdir=/work','--mount','type=bind,source='+str(input_dir)+',target=/input,readonly',
                '--entrypoint=python3',self.cfg['container_image'],'/input/runner_entry.py']

    def decode(self, data, source):
        try:
            value = json.loads(data)
            if value['protocol'] != 1: raise ValueError('protocol')
            res = value['result']
            if not isinstance(res,dict) or not isinstance(res.get('status'),str): raise ValueError('result')
            if res['status'] in ('RAW_EQUAL','RELOCATION_RESOLVED_EQUAL'):
                obj = base64.b64decode(value['object_b64'],validate=True)
                if len(obj) > 8*1024*1024: raise ValueError('object size')
                if res['source_sha256'] != hashlib.sha256(source.encode('latin-1')).hexdigest(): raise ValueError('source hash')
                if res['object_sha256'] != hashlib.sha256(obj).hexdigest(): raise ValueError('object hash')
                res['object_bytes'] = obj
            return res
        except (ValueError,KeyError,TypeError) as e:
            raise RunnerError('invalid isolated runner response') from e

    def compile(self, va, length, flags, source):
        self.validate()  # rehash for each launch; no stale authentication
        with tempfile.TemporaryDirectory(prefix='iblis-isolated-') as temp:
            dest=Path(temp)
            # Explicit allowlist: no private repo, controller settings, environment or credentials.
            shutil.copyfile(self.cfg['game'],dest/'Game.exe')
            for name in self.cfg['compiler_files']:
                p=dest/'compiler'/name; p.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(Path(self.cfg['compiler_dir'])/name,p)
            here=Path(__file__).resolve().parent
            shutil.copyfile(here/'runner_entry.py',dest/'runner_entry.py')
            kit=here.parent/'kit'; (dest/'kit').mkdir()
            for name in ('iblis_check.py','kit.json'):
                shutil.copyfile(kit/name,dest/'kit'/name)
            shutil.copytree(kit/'headers',dest/'kit/headers')
            request=dict(protocol=1,va=va,length=length,flags=flags,source=source,
                         game_sha256=self.image_sha256,cl=self.cfg['cl'],compiler_files=self.cfg['compiler_files'])
            (dest/'request.json').write_text(json.dumps(request,ensure_ascii=True),encoding='utf-8')
            # Verify copied bytes too, closing the source-file mutation window.
            if digest(dest/'Game.exe') != self.image_sha256: raise RunnerError('game changed during copy')
            for name,h in self.cfg['compiler_files'].items():
                if digest(dest/'compiler'/name)!=h: raise RunnerError('compiler changed during copy')
            # tempfile is mode 0700 on Linux; the unprivileged container must read
            # this sanitized snapshot, while its mount remains read-only.
            dest.chmod(0o755)
            for entry in dest.rglob('*'):
                entry.chmod(0o755 if entry.is_dir() else 0o644)
            name='iblis-'+uuid.uuid4().hex
            cmd=self.command(dest,name)
            # No controller secrets inherited even by the Docker CLI. Docker Desktop's local
            # default endpoint is used; custom contexts/remote daemons are deliberately unsupported.
            env={k:v for k,v in os.environ.items() if k in ('PATH','SystemRoot','WINDIR','TEMP','TMP','PROGRAMDATA')}
            env['DOCKER_CONFIG']=str(dest/'empty-docker-config')
            try:
                proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,env=env)
            except OSError as e:
                raise RunnerError('Docker unavailable; public compilation is disabled') from e
            chunks=[]; too_large=threading.Event()
            def read_output():
                total=0
                while True:
                    block=proc.stdout.read(65536)
                    if not block: break
                    total+=len(block)
                    if total>12*1024*1024:
                        too_large.set(); proc.kill(); break
                    chunks.append(block)
            thread=threading.Thread(target=read_output,daemon=True); thread.start()
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired as e:
                raise RunnerError('isolated runner exceeded 120 seconds') from e
            finally:
                if proc.poll() is None: proc.kill()
                proc.wait()
                # Killing docker client does not reliably kill its container.
                subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,timeout=15)
                thread.join(timeout=5)
                proc.stdout.close()
            if too_large.is_set() or thread.is_alive(): raise RunnerError('isolated runner output limit')
            if proc.returncode: raise RunnerError('isolated runner failed; check local Docker configuration')
            return self.decode(b''.join(chunks),source)
