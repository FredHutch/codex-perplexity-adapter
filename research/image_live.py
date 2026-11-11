"""Stage image transport qualification only for a surviving basic strategy."""
import argparse
import asyncio
import json
from research.live import Session,msg,text
from research.fixtures import parts,VALUES
from research.budget import OUT,LiveBudget

async def chain(strategy,stream,fixture,repeat):
    s=Session(strategy,stream,False)
    result={'strategy':strategy,'stream':stream,'fixture':fixture,'repeat':repeat,'checks':s.results,'passed':False}
    try:
        images=parts(repeat*2,16_400_000,fixture=='realistic')
        r=await s.call('image-anchor',[{'type':'message','role':'user','content':[{'type':'input_text','text':'These are synthetic test pages. Remember their visual contents for a later question. Reply ACK only, without tools.'},*images]}])
        if r.get('status')!='completed':return result
        result['initial_body_in_range']=16_000_000<=s.results[-1]['full_body_bytes']<=17_000_000
        if not result['initial_body_in_range']:return result
        r=await s.call('start-image-tools',[msg('Call probe with round 1. After each result call probe with the next round until round 3 has returned. Then finish by listing the Page ID, Dose, and colored rectangle color from the earliest page and from each new page returned by the tool. Do not invent values. Do not call further tools after round 3.')])
        for round_no in range(1,4):
            calls=[i for i in r.get('output',[]) if i.get('type')=='function_call']
            if r.get('status')!='completed' or not calls:return result
            output=[]
            for i,c in enumerate(calls):
                index=repeat*2+round_no
                ip=parts(index,2_500_000,False)
                output.append({'type':'function_call_output','call_id':c['call_id'],'output':[{'type':'input_text','text':f'Synthetic round {round_no} complete. '+('Continue to next round.' if round_no<3 else 'All rounds complete. Give the requested visual values now.')},*ip]})
            r=await s.call(f'image-tool-result-{round_no}',output)
            if r.get('status')!='completed':return result
        result['exceeded_22mb']=s.results[-1]['full_body_bytes']>22_000_000
        expected=VALUES[repeat*2%len(VALUES)]
        required=[expected]+[VALUES[(repeat*2+i)%len(VALUES)] for i in (1,2,3)]
        result['visual_checks']=[all(v.lower() in text(r).lower() for v in vals) for vals in required]
        result['passed']=result['exceeded_22mb'] and all(result['visual_checks']) and not any(i.get('type')=='function_call' for i in r.get('output',[]))
        if result['passed']:
            r=await s.call('image-ordinary-followup',[msg('Without tools repeat the earliest Page ID and Dose.')])
            result['followup_visual_retained']=all(v in text(r) for v in expected[:2])
            result['passed']=r.get('status')=='completed' and result['followup_visual_retained']
    finally:await s.close()
    return result

async def run(a):
    path=OUT/'image-live.json';results=json.loads(path.read_text()) if path.exists() else []
    for strategy in a.strategies:
      for fixture in ('transport','realistic'):
       for repeat in range(2):
        stream=bool(repeat)
        result=await chain(strategy,stream,fixture,repeat);results.append(result)
        path.write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps({'strategy':strategy,'fixture':fixture,'repeat':repeat,'passed':result['passed'],'budget':LiveBudget().summary()}),flush=True)
        if not result['passed']:break
       if not result['passed']:break
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--strategies',nargs='+',required=True);asyncio.run(run(p.parse_args()))
