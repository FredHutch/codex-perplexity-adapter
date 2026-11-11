"""Isolated actual-client image tools against a mock or metered live provider."""
import argparse
from contextlib import nullcontext
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import httpx
import uvicorn
from codex_perplexity_adapter.app import Settings, create_app
from research.tool_continuation import experiment,create_research_app
from research.budget import OUT
from research.fixtures import page
from research.live import Session
from codex_perplexity_adapter.transform import transform_request
from codex_perplexity_adapter.request_size import encode_request
from scripts.qualify_app_server import RPC


def run(binary,strategy,live=False):
    seen=[];saved={};stage=[0];path=[None]
    async def provider(request):
        p=json.loads(request.content);seen.append(p)
        prior=p.get('previous_response_id');history=(saved[prior] if prior else [])+p['input']
        if stage[0]==1:
            names=[t.get('name','') for t in p['tools']]
            print(json.dumps({'dispatch_tools':names}),flush=True)
            name=next((n for n in names if n.endswith('view_image')),None)
            arguments={'path':path[0]}
            if name is None:
                name='functions__exec'
                arguments={'content':'image((await tools.view_image({path:'+json.dumps(path[0])+'})).image_url);'}
            output=[{'id':'fc_image','type':'function_call','name':name,'call_id':'call_image','arguments':json.dumps(arguments),'status':'completed'}]
            stage[0]=2
        else:
            output=[{'id':'msg_'+str(len(seen)),'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'IMAGE_TOOL_MARKER_246','annotations':[]}]}]
        ident='resp_'+str(len(seen));saved[ident]=history+output
        response={'id':ident,'object':'response','status':'completed','output':output,'usage':{'input_tokens':100,'output_tokens':10,'total_tokens':110}}
        events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]}}]
        for i,item in enumerate(output):
            if item['type']=='function_call':
                events += [{'type':'response.output_item.added','output_index':i,'item':item},{'type':'response.output_item.done','output_index':i,'item':item}]
        events += [{'type':'response.completed','response':response}]
        class Chunks(httpx.AsyncByteStream):
            async def __aiter__(self):
                for event in events:
                    body=('data: '+json.dumps(event)+'\n\n').encode()
                    for k in range(0,len(body),31):yield body[k:k+31]
        return httpx.Response(200,stream=Chunks(),headers={'content-type':'text/event-stream'})
    with (nullcontext() if strategy == "production" else experiment(strategy)):
        session=Session(strategy,True,False) if live else None
        app=session.app if live else create_app(Settings(api_key='synthetic',local_token='qualification-token'),httpx.MockTransport(provider))
        class Bounded:
            async def __call__(self,scope,receive,send):
                if not live or scope.get('path')!='/v1/responses':
                    return await app(scope,receive,send)
                body=b''
                while True:
                    event=await receive();body+=event.get('body',b'')
                    if not event.get('more_body',False):break
                payload=json.loads(body);payload['max_output_tokens']=512
                payload['reasoning']={'effort':'medium'}
                full,_=transform_request(payload,'openai/gpt-6.1-sol')
                seen.append(full)
                session.meter.effective=full;session.meter.label='client-'+str(len(seen))
                sent=False
                async def once():
                    nonlocal sent
                    if not sent:
                        sent=True;return {'type':'http.request','body':encode_request(payload),'more_body':False}
                    return await receive()
                return await app(scope,once,send)
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(Bounded(),log_level='error',access_log=False))
        async def serve():
            try:
                await server.serve(sockets=[sock])
            finally:
                if session is not None:
                    await session.close()
        worker=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True);worker.start()
        while not server.started:time.sleep(.01)
        try:
          with tempfile.TemporaryDirectory(prefix='continuation-images-') as folder:
            root=Path(folder);home=root/'home';home.mkdir();im=root/'synthetic.png';page(0).save(im);path[0]=str(im)
            (home/'config.toml').write_text(f'model="gpt-6.1-sol"\nmodel_provider="probe"\nmodel_reasoning_effort="medium"\n[model_providers.probe]\nname="Synthetic"\nwire_api="responses"\nexperimental_bearer_token="qualification-token"\nsupports_websockets=false\nbase_url="http://127.0.0.1:{port}/v1"\n')
            env=dict(os.environ,CODEX_HOME=str(home));env.pop('PERPLEXITY_API_KEY',None)
            version=subprocess.check_output([binary,'--version'],env=env,text=True).strip()
            kind='live' if live else 'mock'
            log=OUT/f'client-images-{kind}-{strategy}-{version.replace(" ","-")}.log'
            with log.open('w') as errors:
                process=subprocess.Popen([binary,'app-server','--stdio'],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True,bufsize=1);rpc=RPC(process)
                try:
                    rpc.call('initialize',{'clientInfo':{'name':'tool-image-qualification','version':'0.1'},'capabilities':{'experimentalApi':True}});rpc.send('initialized')
                    ident=rpc.call('thread/start',{'cwd':str(root),'approvalPolicy':'never','sandbox':'read-only'})['thread']['id']
                    rpc.call('turn/start',{'threadId':ident,'input':[{'type':'text','text':'Remember this synthetic image for later. Reply ACK only, without tools.'},{'type':'localImage','path':str(im)}]})
                    first=rpc.until(lambda e:e.get('method')=='turn/completed',120)
                    stage[0]=1
                    second_start=len(rpc.pending)
                    rpc.call('turn/start',{'threadId':ident,'input':[{'type':'text','text':f'Use view_image to inspect {im}, then report its Page ID, Dose, and rectangle color. Only use tools needed to view this image; finish after viewing it.'}]})
                    second=rpc.until(lambda e:e.get('method')=='turn/completed',120)
                    third_start=len(rpc.pending)
                    rpc.call('turn/start',{'threadId':ident,'input':[{'type':'text','text':'Without tools repeat the Page ID and Dose from the image.'}]})
                    third=rpc.until(lambda e:e.get('method')=='turn/completed',120)
                    tools=[p for p in seen if any(i.get('type')=='function_call_output' and isinstance(i.get('output'),list) and any(x.get('type')=='input_image' for x in i['output'] if isinstance(x,dict)) for i in p.get('input',[]) if isinstance(i,dict))]
                    result={'client':version,'strategy':strategy,'turns':[x['params']['turn']['status'] for x in (first,second,third)],
                        'image_tool_result_transmitted':bool(tools),'shapes':[{'bytes':len(encode_request(p)),'continued':bool(p.get('previous_response_id')),'items':len(p.get('input',[]))} for p in seen],
                        'stats':app.state.continuation.snapshot()}
                    result['errors']=[x['params']['turn'].get('error') for x in (first,second,third)]
                    result['passed']=all(x=='completed' for x in result['turns']) and bool(tools) and (live or result['shapes'][-1]['continued'])
                    if live:
                        result['live']=True
                        result['transmitted']=[{k:v for k,v in record.items() if k!='records'} for record in session.meter.records]
                        def final_text(events):
                            return ' '.join(e.get('params',{}).get('item',{}).get('text','') for e in events if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='agentMessage')
                        result['tool_answer']=final_text(rpc.pending[second_start:third_start])
                        result['followup_answer']=final_text(rpc.pending[third_start:])
                        result['visual_values_received']=all(v.lower() in result['tool_answer'].lower() for v in ('731842','1.25','blue'))
                        result['followup_visual_retained']=all(v in result['followup_answer'] for v in ('731842','1.25'))
                        result['passed']=result['passed'] and result['visual_values_received'] and result['followup_visual_retained'] and all(r['continued'] for r in result['transmitted'][1:])
                        result['budget']=session.budget.summary()
                    (OUT/f'client-images-{kind}-{strategy}-{version.replace(" ","-")}.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
                    assert result['passed']
                finally:process.terminate();process.wait(timeout=10)
        finally:
            server.should_exit=True;worker.join(timeout=10)
            if worker.is_alive():
                raise RuntimeError('Isolated server did not stop')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--strategy',required=True);p.add_argument('--client',required=True);p.add_argument('--live',action='store_true');a=p.parse_args();run(a.client,a.strategy,a.live)
