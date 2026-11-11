"""Real Codex client recovery from the production guard, using a mock provider."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import httpx
import uvicorn
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.qualify_app_server import RPC
from codex_perplexity_adapter.app import Settings,create_app
from codex_perplexity_adapter.request_size import REQUEST_BUDGET_BYTES, encode_request, prepare_request_body


def run(binary):
    requests=[]
    measured=[]
    from unittest.mock import patch
    def measure(payload):
        measured.append(len(encode_request(payload)))
        return prepare_request_body(payload)
    guard_patch=patch('codex_perplexity_adapter.image_resize.prepare_request_body', measure)
    guard_patch.start()
    async def provider(request):
        payload=json.loads(request.content)
        requests.append({'bytes':len(request.content),'continued':bool(payload.get('previous_response_id'))})
        ident='resp_compaction_'+str(len(requests))
        output=[{'type':'message','role':'assistant','id':'msg_'+ident,'phase':'final_answer','content':[{'type':'output_text','text':'RETAIN_GUARD_724','annotations':[]}]}]
        response={'id':ident,'object':'response','status':'completed','output':output,'usage':{'input_tokens':100,'output_tokens':10,'total_tokens':110}}
        events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]}}, {'type':'response.completed','response':response}]
        return httpx.Response(200,text=''.join('data: '+json.dumps(x)+chr(10)*2 for x in events),headers={'content-type':'text/event-stream'})
    app=create_app(Settings(api_key='synthetic-unused',local_token='qualification-token'),httpx.MockTransport(provider))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    worker=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);worker.start()
    while not server.started:time.sleep(.01)
    report={'checks':[],'provider':'mock','guard_bytes':REQUEST_BUDGET_BYTES}
    try:
        with tempfile.TemporaryDirectory(prefix='codex-compaction-guard-') as folder:
            root=Path(folder);config_home=root/'codex-home';config_home.mkdir()
            (config_home/'config.toml').write_text(f'''model="gpt-6.1-sol"
model_provider="probe"
model_context_window=40000000
model_auto_compact_token_limit=30000000
[model_providers.probe]
name="Mock"
wire_api="responses"
experimental_bearer_token="qualification-token"
supports_websockets=false
base_url="http://127.0.0.1:{port}/v1"
''')
            env=dict(os.environ,CODEX_HOME=str(config_home));env.pop('PERPLEXITY_API_KEY',None)
            with (root/'stderr').open('w') as errors:
                p=subprocess.Popen([binary,'app-server','--stdio'],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True,bufsize=1)
                rpc=RPC(p)
                try:
                    rpc.call('initialize',{'clientInfo':{'name':'compaction-qualification','version':'0.1.14'}});rpc.send('initialized')
                    t=rpc.call('thread/start',{'cwd':str(root),'approvalPolicy':'never','sandbox':'read-only','baseInstructions':'Reply only with RETAIN_GUARD_724. Preserve it during compaction.'})['thread']['id']
                    for index in range(REQUEST_BUDGET_BYTES // 990000 + 1):
                        prompt='Retain marker RETAIN_GUARD_724. Synthetic padding '+str(index)+' '+('A'*990000)
                        rpc.call('turn/start',{'threadId':t,'input':[{'type':'text','text':prompt}]})
                        done=rpc.until(lambda e:e.get('method')=='turn/completed',timeout=60)
                        report['checks'].append({'check':'seed-'+str(index),'status':done['params']['turn']['status']})
                    # Force full replay without altering history: an instruction change
                    # invalidates continuation and exercises the production guard.
                    rpc.call('turn/start',{'threadId':t,'effort':'medium','input':[{'type':'text','text':'Retain marker RETAIN_GUARD_724.'}]})
                    done=rpc.until(lambda e:e.get('method')=='turn/completed',timeout=120)
                    report['checks'].append({'check':'oversized-full-replay','status':done['params']['turn']['status'],'error':done['params']['turn'].get('error')})
                    start=len(rpc.pending)
                    rpc.call('thread/compact/start',{'threadId':t})
                    rpc.until(lambda e:e.get('method')=='turn/completed',timeout=120)
                    report['checks'].append({'check':'manual-compaction','completed':any(e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='contextCompaction' for e in rpc.pending[start:])})
                    rpc.call('turn/start',{'threadId':t,'input':[{'type':'text','text':'Repeat the retained marker.'}]})
                    done=rpc.until(lambda e:e.get('method')=='turn/completed',timeout=60)
                    report['checks'].append({'check':'after-compaction','status':done['params']['turn']['status']})
                except Exception as exc:
                    report['error']=str(exc)
                finally:p.terminate();p.wait(timeout=10)
            report['requests']=requests
            report['largest_measured_body']=max(measured)
            assert max(measured)>REQUEST_BUDGET_BYTES
            assert any(c['check']=='oversized-full-replay' and c['status']=='failed' for c in report['checks']), report['checks']
            assert any(c['check']=='oversized-full-replay' and c.get('error', {}).get('codexErrorInfo')=='contextWindowExceeded' for c in report['checks'])
            assert any(c['check']=='manual-compaction' and c['completed'] for c in report['checks'])
            assert report['checks'][-1]['status']=='completed'
            assert any('RETAIN_GUARD_724' in json.dumps(e) for e in rpc.pending if e.get('method')=='item/completed')
            report['status']=app.state.continuation.snapshot()
            report['events']=[{'method':e.get('method'),'params':e.get('params')} for e in rpc.pending if e.get('method') in ('error','turn/completed')]
            report['client']=subprocess.check_output([binary,'--version'],text=True).strip()
            Path('build/qualification-0.1.15/client-guard-compaction.json').write_text(json.dumps(report,indent=2)+chr(10))
            print(json.dumps(report),flush=True)
    finally:server.should_exit=True;worker.join(timeout=10);guard_patch.stop()

if __name__=='__main__':run(str(Path(sys.argv[1]).resolve()))
