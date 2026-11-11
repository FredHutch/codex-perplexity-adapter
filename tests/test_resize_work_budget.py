import asyncio
import io
import json
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from codex_perplexity_adapter import image_resize as resize
from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.request_size import RequestTooLarge, encode_request
from scripts.work_budget_fixtures import many_images
from tests.test_image_resize import H, fixture, payload, response


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now


class BudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.url = fixture()

    def test_exact_decode_boundaries_and_failed_decode_charged(self):
        exact = resize.ResizeWorkBudget()
        exact.reserve_decode(499_999_999)
        exact.reserve_decode(1)
        with self.assertRaises(resize.WorkExhausted): exact.reserve_decode(1)
        self.assertEqual(exact.decoded_pixels, 500_000_000)
        pixels = 1600 * 2200
        for allowance in (pixels - 1, pixels, pixels + 1):
            work = resize.ResizeWorkBudget(pixel_limit=allowance)
            if allowance < pixels:
                with self.assertRaises(resize.WorkExhausted): resize._open_image(self.url, work)
                self.assertEqual(work.decoded_pixels, 0)
            else:
                image, _, _ = resize._open_image(self.url, work); image.close()
                self.assertEqual(work.decoded_pixels, pixels)
        work = resize.ResizeWorkBudget()
        with patch.object(Image.Image, 'load', side_effect=OSError('synthetic corrupt pixels')):
            self.assertIsNone(resize._open_image(self.url, work))
        self.assertEqual(work.decoded_pixels, pixels)

    def test_lazy_termination_closes_generator_and_source(self):
        p = payload(self.url, 22)
        closed, images = [], []
        original_open = resize._open_image
        def opening(*args):
            result = original_open(*args); images.append(result[0]); return result
        def candidates(*args):
            try:
                yield 'data:image/jpeg;base64,AA=='
                self.fail('generated an unnecessary candidate')
            finally: closed.append(True)
        with patch.object(resize, 'TARGET_BYTES', 19_000_000), patch.object(resize, '_open_image', opening), patch.object(resize, '_candidates', candidates):
            result = resize.resize_request(p, True)
        self.assertEqual(closed, [True]); self.assertEqual(result.resized_images, 1)
        with self.assertRaises(ValueError): images[0].getpixel((0, 0))

    def test_soft_deadline_retains_completed_candidate_and_active_only(self):
        clock = Clock(); work = resize.ResizeWorkBudget(clock=clock)
        work.begin(); clock.now = 10; work.end(); clock.now = 1000
        self.assertEqual(work.active_seconds(), 10)
        p = payload(self.url, 32)
        def candidates(*args):
            clock.now += 51
            yield 'data:image/jpeg;base64,AA=='
            work.check()
            self.fail('continued past deadline')
        with patch.object(resize, '_candidates', candidates):
            with self.assertRaises(RequestTooLarge) as error: resize.resize_request(p, True, work)
        self.assertEqual(work.stop_reason, 'time_budget')
        self.assertIn('time_budget', str(error.exception))
        self.assertEqual(error.exception.size, len(encode_request(p)) - len(self.url) + 27)
        self.assertEqual(work.elapsed, 61)

    def test_native_encode_can_finish_after_deadline_without_another_candidate(self):
        clock = Clock(); work = resize.ResizeWorkBudget(clock=clock)
        work.begin()
        source, _, mime = resize._open_image(self.url, work)
        original_save = Image.Image.save
        calls = []
        def save(image, *args, **kwargs):
            original_save(image, *args, **kwargs)
            calls.append(image.size); clock.now = 61
        try:
            generator = resize._candidates(source, mime, work)
            with patch.object(Image.Image, 'save', save):
                # The completed candidate survives a slow native encode.
                self.assertTrue(next(generator).startswith('data:image/jpeg;base64,'))
                with self.assertRaises(resize.WorkExhausted): next(generator)
            self.assertEqual(len(calls), 1)
        finally:
            generator.close(); source.close()

    def test_exhaustion_accepts_best_below_guard_json_sse_or_rejects(self):
        for stream in (False, True):
            for target in (18_500_000, 19_500_000):
                p = payload(self.url, 2, stream=stream, metadata={'padding': ''})
                p['metadata']['padding'] = 'x' * (target + 800_000 - len(encode_request(p)))
                candidate_len = len(self.url) - 800_000
                candidate = 'data:image/jpeg;base64,' + 'A' * (candidate_len - 23)
                seen = []
                def provider(r): seen.append(r.content); return response(stream)
                with patch.object(resize, 'MAX_REQUEST_PIXELS', 1600 * 2200), patch.object(resize, 'MAX_MEMO_BYTES', 1), patch.object(resize, '_candidates', side_effect=lambda *args: (x for x in [candidate])):
                    with TestClient(create_app(Settings(api_key='synthetic', resize_images_on_limit=True), httpx.MockTransport(provider))) as client:
                        r = client.post('/v1/responses', headers=H, json=p)
                if target <= 19_000_000:
                    self.assertEqual(len(seen), 1)
                    self.assertTrue(18_000_000 < len(seen[0]) <= 19_000_000)
                else:
                    self.assertFalse(seen); self.assertIn('context_length_exceeded', r.text)
                    self.assertIn('pixel_budget', r.text)

    def test_memo_hits_free_eviction_redecode_charged(self):
        p = payload(self.url, 32)
        for memo_limit, expected in ((32 * 1024 * 1024, 1600 * 2200), (1, 32 * 1600 * 2200)):
            work = resize.ResizeWorkBudget()
            with patch.object(resize, 'TARGET_BYTES', 1), patch.object(resize, 'MAX_MEMO_BYTES', memo_limit):
                resize.resize_request(p, True, work)
            self.assertEqual(work.decoded_pixels, expected)

    def test_memo_eviction_counts_the_next_decode_again(self):
        second = fixture(size=(1601, 2200))
        p = payload(self.url, 3, metadata={'padding': 'x' * 19_000_000})
        p['input'][1]['content'][0]['image_url'] = second
        for memo_limit, attempts in ((60, 3), (120, 2)):
            work = resize.ResizeWorkBudget()
            with patch.object(resize, 'MAX_MEMO_BYTES', memo_limit), patch.object(resize, '_candidates', side_effect=lambda *args: (x for x in ['data:image/jpeg;base64,AA=='])):
                with self.assertRaises(RequestTooLarge): resize.resize_request(p, True, work)
            self.assertEqual(work.decoded_pixels, (attempts - 1) * 1600 * 2200 + 1601 * 2200)

    def test_fifty_occurrences_old_budget_fails_new_fits(self):
        p = many_images(); original = encode_request(p)
        images = p['input'][-1]['output']
        self.assertEqual(len(images), 50)
        self.assertEqual(len({i['image_url'] for i in images}), 49)
        self.assertEqual(len(original), 22_900_000)
        with self.assertRaises(RequestTooLarge):
            resize.resize_request(p, True, resize.ResizeWorkBudget(pixel_limit=100_000_000))
        work = resize.ResizeWorkBudget()
        result = resize.resize_request(p, True, work)
        self.assertGreater(work.decoded_pixels, 100_000_000)
        self.assertLessEqual(len(result.body), 19_000_000)
        self.assertEqual(encode_request(p), original)

    def test_fallback_shares_allowance_retries_reuse_bytes(self):
        seen, budgets = [], []
        original = resize.resize_request
        def prepare(p, enabled, work):
            budgets.append(work)
            value = original(p, enabled, work)
            # Simulated work in the first selected body must remain charged.
            if len(budgets) == 1: work.elapsed += 7; work.decoded_pixels += 101
            return value
        def provider(request):
            seen.append(request.content)
            if len(seen) == 2: return httpx.Response(400, json={'error': {'param': 'previous_response_id'}})
            if len(seen) == 3: return httpx.Response(429, headers={'retry-after': '0'}, json={'error': 'rate'})
            return response()
        with TestClient(create_app(Settings(api_key='synthetic', resize_images_on_limit=True), httpx.MockTransport(provider))) as client:
            p = payload(self.url, 32)
            self.assertEqual(client.post('/v1/responses', headers=H, json=p).status_code, 200)
            p['input'].append({'role': 'user', 'content': 'followup'})
            with patch.object(resize, 'resize_request', prepare):
                self.assertEqual(client.post('/v1/responses', headers=H, json=p).status_code, 200)
        self.assertIs(budgets[0], budgets[1]); self.assertEqual(len(budgets), 2)
        self.assertGreaterEqual(budgets[1].elapsed, 7)
        self.assertGreater(budgets[1].decoded_pixels, 101)
        self.assertEqual(seen[2], seen[3])


class WorkerBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_time_excluded_and_cancel_signalled_until_native_done(self):
        entered, release = threading.Event(), threading.Event()
        clock = Clock(); first_work = resize.ResizeWorkBudget(clock=clock)
        queued_work = resize.ResizeWorkBudget(clock=clock)
        calls = []
        def blocked(p, enabled, work):
            work.begin(); calls.append(p); entered.set()
            if p == 1: release.wait(3)
            try: work.check_cancelled(); return p
            finally: work.end()
        resizer = resize.ImageResizer(True)
        with patch.object(resize, 'resize_request', blocked):
            first = asyncio.create_task(resizer.prepare(1, first_work))
            while not entered.is_set(): await asyncio.sleep(.01)
            second = asyncio.create_task(resizer.prepare(2, queued_work))
            clock.now = 100
            first.cancel()
            with self.assertRaises(asyncio.CancelledError): await first
            self.assertTrue(first_work.cancelled.is_set())
            await asyncio.sleep(.03); self.assertEqual(calls, [1])
            self.assertEqual(queued_work.active_seconds(), 0)
            release.set(); self.assertEqual(await second, 2)
            self.assertEqual(queued_work.elapsed, 0)

    async def test_http_disconnect_during_preparation_transmits_nothing(self):
        entered, release = threading.Event(), threading.Event()
        disconnected = asyncio.Event(); seen = []; workers = []
        def blocked(p, enabled, work):
            workers.append(work); entered.set(); release.wait(3)
            work.check_cancelled()
            return resize.PreparedRequest(b'{}', 20_000_000, 1)
        async def provider(request): seen.append(request); return response()
        app = create_app(Settings(api_key='synthetic', resize_images_on_limit=True), httpx.MockTransport(provider))
        body = encode_request({'model': 'gpt-6.1-sol', 'input': 'synthetic'})
        sent = False
        async def receive():
            nonlocal sent
            if not sent:
                sent = True; return {'type': 'http.request', 'body': body}
            await disconnected.wait(); return {'type': 'http.disconnect'}
        async def send(event): pass
        scope = {'type': 'http', 'method': 'POST', 'path': '/v1/responses', 'query_string': b'',
                 'headers': [(b'authorization', b'Bearer local-adapter-token')], 'scheme': 'http',
                 'server': ('127.0.0.1', 0), 'client': ('127.0.0.1', 1), 'http_version': '1.1'}
        with patch.object(resize, 'resize_request', blocked):
            task = asyncio.create_task(app(scope, receive, send))
            while not entered.is_set(): await asyncio.sleep(.01)
            disconnected.set()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertTrue(workers[0].cancelled.is_set())
            self.assertFalse(seen)
            release.set(); await asyncio.sleep(.05)
