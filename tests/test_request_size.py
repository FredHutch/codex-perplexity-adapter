import copy
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.request_size import (
    RequestTooLarge, encode_request, normalize_transport_error, prepare_request_body,
)

LIMIT_MESSAGE = 'request size 22070288 bytes exceeds the 20 MB downstream transport limit'


class RequestSizeTests(unittest.TestCase):
    def test_complete_utf8_size_and_exact_boundary(self):
        for field in ('input', 'instructions', 'tools', 'metadata'):
            with self.subTest(field=field):
                payload = {field: 'é☃' * 100}
                original = copy.deepcopy(payload)
                body = encode_request(payload)
                self.assertEqual(prepare_request_body(payload, budget=len(body)), body)
                with self.assertRaises(RequestTooLarge):
                    prepare_request_body(payload, budget=len(body)-1)
                self.assertEqual(payload, original)

    def test_images_and_tool_outputs_are_not_modified(self):
        payload = {'input': [
            {'type':'function_call','call_id':'call_1','name':'test','arguments':'{}'},
            {'type':'function_call_output','call_id':'call_1','output':[
                {'type':'input_text','text':'preserve'},
                {'type':'input_image','image_url':'data:image/png;base64,TEST','detail':'high'}]}]}
        self.assertEqual(json.loads(prepare_request_body(payload)), payload)
        with self.assertRaises(RequestTooLarge):
            prepare_request_body(payload,budget=10)

    def test_text_only_request_over_real_budget_is_rejected(self):
        payload = {'input':'x' * 22_070_288}
        with self.assertRaisesRegex(RequestTooLarge, '/compact'):
            prepare_request_body(payload)

    def test_oversized_text_rejected_before_upstream_for_json_and_stream(self):
        for stream in (False, True):
            async def handler(request):
                self.fail('Oversized request reached upstream')
            with patch('codex_perplexity_adapter.app.prepare_request_body',
                       side_effect=lambda payload, **kwargs: prepare_request_body(payload,budget=1000)):
                with TestClient(create_app(Settings(api_key='test'),transport=httpx.MockTransport(handler))) as client:
                    r = client.post('/v1/responses',json={'model':'gpt-6.1-sol','input':'x'*2000,'stream':stream},
                        headers={'Authorization':'Bearer local-adapter-token'})
            self.assertEqual(r.status_code,200 if stream else 400)
            if stream:
                events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith('data:')]
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['type'], 'response.failed')
                self.assertEqual(events[0]['response']['status'], 'failed')
                error = events[0]['response']['error']
            else:
                error = r.json()['error']
            self.assertEqual(error['code'],'context_length_exceeded')

    def test_downstream_limit_is_normalized_but_unrelated_errors_are_preserved(self):
        for status in (400, 413):
            for message in (LIMIT_MESSAGE, 'validation failed: unrelated error'):
                async def handler(request):
                    return httpx.Response(status,json={'error':{'message':message,'type':'invalid_request','code':status}})
                with TestClient(create_app(Settings(api_key='test'),transport=httpx.MockTransport(handler))) as client:
                    r=client.post('/v1/responses',json={'model':'gpt-6.1-sol','input':'small'},
                        headers={'Authorization':'Bearer local-adapter-token'})
                if message == LIMIT_MESSAGE:
                    self.assertEqual(r.status_code,400)
                    self.assertEqual(r.json()['error']['code'],'context_length_exceeded')
                    self.assertIn(LIMIT_MESSAGE,r.json()['error']['message'])
                else:
                    self.assertEqual(r.status_code,status)
                    self.assertEqual(r.json()['error']['code'],status)

    def test_stream_failure_remains_failure_with_recognizable_code(self):
        async def handler(request):
            event={'type':'response.failed','response':{'status':'failed','error':{
                'message':LIMIT_MESSAGE,'code':400,'type':'invalid_request'}}}
            return httpx.Response(200,text='data: '+json.dumps(event)+'\n\n',
                headers={'content-type':'text/event-stream'})
        with TestClient(create_app(Settings(api_key='test'),transport=httpx.MockTransport(handler))) as client:
            r=client.post('/v1/responses',json={'model':'gpt-6.1-sol','input':'small','stream':True},
                headers={'Authorization':'Bearer local-adapter-token'})
        event=next(json.loads(line[5:]) for line in r.text.splitlines() if line.startswith('data:'))
        self.assertEqual(event['type'],'response.failed')
        self.assertEqual(event['response']['status'],'failed')
        self.assertEqual(event['response']['error']['code'],'context_length_exceeded')

    def test_downstream_http_limit_becomes_failed_event_for_streaming_client(self):
        async def handler(request):
            return httpx.Response(400,json={'error':{'message':LIMIT_MESSAGE,'code':400}})
        with TestClient(create_app(Settings(api_key='test'),transport=httpx.MockTransport(handler))) as client:
            r=client.post('/v1/responses',json={'model':'gpt-6.1-sol','input':'small','stream':True},
                headers={'Authorization':'Bearer local-adapter-token'})
        self.assertEqual(r.status_code,200)
        self.assertIn('text/event-stream',r.headers['content-type'])
        events=[json.loads(line[5:]) for line in r.text.splitlines() if line.startswith('data:')]
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['type'],'response.failed')
        self.assertEqual(events[0]['response']['error']['code'],'context_length_exceeded')

    def test_small_json_and_stream_requests_send_exact_measured_body(self):
        for stream in (False,True):
            async def handler(request):
                body=request.content
                self.assertEqual(int(request.headers['content-length']),len(body))
                self.assertEqual(body,encode_request(json.loads(body)))
                response={'status':'completed','output':[]}
                if stream:
                    return httpx.Response(200,text='data: '+json.dumps({'type':'response.completed','response':response})+'\n\n',headers={'content-type':'text/event-stream'})
                return httpx.Response(200,json=response)
            with TestClient(create_app(Settings(api_key='test'),transport=httpx.MockTransport(handler))) as client:
                r=client.post('/v1/responses',json={'model':'gpt-6.1-sol','input':'é☃','stream':stream},
                    headers={'Authorization':'Bearer local-adapter-token'})
            self.assertEqual(r.status_code,200)
