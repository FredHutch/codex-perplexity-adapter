import copy
import base64
import io
import json
import unittest
from unittest.mock import patch
import httpx
from PIL import Image
from fastapi.testclient import TestClient
from codex_perplexity_adapter.app import Settings
from codex_perplexity_adapter.continuation import canonical_item
from codex_perplexity_adapter.transform import transform_request
from research.tool_continuation import experiment, create_research_app, ResearchManager, ResearchCapture
from tests.test_continuation import StatefulProvider, message, answer, terminal, HEADERS
from scripts.qualify_continuation import image


class PermissiveProvider(StatefulProvider):
    allow_tool_references = False

    async def __call__(self,request):
        if self.allow_tool_references:
            self.tool_responses.clear()
        response = await super().__call__(request)
        if response.status_code == 200 and json.loads(request.content).get('stream'):
            result = json.loads(response.text.split('data: ', 1)[1])['response']
            events = [{'type': 'response.created', 'response': {**result, 'status': 'in_progress', 'output': []}}]
            for index, item in enumerate(result['output']):
                events.extend([{'type': 'response.output_item.added', 'output_index': index, 'item': item},
                               {'type': 'response.output_item.done', 'output_index': index, 'item': item}])
            events.append({'type': 'response.completed', 'response': result})
            data = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()
            class Chunks(httpx.AsyncByteStream):
                async def __aiter__(self):
                    for start in range(0, len(data), 31):
                        yield data[start:start + 31]
            return httpx.Response(200, stream=Chunks(), headers={'content-type': 'text/event-stream'})
        return response


class ResearchTests(unittest.TestCase):
    def setup_strategy(self,strategy,resize=False):
        self.provider=PermissiveProvider(self)
        self.provider.allow_tool_references = strategy == 'direct'
        ctx=experiment(strategy);ctx.__enter__();self.addCleanup(ctx.__exit__,None,None,None)
        self.app=create_research_app(strategy,Settings(api_key='synthetic',resize_images_on_limit=resize),httpx.MockTransport(self.provider))
        self.client=TestClient(self.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def send(self,history,stream,tools,**extra):
        payload={'model':'gpt-6.1-sol','input':history,'stream':stream,'tools':tools,**extra}
        before=copy.deepcopy(payload)
        normalized,_=transform_request(payload,'openai/gpt-6.1-sol')
        self.provider.expected=normalized['input']
        result=self.client.post('/v1/responses',headers=HEADERS,json=payload)
        self.assertEqual(payload,before)
        return result

    def test_loops_reconstruct_exactly(self):
        encoded = io.BytesIO()
        Image.new('RGB', (32, 24), (40, 50, 60)).save(encoded, 'JPEG')
        jpeg = 'data:image/jpeg;base64,' + base64.b64encode(encoded.getvalue()).decode()
        for strategy in ('replay','direct','anchor'):
          for stream in (False,True):
           for custom,namespace in ((False,False),(True,True)):
            for anchored in (False,True):
             with self.subTest(strategy=strategy,stream=stream,custom=custom,anchored=anchored):
              self.setup_strategy(strategy)
              tool={'type':'custom' if custom else 'function','name':'inspect'}
              if not custom:tool['parameters']={'type':'object','properties':{}}
              tools=[{'type':'namespace','name':'functions','tools':[tool]}] if namespace else [tool]
              history=[message('Remember OLD_MARKER')]
              anchor=None
              if anchored:
               out=terminal(self.send(history,stream,tools));history+=out['output'];anchor=out['id']
               history+=[message('Use tools')]
              for round_no in range(3):
               self.provider.output=[{'type':'reasoning','id':'rs_'+str(round_no),'summary':[], 'encrypted_content':'MOCK_REASONING'},answer('Inspecting',phase='commentary')] + [
                {'type':'function_call','id':f'fc_{round_no}_{i}','call_id':f'call_{round_no}_{i}','name':'functions__inspect' if namespace else 'inspect',
                 'arguments':'{"content":"inspect"}' if custom else '{}','status':'completed'} for i in range(2)]
               out=terminal(self.send(history,stream,tools))
               history+=out['output']
               history += [{'type':'custom_tool_call_output' if custom else 'function_call_output','call_id':f'call_{round_no}_{i}',
                 'output':[{'type':'input_text','text':f'ROUND_{round_no}_{i}'},{'type':'input_image','image_url':jpeg if i else image((20,30,40),padding=0),'detail':'original'}]} for i in range(2)]
               prior=self.provider.seen[-1].get('previous_response_id')
               if strategy=='anchor' and anchor:self.assertEqual(prior,anchor)
               if strategy=='direct' and round_no:self.assertIsNotNone(prior)
               if strategy=='replay' and round_no:self.assertIsNone(prior)
              self.provider.output=[answer('FINAL_MARKER')]
              final=terminal(self.send(history,stream,tools));history+=final['output']
              self.provider.output=[answer('FOLLOWUP')]
              self.send(history+[message('Next')],stream,tools)
              self.assertEqual(self.provider.seen[-1]['previous_response_id'],final['id'])
              self.doCleanups()

    def test_generic_error_no_replay_and_structured_fallback(self):
      for strategy in ('direct','anchor'):
       for stream in (False,True):
        for structured in (False,True):
         with self.subTest(strategy=strategy,stream=stream,structured=structured):
          self.setup_strategy(strategy)
          h=[message('start')];first=terminal(self.send(h,stream,[]));h+=first['output']+[message('next')]
          self.provider.failure=(400,{'error':{'code':'previous_response_not_found' if structured else 'invalid_request','message':'synthetic'}})
          before=len(self.provider.seen);r=self.send(h,stream,[])
          self.assertEqual(len(self.provider.seen)-before,2 if structured else 1)
          if structured:self.assertEqual(terminal(r)['status'],'completed')
          else:self.assertEqual(r.status_code,400)
          self.doCleanups()

    def test_failure_delivery_changes_and_isolation(self):
      for strategy in ('direct','anchor'):
       with self.subTest(strategy=strategy):
        manager=ResearchManager(strategy=strategy)
        key=manager.session_key('s','t'); other=manager.session_key('s','other')
        p={'input':[message('a')],'model':'x'}; fp=manager.fingerprints(p)
        manager.accept(key,{'id':'resp_ok','status':'completed'},fp,[answer()],p['input'])
        full={**p,'input':p['input']+[answer(),message('b')]}
        self.assertIn('previous_response_id',manager.select(key,full,manager.fingerprints(full)))
        self.assertNotIn('previous_response_id',manager.select(other,full,manager.fingerprints(full)))
        for changed in ({**full,'model':'y'},{**full,'input':[message('compacted')]},{**full,'input':[message('changed'),answer(),message('b')]}):
         manager.accept(key,{'id':'resp_ok','status':'completed'},fp,[answer()],p['input'])
         self.assertNotIn('previous_response_id',manager.select(key,changed,manager.fingerprints(changed)))
        for invalid,delivered,pending in ((False,False,True),(True,True,True),(False,True,False)):
         manager.accept(key,{'id':'resp_ok','status':'completed'},fp,[answer()],p['input'])
         cap=ResearchCapture(manager,key,fp,[],'x');cap.input_items=p['input'];cap.invalid=invalid
         if pending:cap.pending=({'id':'resp_new','status':'completed'},[answer()])
         cap.finalize(delivered);self.assertNotIn(key,manager.entries)
        manager.accept(key,{'id':'resp_ok','status':'completed'},fp,[answer()],p['input'])
        with patch.object(manager,'clock',return_value=manager.clock()+3601):
         self.assertNotIn('previous_response_id',manager.select(key,full,manager.fingerprints(full)))

    def test_oversized_selected_and_fallback_blocked(self):
      for strategy in ('direct','anchor'):
       with self.subTest(strategy=strategy):
        self.setup_strategy(strategy)
        h=[message('x'*19_000_000)]
        response=self.send(h,False,[])
        self.assertEqual(response.status_code,400);self.assertEqual(len(self.provider.seen),0)
        h=[message('start')];first=terminal(self.send(h,False,[]));h+=first['output']+[message('next')]
        self.provider.failure=(404,{'error':{'code':'previous_response_not_found'}})
        from codex_perplexity_adapter.request_size import prepare_request_body
        with patch('codex_perplexity_adapter.image_resize.prepare_request_body',lambda p:prepare_request_body(p,budget=180)):
         before=len(self.provider.seen);r=self.send(h,False,[])
         self.assertEqual(r.status_code,400);self.assertLessEqual(len(self.provider.seen)-before,1)
        self.doCleanups()

    def test_replay_image_control_exceeds_22mb_without_transmission(self):
        url = image((20, 30, 40), padding=17_000_000)
        from codex_perplexity_adapter.request_size import encode_request
        for stream in (False, True):
            with self.subTest(stream=stream):
                self.setup_strategy('replay')
                history = [{'role': 'user', 'content': [{'type': 'input_image', 'image_url': url}]}]
                full, _ = transform_request({'model': 'gpt-6.1-sol', 'input': history, 'stream': stream, 'tools': []}, 'openai/gpt-6.1-sol')
                self.assertGreater(len(encode_request(full)), 22_000_000)
                response = self.send(history, stream, [])
                if stream:
                    self.assertIn('response.failed', response.text)
                else:
                    self.assertEqual(response.status_code, 400)
                self.assertIn('context_length_exceeded', response.text)
                self.assertEqual(len(self.provider.seen), 0)
                self.doCleanups()

    def test_resized_anchor_tool_loop_binds_original_history(self):
        from tests.test_image_resize import fixture, payload
        from codex_perplexity_adapter.request_size import encode_request
        self.setup_strategy('anchor', resize=True)
        original = payload(fixture(), 32)
        original['tools'] = [{'type': 'function', 'name': 'inspect', 'parameters': {'type': 'object', 'properties': {}}}]
        history = copy.deepcopy(original['input'])
        def post():
            return terminal(self.client.post('/v1/responses', headers=HEADERS, json={**original, 'input': history}))
        first = post()
        self.assertEqual(first['status'], 'completed')
        self.assertNotEqual(self.provider.seen[0]['input'], history)
        self.assertEqual(history, original['input'])
        history += first['output'] + [message('inspect the next image')]
        self.provider.output = [{'type': 'function_call', 'name': 'inspect', 'call_id': 'call_next', 'arguments': '{}', 'status': 'completed'}]
        self.provider.expected = self.provider.saved[first['id']] + history[len(original['input']) + len(first['output']):]
        dispatch = post()
        self.assertEqual(self.provider.seen[-1]['previous_response_id'], first['id'])
        history += dispatch['output'] + [{'type': 'function_call_output', 'call_id': 'call_next', 'output': [{'type': 'input_image', 'image_url': image((80, 90, 100), 0)}]}]
        suffix = history[len(original['input']) + len(first['output']):]
        self.provider.expected = self.provider.saved[first['id']] + suffix
        self.provider.output = [answer('finished')]
        final = post()
        self.assertEqual(self.provider.seen[-1]['previous_response_id'], first['id'])
        self.assertLess(len(encode_request(self.provider.seen[-1])), 19_000_000)
        history += final['output'] + [message('follow up')]
        self.provider.expected = self.provider.saved[final['id']] + [history[-1]]
        post()
        self.assertEqual(self.provider.seen[-1]['previous_response_id'], final['id'])
        self.assertEqual(self.provider.seen[-1]['input'], [history[-1]])
        stats = self.client.get('/v1/adapter/status', headers=HEADERS).json()
        self.assertEqual(stats['image_resize_requests'], 1)
        self.assertGreater(stats['image_saved_request_bytes'], 0)
        self.assertGreater(stats['saved_request_bytes'], stats['image_saved_request_bytes'])

if __name__=='__main__':unittest.main()
