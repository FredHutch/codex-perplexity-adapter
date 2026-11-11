"""Dedicated, bounded source adapter for one synthetic desktop test chat."""
import json
import threading
from pathlib import Path
import sys
import uvicorn
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.request_size import encode_request
from scripts.qualify_continuation import Budget, MeteredTransport

root=Path(__file__).resolve().parents[1]/'build/qualification-0.1.13'
meter=MeteredTransport(Budget())
app=create_app(Settings(api_key='unused-local-relay',local_token='qualification-token'),meter)
records=[]
class Bounded:
 async def __call__(self,scope,receive,send):
  if scope.get('path')!='/v1/responses':return await app(scope,receive,send)
  chunks=b''
  while True:
   event=await receive()
   chunks+=event.get('body',b'')
   if not event.get('more_body',False):break
  body=json.loads(chunks)
  body['max_output_tokens']=512
  record={'input_items':len(body.get('input',[])), 'client_body_bytes':len(chunks)}
  sent=False
  async def once():
   nonlocal sent
   if not sent:
    sent=True
    return {'type':'http.request','body':encode_request(body),'more_body':False}
   return await receive()
  try:return await app(scope,once,send)
  finally:
   record['stats']=app.state.continuation.snapshot()
   records.append(record)
   (root/'isolated-desktop-traffic.json').write_text(json.dumps(records,indent=2)+chr(10))
print('Dedicated qualification adapter: http://127.0.0.1:45013/v1',flush=True)
uvicorn.run(Bounded(),host='127.0.0.1',port=45013,log_level='error',access_log=False)
