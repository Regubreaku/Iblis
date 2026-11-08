"""Protocol v1 entry point, executed ONLY inside the locked-down Linux/Wine container."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys


def execute(request):
    try:
        if request.get('protocol') != 1: raise ValueError('protocol')
        source=request['source']
        if not isinstance(source,str) or not source or len(source)>262144: raise ValueError('source length')
        source.encode('latin-1')
    except (KeyError,ValueError,UnicodeError):
        return dict(protocol=1,result=dict(status='SOURCE_ERROR',detail='source must be Latin-1 text under 262144 characters'))
    try:
        inp=Path('/input')
        for name,want in request['compiler_files'].items():
            if hashlib.sha256((inp/'compiler'/name).read_bytes()).hexdigest()!=want: raise ValueError('compiler hash')
        game=inp/'Game.exe'
        if hashlib.sha256(game.read_bytes()).hexdigest()!=request['game_sha256']: raise ValueError('image hash')
        sys.path.insert(0,str(inp/'kit'))
        import iblis_check as kit
        cl=inp/'compiler'/request['cl']
        settings=dict(image=str(game),cl=str(cl),runner='wine')
        res=kit.compile_and_compare(settings,request['va'],request['length'],request['flags'],source,'/work/build')
        value=dict(protocol=1,result=res)
        if res['status'] in ('RAW_EQUAL','RELOCATION_RESOLVED_EQUAL'):
            obj=Path('/work/build/candidate.obj').read_bytes()
            if len(obj)>8*1024*1024: raise ValueError('object size')
            res.update(object_sha256=hashlib.sha256(obj).hexdigest(),cl_sha256=request['compiler_files'][request['cl']],
                       claimed_length=request['length'],original_body_sha256=hashlib.sha256(kit.PE(str(game)).read(int(request['va'],16),request['length'])).hexdigest())
            records=res.pop('relocation_records',None)
            if records is None and res.get('relocations')==0: records=[]
            if records is None: raise ValueError('checker does not expose verifier relocation records')
            res['relocations']=records
            value['object_b64']=base64.b64encode(obj).decode('ascii')
        return value
    except Exception as e:
        return dict(protocol=1,result=dict(status='ENVIRONMENT_ERROR',detail='isolated runner '+type(e).__name__))

if __name__=='__main__':
    print(json.dumps(execute(json.loads(Path('/input/request.json').read_text(encoding='utf-8')))))
