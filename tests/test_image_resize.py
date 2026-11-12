import asyncio
import base64
import copy
import io
import json
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.cli import build_parser
from codex_perplexity_adapter.image_resize import ImageResizer, PreparedRequest, resize_request, _candidates
from codex_perplexity_adapter.request_size import encode_request, RequestTooLarge

H = {"Authorization": "Bearer local-adapter-token", "session-id": "synthetic", "thread-id": "resize"}

def fixture(fmt="JPEG", size=(1600, 2200), **save):
    image = Image.new("RGBA" if fmt == "PNG" else "RGB", size, (255, 255, 255, 128) if fmt == "PNG" else "white")
    draw = ImageDraw.Draw(image)
    for y in range(40, size[1] - 20, 35):
        draw.text((40, y), "FORM 12345 / value 1.25 / [ ] No [X] Yes / ABCDEFGHIJKLMNOPQRSTUVWXYZ", fill="black", font_size=20)
    out = io.BytesIO()
    image.save(out, fmt, **({"quality": 90} if fmt == "JPEG" else {}), **save)
    return "data:image/" + ("jpeg" if fmt == "JPEG" else "png") + ";base64," + base64.b64encode(out.getvalue()).decode()

def decoded(url):
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))

def payload(url, count=1, **extra):
    return {"model": "gpt-6.1-sol", "input": [
        {"role": "user", "content": [{"type": "input_image", "image_url": url, "detail": "high"}]}
        for _ in range(count)], **extra}

def response(stream=False, status="completed", output=None):
    obj = {"id": "resp_resize", "status": status, "output": output if output is not None else []}
    if stream:
        return httpx.Response(200, text="data: " + json.dumps({"type": "response." + status, "response": obj}) + "\n\n", headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json=obj)


class ResizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jpeg = fixture()
        cls.png = fixture("PNG")

    def test_defaults_and_exact_guard_untouched(self):
        self.assertTrue(build_parser().parse_args([]).resize_images_on_limit)
        self.assertTrue(Settings(api_key="synthetic").resize_images_on_limit)
        self.assertFalse(build_parser().parse_args(["--no-resize-images-on-limit"]).resize_images_on_limit)
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--resize-images-on-limit", "--no-resize-images-on-limit"])
        self.assertTrue(build_parser().parse_args(["--resize-images-on-limit"]).resize_images_on_limit)
        for size in (18_000_001, 18_999_999, 19_000_000, 19_000_001):
            p = payload(self.jpeg, metadata={"padding": "é"})
            padding = size - len(encode_request(p))
            p["metadata"]["padding"] += "x" * padding
            self.assertEqual(len(encode_request(p)), size)
            if size <= 19_000_000:
                with patch("codex_perplexity_adapter.image_resize._open_image", side_effect=AssertionError("unnecessary decode")):
                    self.assertEqual(resize_request(p, True).body, encode_request(p))
            else:
                with self.assertRaises(RequestTooLarge):
                    resize_request(p, False)
                self.assertLessEqual(len(resize_request(p, True).body), 19_000_000)

    def test_substantial_recovery_oldest_first_and_original_preserved(self):
        p = payload(self.jpeg, 32)
        before = copy.deepcopy(p)
        result = resize_request(p, True)
        self.assertGreater(result.original_size, 28_000_000)
        self.assertLessEqual(len(result.body), 18_000_000)
        parts = json.loads(result.body)["input"]
        self.assertNotEqual(parts[0], p["input"][0])
        self.assertEqual(parts[-1], p["input"][-1])
        self.assertEqual(p, before)
        sizes = [decoded(i["content"][0]["image_url"]).size for i in parts]
        self.assertTrue(all(max(size) >= 1024 for size in sizes))
        self.assertEqual(result.body, resize_request(p, True).body)

    def test_formats_transparency_orientation_and_metadata(self):
        exif = Image.Exif(); exif[274] = 6; exif[256] = 1600; exif[257] = 2200; exif[305] = "synthetic"
        url = fixture(exif=exif, icc_profile=b"synthetic-profile")
        for original in (url, self.png):
            p = payload(original)
            with patch("codex_perplexity_adapter.image_resize.REQUEST_BUDGET_BYTES", 1000), patch("codex_perplexity_adapter.image_resize.TARGET_BYTES", 1000):
                result = resize_request(p, True)
            candidate = decoded(json.loads(result.body)["input"][0]["content"][0]["image_url"])
            if original == url:
                self.assertEqual(max(candidate.size), 1024)
                self.assertGreater(candidate.width, candidate.height)
                self.assertNotIn(274, candidate.getexif())
                self.assertNotIn(256, candidate.getexif())
                self.assertEqual(candidate.getexif()[305], "synthetic")
                self.assertEqual(candidate.info["icc_profile"], b"synthetic-profile")
                self.assertEqual(candidate.format, "JPEG")
            else:
                # This text PNG grows when smoothed; inspect the candidate
                # directly to verify alpha, and retain original on the wire.
                with decoded(original) as image:
                    candidate = decoded(list(_candidates(image, "PNG"))[-1])
                self.assertEqual(candidate.format, "PNG")
                self.assertEqual(candidate.mode, "RGBA")
                self.assertEqual(candidate.getpixel((0, 0))[3], 128)

    def test_skips_and_scope(self):
        p = payload(self.jpeg)
        p["input"] += [{"role": "assistant", "content": copy.deepcopy(p["input"][0]["content"])},
            {"type": "function_call", "arguments": self.jpeg, "call_id": "c"}]
        with patch("codex_perplexity_adapter.image_resize.REQUEST_BUDGET_BYTES", 1000), patch("codex_perplexity_adapter.image_resize.TARGET_BYTES", 1000):
            result = json.loads(resize_request(p, True).body)
        self.assertEqual(result["input"][1:], p["input"][1:])
        for url in ("https://example.invalid/a.jpg", "data:image/jpeg;base64,INVALID", fixture(size=(100, 100))):
            p = payload(url, metadata={"padding": "x" * 19_000_000})
            with self.assertRaises(RequestTooLarge):
                resize_request(p, True)
        with patch("codex_perplexity_adapter.image_resize.MAX_REQUEST_PIXELS", 1):
            with self.assertRaises(RequestTooLarge):
                resize_request(payload(self.jpeg, 32), True)

    def test_growing_png_never_replaces_and_cannot_fit(self):
        p = payload(self.png)
        with patch("codex_perplexity_adapter.image_resize.REQUEST_BUDGET_BYTES", 1), patch("codex_perplexity_adapter.image_resize._candidates", side_effect=lambda *args: (x for x in [self.png + "A"])):
            self.assertEqual(resize_request(p, True).body, encode_request(p))

    def test_json_sse_counters_without_continuation_and_failures(self):
        for stream in (False, True):
            for status in ("completed", "failed", "incomplete"):
                seen = []
                def provider(request):
                    seen.append(request.content)
                    return response(stream, status)
                app = create_app(Settings(api_key="synthetic", continuation="disabled", resize_images_on_limit=True), httpx.MockTransport(provider))
                with TestClient(app) as client:
                    r = client.post("/v1/responses", headers=H, json=payload(self.jpeg, 32, stream=stream))
                    self.assertEqual(r.status_code, 200)
                    self.assertLessEqual(len(seen[0]), 18_000_000)
                    stats = client.get("/v1/adapter/status", headers=H).json()
                    self.assertEqual(stats["image_resize_requests"], int(status == "completed"))
                    self.assertEqual(stats["saved_request_bytes"], 0)
                    self.assertEqual(stats["image_saved_request_bytes"] > 0, status == "completed")

    def test_resumed_continuation_uses_original_fingerprints_and_fallback(self):
        seen = []
        reject = False
        def provider(request):
            p = json.loads(request.content); seen.append(p)
            if reject and "previous_response_id" in p:
                return httpx.Response(400, json={"error": {"param": "previous_response_id"}})
            return response()
        with TestClient(create_app(Settings(api_key="synthetic", resize_images_on_limit=True), httpx.MockTransport(provider))) as client:
            p = payload(self.jpeg, 32)
            client.post("/v1/responses", headers=H, json=p)
            p["input"].append({"role": "user", "content": "next"})
            client.post("/v1/responses", headers=H, json=p)
            self.assertEqual(len(seen[-1]["input"]), 1)
            self.assertIn("previous_response_id", seen[-1])
            reject = True
            p["input"].append({"role": "user", "content": "third"})
            client.post("/v1/responses", headers=H, json=p)
            self.assertNotIn("previous_response_id", seen[-1])
            self.assertLessEqual(len(encode_request(seen[-1])), 18_000_000)
            self.assertNotEqual(seen[-1]["input"][0], p["input"][0])
            stats = client.get("/v1/adapter/status", headers=H).json()
            self.assertEqual(stats["image_resize_requests"], 2)
            self.assertGreater(stats["saved_request_bytes"], 28_000_000)

    def test_parallel_custom_namespaced_tool_replay_and_explicit_continuation(self):
        seen, bodies = [], []
        def provider(request):
            bodies.append(request.content)
            p = json.loads(request.content); seen.append(p)
            return response(bool(p.get("stream")))
        settings = Settings(api_key="synthetic", resize_images_on_limit=True)
        with TestClient(create_app(settings, httpx.MockTransport(provider))) as client:
            p = payload(self.jpeg, 32, stream=True)
            images = p.pop("input")
            p["input"] = [{"type": "custom_tool_call", "call_id": "a", "name": "read", "namespace": "files", "input": "original-arguments"},
                {"type": "function_call", "call_id": "b", "name": "read", "arguments": "{}"},
                {"type": "custom_tool_call_output", "call_id": "a", "output": [i["content"][0] for i in images[:16]]},
                {"type": "function_call_output", "call_id": "b", "output": [i["content"][0] for i in images[16:]]}]
            client.post("/v1/responses", headers=H, json=p)
            self.assertNotIn("previous_response_id", seen[-1])
            self.assertEqual([i["call_id"] for i in seen[-1]["input"]], ["a", "b", "a", "b"])
            self.assertIn("original-arguments", seen[-1]["input"][0]["arguments"])
            self.assertLessEqual(len(bodies[-1]), 18_000_000)
            p["input"].append({"role": "user", "content": "followup"})
            client.post("/v1/responses", headers=H, json=p)
            self.assertIn("previous_response_id", seen[-1])
            p = payload(self.jpeg, 32, previous_response_id="explicit", store=False)
            client.post("/v1/responses", headers=H, json=p)
            self.assertEqual(seen[-1]["previous_response_id"], "explicit")
            self.assertFalse(seen[-1]["store"])

    def test_partial_recovery_below_guard_and_resource_skips(self):
        p = payload(self.jpeg, 32)
        with patch("codex_perplexity_adapter.image_resize.TARGET_BYTES", 1):
            floor_size = len(resize_request(p, True).body)
            p["metadata"] = {"padding": "x" * (18_500_000 - floor_size - len(encode_request({"padding": ""})) - 15)}
            result = resize_request(p, True)
            self.assertGreater(len(result.body), 18_000_000)
            self.assertLessEqual(len(result.body), 19_000_000)
        for limit in ("MAX_ENCODED_BYTES", "MAX_IMAGE_PIXELS"):
            with patch("codex_perplexity_adapter.image_resize." + limit, 1):
                with self.assertRaises(RequestTooLarge): resize_request(payload(self.jpeg, 32), True)

    def test_counters_not_credited_after_delivery_cancellation(self):
        from codex_perplexity_adapter.app import ContinuationCapture
        from codex_perplexity_adapter.continuation import ContinuationManager
        from codex_perplexity_adapter.status import BudgetStatus
        status = BudgetStatus()
        capture = ContinuationCapture(ContinuationManager(), None, None, [], "openai/gpt-6.1-sol", status)
        capture.image_prepared = PreparedRequest(b"small", 100, 2)
        capture.commit({"id": "resp_test", "status": "completed", "output": []})
        capture.finalize(False)
        self.assertEqual(status.snapshot()["image_saved_request_bytes"], 0)

    def test_grayscale_progressive_palette_and_color_key_transparency(self):
        from codex_perplexity_adapter.image_resize import _open_image
        for mode, fmt, options in (("L", "JPEG", {"progressive": True}),
                                    ("P", "PNG", {"transparency": 0}),
                                    ("RGB", "PNG", {"transparency": (0, 0, 0)})):
            im = Image.new(mode, (1400, 1200)); data = io.BytesIO(); im.save(data, fmt, **options)
            url = "data:image/" + ("jpeg" if fmt == "JPEG" else "png") + ";base64," + base64.b64encode(data.getvalue()).decode()
            opened = _open_image(url, 100_000_000)
            with opened[0] as source:
                result = decoded(list(_candidates(source, fmt))[-1])
            self.assertEqual(result.size, (1024, 878))
            if fmt == "PNG":
                self.assertEqual(result.mode, "RGBA")
                self.assertEqual(result.getpixel((0, 0))[3], 0)

    def test_malformed_cmyk_and_unsupported_metadata_pass_unchanged(self):
        from codex_perplexity_adapter.image_resize import _open_image
        im = Image.new("CMYK", (1400, 1200)); out = io.BytesIO(); im.save(out, "JPEG")
        cmyk = "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()
        self.assertIsNone(_open_image(cmyk, 100_000_000))
        raw = base64.b64decode(self.jpeg.split(",", 1)[1])
        truncated = "data:image/jpeg;base64," + base64.b64encode(raw[:len(raw)//2]).decode()
        self.assertIsNone(_open_image(truncated, 100_000_000))
        raw = raw[:2] + b"\xff\xe1\x00\x0bUNKNOWN!!" + raw[2:]
        unsupported = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        self.assertIsNone(_open_image(unsupported, 100_000_000))

    def test_stateful_provider_reconstruction_after_resized_turn(self):
        saved, seen = {}, []
        def provider(request):
            p = json.loads(request.content); seen.append(p)
            history = saved[p["previous_response_id"]] + p["input"] if "previous_response_id" in p else p["input"]
            if len(seen) > 1:
                self.assertEqual(history[:-1], saved["resp_1"])
                self.assertEqual(history[-1]["content"], "retained marker")
            ident = "resp_" + str(len(seen)); saved[ident] = history
            return httpx.Response(200, json={"id": ident, "status": "completed", "output": []})
        with TestClient(create_app(Settings(api_key="synthetic", resize_images_on_limit=True), httpx.MockTransport(provider))) as c:
            p = payload(self.jpeg, 32)
            c.post("/v1/responses", headers=H, json=p)
            self.assertNotEqual(saved["resp_1"][0]["content"][0]["image_url"], self.jpeg)
            p["input"].append({"role": "user", "content": "retained marker"})
            c.post("/v1/responses", headers=H, json=p)
            self.assertEqual(len(seen[-1]["input"]), 1)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_keeps_worker_busy_and_loop_responsive(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def blocked(payload, enabled, work):
            calls.append(payload); entered.set(); release.wait(3)
            return payload
        resizer = ImageResizer(True)
        with patch("codex_perplexity_adapter.image_resize.resize_request", blocked):
            first = asyncio.create_task(resizer.prepare(1))
            while not entered.is_set():
                await asyncio.sleep(.01)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError): await first
            second = asyncio.create_task(resizer.prepare(2))
            await asyncio.sleep(.05)
            self.assertEqual(calls, [1])
            release.set()
            self.assertEqual(await second, 2)
