"""Offline startup/authentication/production guard checks on extracted servers."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.request_size import REQUEST_BUDGET_BYTES, encode_request
import urllib.error
import urllib.request

root=Path(__file__).resolve().parents[1]
checks=[]
for arch in ['arm64','x86_64']:
    executable=Path(os.environ.get('ADAPTER_VERIFICATION_ROOT', str(root/'build/qualification-0.1.14/final-extracted')))/arch/'Codex Perplexity Adapter.app/Contents/Resources/adapter-server'
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    env=dict(os.environ,PERPLEXITY_API_KEY='synthetic-unused')
    command=([str(executable)] if arch=='arm64' else ['arch','-x86_64',str(executable)])+['--port',str(port),'--local-token','packaged-test-token','--log-level','error']
    with (root/f'build/qualification-0.1.14/{arch}-server-check.log').open('w') as log:
        process=subprocess.Popen(command,env=env,stdout=log,stderr=log)
        def request(path,body=None,token=None):
            headers={'Content-Type':'application/json'}
            if token:headers['Authorization']='Bearer '+token
            req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=body,headers=headers)
            try:r=urllib.request.urlopen(req,timeout=15)
            except urllib.error.HTTPError as exc:r=exc
            return r.status,r.read()
        try:
            for attempt in range(100):
                if process.poll() is not None:raise RuntimeError('Server exited')
                try:
                    status,body=request('/health')
                    if status==200:break
                except OSError:time.sleep(.1)
            health=status==200
            denied,_=request('/v1/adapter/status')
            authenticated,body=request('/v1/adapter/status',token='packaged-test-token')
            snapshot=json.loads(body)
            stream_checks=[]
            for stream in [False,True]:
                payload={'model':'gpt-6.1-sol','input':[{'role':'user','content':'X'*REQUEST_BUDGET_BYTES}],'stream':stream}
                status,body=request('/v1/responses',encode_request(payload),token='packaged-test-token')
                if stream:
                    events=[json.loads(line[6:]) for line in body.decode().splitlines() if line.startswith('data: {')]
                    error=events[-1]['response']['error']
                else:error=json.loads(body)['error']
                stream_checks.append({'stream':stream,'http_status':status,'recognized_error':error['code']=='context_length_exceeded'})
            record={'arch':arch,'health':health,'unauthenticated_status':denied,'authenticated_status':authenticated,'numeric_tool_counter':type(snapshot.get('tool_boundary_replays')) is int,'oversized':stream_checks}
            checks.append(record)
            if not (health and denied==401 and authenticated==200 and record['numeric_tool_counter'] and all(x['recognized_error'] for x in stream_checks)):raise AssertionError(record)
        finally:
            process.terminate();process.wait(timeout=10)
output=root/'build/qualification-0.1.14/packaged-server-checks.json'
output.write_text(json.dumps(checks,indent=2)+chr(10))
print(json.dumps(checks))
