"""Synthetic live qualification; run only under the separately approved budget."""
import argparse
import asyncio
import copy
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
import httpx

from codex_perplexity_adapter.app import Settings
from codex_perplexity_adapter.request_size import encode_request
from codex_perplexity_adapter.transform import transform_request
from research.budget import LiveBudget, OUT
from research.tool_continuation import experiment, create_research_app
from scripts.qualify_continuation import completed, text, msg

TOOLS=[{'type':'function','name':'probe','description':'Return a synthetic qualification marker.',
        'parameters':{'type':'object','properties':{'round':{'type':'integer'}},'required':['round'],'additionalProperties':False},'strict':True}]
HEADERS={'Authorization':'Bearer qualification-token','session-id':'tool-research','thread-id':'isolated'}


def safe_error(error):
    if not isinstance(error,dict):return {'message':str(error)[:500]} if error else None
    result={k:error[k] for k in ('type','code','param') if k in error}
    message=str(error.get('message',''))
    message=re.sub(r'(?i)(resp_|call_|req_)[a-z0-9_-]+','[redacted-id]',message)
    result['message']=message[:1500]
    return result


class Meter(httpx.AsyncBaseTransport):
    def __init__(self,budget):
        self.budget=budget;self.inner=httpx.AsyncHTTPTransport(retries=0)
        self.records=[];self.effective=None;self.label=''
    async def handle_async_request(self,request):
        data=await request.aread();p=json.loads(data)
        reserved=[self.budget.reserve(self.effective,len(data),self.label)]
        record={'body_bytes':len(data),'input_items':len(p.get('input',[])),
                'continued':bool(p.get('previous_response_id')),'records':reserved}
        self.records.append(record)
        response=await self.inner.handle_async_request(request)
        original=response.stream;budget=self.budget
        class UsageStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                buffer=b''
                async for chunk in original:
                    buffer+=chunk
                    while b'\n\n' in buffer:
                        block,buffer=buffer.split(b'\n\n',1)
                        for line in block.splitlines():
                            if line.startswith(b'data: {'):
                                try:
                                    event=json.loads(line[6:])
                                    if event.get('type')=='response.completed':
                                        budget.settle(reserved[0],event.get('response',{}))
                                except ValueError:pass
                    if len(buffer)>2_000_000:buffer=b''
                    yield chunk
                if buffer.startswith(b'{'):
                    try:budget.settle(reserved[0],json.loads(buffer))
                    except ValueError:pass
            async def aclose(self):await original.aclose()
        response.stream=UsageStream()
        return response
    async def aclose(self):pass
    async def close(self):await self.inner.aclose()


class Session:
    def __init__(self,strategy,stream=False,store=False):
        self.strategy=strategy;self.stream=stream;self.store=store;self.history=[];self.tools=TOOLS
        self.budget=LiveBudget();self.meter=Meter(self.budget)
        self.ctx=experiment(strategy);self.ctx.__enter__()
        keypath=Path('/private/tmp/perplexity-tool-continuation-test-key')
        if keypath.exists() and keypath.stat().st_mode & 0o077:
            raise RuntimeError('Test key file must have owner-only permissions')
        key=keypath.read_text().strip() if keypath.exists() and keypath.stat().st_size else os.environ.get('PERPLEXITY_API_KEY')
        if not key:
            raise RuntimeError('A test API key is required')
        self.app=create_research_app(strategy,Settings(api_key=key,
            local_token='qualification-token',timeout_seconds=180),self.meter)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://isolated',timeout=240)
        self.results=[];self.id=uuid.uuid4().hex
    async def call(self,label,append=None):
        if append:self.history+=append
        payload={'model':'gpt-6.1-sol','input':copy.deepcopy(self.history),'tools':self.tools,
                 'tool_choice':'auto','stream':self.stream,'store':self.store,
                 'reasoning':{'effort':'medium'},'max_output_tokens':512}
        self.meter.effective=transform_request(payload,'openai/gpt-6.1-sol')[0]
        self.meter.label=label
        before=len(self.meter.records);start=time.monotonic()
        try:
            response=await self.client.post('/v1/responses',headers={**HEADERS,'thread-id':self.id},json=payload)
            result=completed(response)
            transmitted=self.meter.records[before:]
            if transmitted:self.budget.settle(transmitted[-1]['records'][0],result)
            check={'label':label,'http_status':response.status_code,'status':result.get('status'),
                   'error':safe_error(result.get('error')),'full_body_bytes':len(encode_request(self.meter.effective)),
                   'requests':[{k:v for k,v in r.items() if k!='records'} for r in transmitted],
                   'seconds':round(time.monotonic()-start,3),'text':text(result),
                   'tool_calls':sum(i.get('type')=='function_call' for i in result.get('output',[]))}
        except Exception as exc:
            result={};check={'label':label,'status':'exception','error':safe_error({'message':type(exc).__name__+': '+str(exc)}),
                  'full_body_bytes':len(encode_request(self.meter.effective)),
                  'requests':[{k:v for k,v in r.items() if k!='records'} for r in self.meter.records[before:]],
                  'seconds':round(time.monotonic()-start,3)}
        self.results.append(check); print(json.dumps(check),flush=True)
        if result.get('status')=='completed':self.history+=result['output']
        return result
    async def close(self):
        await self.client.aclose();await self.meter.close();self.ctx.__exit__(None,None,None)


async def basic(strategy,stream,store,repeat):
    s=Session(strategy,stream,store)
    seed=f'SEED_{repeat}_782';marker=f'TOOL_RESULT_{repeat}_593'
    report={'strategy':strategy,'stream':stream,'store':store,'repeat':repeat,'checks':s.results,'passed':False}
    try:
        if strategy=='anchor':
            r=await s.call('anchor-answer',[msg(f'Remember {seed}. Reply ACK only. Do not call tools.')])
            if r.get('status')!='completed' or any(i.get('type')=='function_call' for i in r.get('output',[])):return report
            prompt='Now call probe once with round 1. After its result, repeat the saved SEED marker and returned tool marker exactly, then finish. Do not call tools again.'
        else:
            prompt=f'Remember {seed}. Call probe once with round 1. After its result, repeat the saved SEED marker and returned tool marker exactly, then finish. Do not call tools again.'
        r=await s.call('tool-dispatch',[msg(prompt)])
        calls=[i for i in r.get('output',[]) if i.get('type')=='function_call']
        if r.get('status')!='completed' or len(calls)!=1:return report
        r=await s.call('tool-result',[{'type':'function_call_output','call_id':calls[0]['call_id'],'output':marker}])
        report['retained_seed']=seed in text(r);report['retained_result']=marker in text(r)
        report['passed']=r.get('status')=='completed' and report['retained_seed'] and report['retained_result'] and not any(i.get('type')=='function_call' for i in r.get('output',[]))
        if report['passed']:
            r=await s.call('ordinary-followup',[msg('Without tools repeat the saved SEED and tool result markers exactly.')])
            report['passed']=r.get('status')=='completed' and seed in text(r) and marker in text(r)
    finally:await s.close()
    return report


async def run(args):
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/'basic-live.json'
    results=json.loads(path.read_text()) if path.exists() else []
    for strategy in args.strategies:
      for stream in (False,True):
       for store,repetitions in ((False,args.repeats),(True,1)):
        for repeat in range(repetitions):
         key=(strategy,stream,store,repeat)
         if any((r['strategy'],r['stream'],r['store'],r['repeat'])==key and not any(c.get('http_status')==401 for c in r['checks']) for r in results):continue
         report=await basic(strategy,stream,store,repeat)
         results.append(report);path.write_text(json.dumps(results,indent=2)+'\n')
         print(json.dumps({'chain':key,'passed':report['passed'],'budget':LiveBudget().summary()}),flush=True)
         if any(c.get('http_status')==401 for c in report['checks']):
             raise RuntimeError('Authentication failed; stopped further live calls')
    return results

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--strategies',nargs='+',choices=['replay','direct','anchor'],default=['replay','direct','anchor']);p.add_argument('--repeats',type=int,default=3)
    asyncio.run(run(p.parse_args()))
