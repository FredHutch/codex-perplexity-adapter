import asyncio
import copy
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.cli import build_parser
from codex_perplexity_adapter.request_size import (
    RequestTooLarge, encode_request, prepare_request_body, warning_level,
)
from codex_perplexity_adapter.status import BudgetStatus, StatusMiddleware


class StatusTests(unittest.TestCase):
    def test_authentication_and_no_auth_configuration(self):
        for token in ('local-adapter-token',''):
            with TestClient(create_app(Settings(api_key='unused',local_token=token))) as c:
                self.assertEqual(c.get('/v1/adapter/status').status_code,401)
                r=c.get('/v1/adapter/status',headers={'Authorization':'Bearer local-adapter-token'})
                self.assertEqual(r.status_code,200 if token else 401)
                if token:
                    self.assertEqual(r.headers['cache-control'],'no-store')
                    self.assertTrue(all(type(v) is int for v in r.json().values()))

    def test_usage_preserved_and_no_content_storage(self):
        secret='SYNTHETIC_SECRET_739185'
        seen=[]
        async def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200,json={'output':[],'usage':{'input_tokens':42,'output_tokens':2}})
        with tempfile.TemporaryDirectory() as d:
            before=set(Path(d).rglob('*'))
            previous=os.getcwd()
            os.chdir(d)
            self.addCleanup(os.chdir,previous)
            with patch('builtins.open', side_effect=AssertionError('Unexpected file access')), \
                 self.assertNoLogs('codex_perplexity_adapter',level='DEBUG'), \
                 TestClient(create_app(Settings(api_key=secret),transport=httpx.MockTransport(handler))) as c:
                headers={'Authorization':'Bearer local-adapter-token'}
                r=c.post('/v1/responses',headers=headers,json={'model':'gpt-6.1-sol','input':secret})
                self.assertEqual(r.json()['usage'],{'input_tokens':42,'output_tokens':2})
                status=c.get('/v1/adapter/status',headers=headers).json()
                self.assertEqual(status['active_requests'],0)
                self.assertEqual(status['latest_completed_request_bytes'],len(encode_request(seen[0])))
                self.assertNotIn(secret,json.dumps(status))
                self.assertEqual(seen[0]['input'],secret)
            self.assertEqual(before,set(Path(d).rglob('*')))
            os.chdir(previous)

    def test_size_failures_count_once_and_reset(self):
        h={'Authorization':'Bearer local-adapter-token'}
        for stream in (False, True):
            async def handler(request):
                error={'message':'request size 22000000 bytes exceeds the 20 MB downstream transport limit SYNTHETIC_SECRET'}
                if stream:
                    event={'type':'response.failed','response':{'status':'failed','error':error}}
                    return httpx.Response(200,text='data: '+json.dumps(event)+'\n\n',headers={'content-type':'text/event-stream'})
                return httpx.Response(413,json={'error':error})
            with TestClient(create_app(Settings(api_key='unused'),transport=httpx.MockTransport(handler))) as c:
                c.post('/v1/responses',headers=h,json={'model':'gpt-6.1-sol','input':'small','stream':stream})
                status=c.get('/v1/adapter/status',headers=h).json()
                self.assertEqual(status['size_failures'],1)
                self.assertEqual(status['active_requests'],0)
                self.assertNotIn('SECRET',json.dumps(status))
        with TestClient(create_app(Settings(api_key='unused'))) as c:
            self.assertTrue(all(value==0 for value in c.get('/v1/adapter/status',headers=h).json().values()))

    def test_mixed_custom_and_ordinary_images_without_injection(self):
        from codex_perplexity_adapter.transform import transform_request
        payload={'model':'gpt-6.1-sol','input':[
            {'type':kind,'call_id':'call_'+str(i),'output':[
                {'type':'input_text','text':'unicode é secret'},
                {'type':'input_image','image_url':'data:image/png;base64,TEST','detail':'original'}]}
            for i,kind in enumerate(('custom_tool_call_output','function_call_output'))]}
        original=copy.deepcopy(payload)
        expected,_=transform_request(payload,'openai/gpt-6.1-sol')
        async def handler(request):
            forwarded=json.loads(request.content)
            self.assertEqual(forwarded,expected)
            return httpx.Response(200,json={'output':[]})
        with TestClient(create_app(Settings(api_key='unused'),transport=httpx.MockTransport(handler))) as c:
            r=c.post('/v1/responses',headers={'Authorization':'Bearer local-adapter-token'},json=payload)
            self.assertEqual(r.status_code,200)
        self.assertEqual(payload,original)

    def test_upstream_connection_error_does_not_expose_credentials(self):
        async def handler(request):raise httpx.ConnectError('SYNTHETIC_SECRET_739185')
        with TestClient(create_app(Settings(api_key='unused'),transport=httpx.MockTransport(handler))) as c:
            h={'Authorization':'Bearer local-adapter-token'}
            r=c.post('/v1/responses',headers=h,json={'model':'gpt-6.1-sol','input':'private'})
            self.assertEqual(r.status_code,502)
            self.assertNotIn('SYNTHETIC_SECRET',r.text)
            self.assertEqual(c.get('/v1/adapter/status',headers=h).json()['active_requests'],0)


class LifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stream_lifetime_disconnect_closes_resources(self):
        entered=asyncio.Event()
        closed=asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"private"}\n\n'
                await asyncio.Event().wait()
            async def aclose(self):closed.set()
        async def upstream(request):
            return httpx.Response(200,stream=Stream(),headers={'content-type':'text/event-stream'})
        app=create_app(Settings(api_key='unused'),transport=httpx.MockTransport(upstream))
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1',
               'method':'POST','scheme':'http','path':'/v1/responses','raw_path':b'/v1/responses',
               'query_string':b'','headers':[(b'authorization',b'Bearer local-adapter-token')],
               'server':('localhost',80),'client':('localhost',123)}
        first=True
        disconnect=asyncio.Event()
        async def receive():
            nonlocal first
            if first:
                first=False
                return {'type':'http.request','body':json.dumps({'model':'gpt-6.1-sol','input':'secret','stream':True}).encode(),'more_body':False}
            await disconnect.wait()
            return {'type':'http.disconnect'}
        async def send(event):
            if event['type']=='http.response.body' and event.get('body'):entered.set()
        task=asyncio.create_task(app(scope,receive,send))
        await asyncio.wait_for(entered.wait(),3)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            h={'Authorization':'Bearer local-adapter-token'}
            status=(await c.get('/v1/adapter/status',headers=h)).json()
            self.assertEqual(status['active_requests'],1)
            disconnect.set()
            await asyncio.wait_for(task,3)
            self.assertTrue(closed.is_set())
            status=(await c.get('/v1/adapter/status',headers=h)).json()
            self.assertEqual(status['active_requests'],0)

    async def test_concurrent_requests_finish_and_cancel_without_retained_content(self):
        status=BudgetStatus()
        entered=asyncio.Queue()
        release=asyncio.Event()
        async def app(scope,receive,send):
            key=scope['state']['budget_request_id']
            status.active[key]=key*100
            await entered.put(key)
            await release.wait()
        middleware=StatusMiddleware(app,status)
        async def unused(*args):pass
        tasks=[asyncio.create_task(middleware({'type':'http','path':'/v1/responses'},unused,unused)) for _ in range(2)]
        await entered.get();await entered.get()
        self.assertEqual(status.snapshot()['active_requests'],2)
        self.assertEqual(status.snapshot()['largest_active_request_bytes'],200)
        tasks[0].cancel()
        with self.assertRaises(asyncio.CancelledError):await tasks[0]
        self.assertEqual(status.snapshot()['active_requests'],1)
        release.set();await tasks[1]
        self.assertEqual(status.active,{})
        self.assertEqual(status.latest,200)
