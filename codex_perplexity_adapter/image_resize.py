"""Request-local, bounded image resizing after continuation selection."""
from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import io
import math
import hashlib
import logging
import threading
import time
import warnings
from dataclasses import dataclass, field
from collections import OrderedDict
from contextlib import ExitStack, closing

from PIL import Image, ImageOps, JpegImagePlugin, PngImagePlugin

from .request_size import REQUEST_BUDGET_BYTES, RequestTooLarge, encode_request, prepare_request_body

TARGET_BYTES = 18_000_000
MIN_LONG_EDGE = 1024
MAX_ENCODED_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_REQUEST_PIXELS = 500_000_000
MAX_PREPARATION_SECONDS = 60.0
MAX_MEMO_BYTES = 32 * 1024 * 1024
RESIZE_POLICY = "oldest-first-v2:0.85:1024:18000000:jpeg85:keep-format:decode500m:soft60s"
LOGGER = logging.getLogger("uvicorn.error.image_resize")


class ResizeCancelled(Exception):
    pass


class WorkExhausted(Exception):
    pass


@dataclass
class ResizeWorkBudget:
    """One client's allowances, active only while its worker prepares bytes."""
    pixel_limit: int = field(default_factory=lambda: MAX_REQUEST_PIXELS)
    seconds_limit: float = field(default_factory=lambda: MAX_PREPARATION_SECONDS)
    clock: object = time.monotonic
    decoded_pixels: int = 0
    elapsed: float = 0.0
    stop_reason: str | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    started: float | None = None

    def begin(self):
        self.started = self.clock()

    def end(self):
        if self.started is not None:
            self.elapsed += self.clock() - self.started
            self.started = None

    def active_seconds(self):
        return self.elapsed + (self.clock() - self.started if self.started is not None else 0)

    def check_cancelled(self):
        if self.cancelled.is_set():
            self.stop_reason = "cancelled"
            raise ResizeCancelled()

    def check(self):
        self.check_cancelled()
        if self.stop_reason or self.active_seconds() >= self.seconds_limit:
            self.stop_reason = self.stop_reason or "time_budget"
            raise WorkExhausted(self.stop_reason)

    def reserve_decode(self, pixels):
        self.check()
        if pixels > self.pixel_limit - self.decoded_pixels:
            self.stop_reason = "pixel_budget"
            raise WorkExhausted(self.stop_reason)
        self.decoded_pixels += pixels


@dataclass(frozen=True)
class PreparedRequest:
    body: bytes
    original_size: int
    resized_images: int = 0

    @property
    def saved_bytes(self):
        return self.original_size - len(self.body)


def image_parts(payload):
    """Visit only actual user/tool image blocks, never arbitrary nested JSON."""
    for item in payload.get("input", []) if isinstance(payload.get("input"), list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type", "message") == "message" and item.get("role") == "user":
            parts = item.get("content")
        elif item.get("type") == "function_call_output":
            parts = item.get("output")
        else:
            continue
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "input_image":
                    yield part


def _open_image(url, work):
    if isinstance(work, int):
        work = ResizeWorkBudget(pixel_limit=work)
    work.check()
    if not isinstance(url, str):
        return None
    prefix, separator, encoded = url.partition(",")
    mime = {"data:image/jpeg;base64": "JPEG", "data:image/png;base64": "PNG"}.get(prefix)
    if not separator or mime is None or len(encoded) > MAX_ENCODED_BYTES:
        return None
    image = None
    try:
        raw = base64.b64decode(encoded, validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(raw))
        pixels = image.width * image.height
        if (image.format != mime or image.mode not in {"RGB", "L", "RGBA", "LA", "P"}
                or getattr(image, "n_frames", 1) != 1
                or pixels > MAX_IMAGE_PIXELS
                or max(image.size) <= MIN_LONG_EDGE):
            image.close()
            return None
        # Unknown APP records can contain HDR gain maps, MPF or proprietary
        # display metadata. Do not silently discard them.
        if mime == "JPEG":
            if image.mode not in {"RGB", "L"} or any(
                marker.startswith("APP") and not (
                    (marker == "APP0" and data.startswith(b"JFIF\0"))
                    or (marker == "APP1" and data.startswith(b"Exif\0\0"))
                    or (marker == "APP2" and data.startswith(b"ICC_PROFILE\0")))
                for marker, data in image.applist
            ):
                image.close()
                return None
        elif getattr(image, "private_chunks", []):
            image.close()
            return None
        work.reserve_decode(pixels)
        image.load()  # Strict decoding; never enable LOAD_TRUNCATED_IMAGES.
        work.check()
        return image, pixels, mime
    except (ValueError, OSError, SyntaxError, binascii.Error, Image.DecompressionBombWarning, Image.DecompressionBombError):
        if image is not None:
            image.close()
        return None
    except BaseException:
        if image is not None:
            image.close()
        raise


def _candidates(image, mime, work=None):
    work = work or ResizeWorkBudget()
    work.check()
    sampling = JpegImagePlugin.get_sampling(image) if mime == "JPEG" else None
    info = image.info.copy()
    exif = Image.Exif()
    exif.load(image.getexif().tobytes())
    # IFD1 thumbnails and dimension tags describe the source, not this copy.
    exif.pop(0x0112, None)
    for tag in (0x0100, 0x0101, 0x0201, 0x0202):
        exif.pop(tag, None)
    if 0x8769 in exif:
        nested = exif.get_ifd(0x8769)
        for tag in (0xA002, 0xA003):
            nested.pop(tag, None)
    # Sanitizing a separate EXIF instance leaves the source orientation intact.
    with closing(ImageOps.exif_transpose(image)) as oriented, ExitStack() as stack:
        if oriented.mode == "P" or (mime == "PNG" and "transparency" in info):
            oriented = stack.enter_context(closing(oriented.convert("RGBA" if "transparency" in info else "RGB")))
        edge = max(oriented.size)
        while edge > MIN_LONG_EDGE:
            work.check()
            edge = max(MIN_LONG_EDGE, math.floor(edge * 0.85))
            scale = edge / max(oriented.size)
            size = tuple(max(1, round(d * scale)) for d in oriented.size)
            with closing(oriented.resize(size, Image.Resampling.LANCZOS)) as resized:
                work.check()
                options = {"icc_profile": info.get("icc_profile"), "exif": exif.tobytes(),
                           "dpi": info.get("dpi", (0, 0))}
                if mime == "JPEG":
                    options.update(quality=85, optimize=True, subsampling=sampling)
                else:
                    options["optimize"] = True
                    pnginfo = PngImagePlugin.PngInfo()
                    for key, value in getattr(image, "text", {}).items():
                        # XMP may contain stale geometry/orientation.
                        if "xmp" in key.lower():
                            raise ValueError("unsupported XMP metadata")
                        pnginfo.add_itxt(key, value)
                    for key in ("srgb", "gamma", "chromaticity"):
                        if key in info:
                            # Pillow does not roundtrip these via save kwargs.
                            raise ValueError("unsupported PNG color metadata")
                    options["pnginfo"] = pnginfo
                    if "transparency" in info and resized.mode not in {"RGBA", "LA"}:
                        options["transparency"] = info["transparency"]
                out = io.BytesIO()
                resized.save(out, mime, **options)
                yield ("data:image/jpeg;base64," if mime == "JPEG" else "data:image/png;base64,") + base64.b64encode(out.getvalue()).decode("ascii")


def resize_request(payload, enabled=False, work=None):
    work = work or ResizeWorkBudget()
    work.begin()
    original_size = final_size = changed = processed = skipped = 0
    stop = "unchanged"
    try:
        work.check_cancelled()
        original = encode_request(payload)
        original_size = final_size = len(original)
        if len(original) <= REQUEST_BUDGET_BYTES or not enabled:
            return PreparedRequest(prepare_request_body(payload), len(original))
        working = copy.deepcopy(payload)
        current_size = len(original)
        memo = OrderedDict()
        memo_bytes = 0
        stop = "images_exhausted"
        try:
            for part in image_parts(working):
                if current_size <= TARGET_BYTES:
                    stop = "target_reached"
                    break
                work.check()
                url = part.get("image_url")
                if not isinstance(url, str):
                    skipped += 1
                    continue
                # Fixed-size keys do not retain another copy of large URLs.
                key = hashlib.sha256(url.encode("utf-8")).digest()
                best = url
                if key in memo:
                    candidates = memo[key]
                    memo.move_to_end(key)
                    for candidate in candidates:
                        if len(candidate) < len(best):
                            current_size += len(candidate) - len(best)
                            best = candidate
                        if current_size <= TARGET_BYTES:
                            break
                else:
                    opened = _open_image(url, work)
                    if opened is None:
                        skipped += 1
                        candidates = []
                    else:
                        image, _, mime = opened
                        processed += 1
                        candidates = []
                        candidate_bytes = 0
                        try:
                            with closing(image):
                                with closing(_candidates(image, mime, work)) as generator:
                                    for candidate in generator:
                                        work.check_cancelled()
                                        if len(candidate) < len(best):
                                            current_size += len(candidate) - len(best)
                                            best = candidate
                                            if candidates is not None and len(key) + candidate_bytes + len(candidate) <= MAX_MEMO_BYTES:
                                                while memo and memo_bytes + len(key) + candidate_bytes + len(candidate) > MAX_MEMO_BYTES:
                                                    old_key, old_candidates = memo.popitem(last=False)
                                                    memo_bytes -= len(old_key) + sum(map(len, old_candidates))
                                                candidates.append(candidate)
                                                candidate_bytes += len(candidate)
                                            else:
                                                candidates = None
                                        if current_size <= TARGET_BYTES:
                                            break
                        except (OSError, ValueError, SyntaxError, TypeError, KeyError, OverflowError):
                            # Never partly keep a metadata variant we cannot handle.
                            current_size += len(url) - len(best)
                            best, candidates = url, []
                        finally:
                            if best != url:
                                part["image_url"] = best
                                changed += 1
                    if candidates is not None:
                        cost = len(key) + sum(map(len, candidates))
                        while memo and memo_bytes + cost > MAX_MEMO_BYTES:
                            old_key, old_candidates = memo.popitem(last=False)
                            memo_bytes -= len(old_key) + sum(map(len, old_candidates))
                        if cost <= MAX_MEMO_BYTES:
                            memo[key] = candidates
                            memo_bytes += cost
                    continue
                if best != url:
                    part["image_url"] = best
                    changed += 1
        except WorkExhausted:
            stop = work.stop_reason
        if current_size <= TARGET_BYTES:
            stop = "target_reached"
        work.check_cancelled()
        # Always re-measure complete UTF-8 JSON, including after exhaustion.
        final_size = len(encode_request(working))
        try:
            body = prepare_request_body(working)
        except RequestTooLarge as exc:
            if work.stop_reason:
                raise RequestTooLarge(str(exc) + f" Image resizing stopped: {work.stop_reason}; "
                    f"decoded pixels {work.decoded_pixels}/{work.pixel_limit}; "
                    f"active preparation {work.active_seconds():.2f}/{work.seconds_limit:.0f} seconds.", exc.size) from exc
            raise
        work.check_cancelled()
        return PreparedRequest(body, original_size, changed)
    finally:
        work.end()
        if enabled and original_size > REQUEST_BUDGET_BYTES:
            LOGGER.info("image_preparation elapsed_seconds=%.3f decoded_pixels=%d processed_images=%d "
                        "skipped_images=%d original_bytes=%d final_bytes=%d stop_reason=%s",
                        work.elapsed, work.decoded_pixels, processed, skipped,
                        original_size, final_size, work.stop_reason or stop)


class ImageResizer:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.slot = asyncio.Semaphore(1)

    async def prepare(self, payload, work=None):
        work = work or ResizeWorkBudget()
        await self.slot.acquire()
        cancelled = object()
        def run():
            try:
                return resize_request(payload, self.enabled, work)
            except ResizeCancelled:
                return cancelled
        try:
            future = asyncio.get_running_loop().run_in_executor(None, run)
        except BaseException:
            self.slot.release()
            raise
        def finished(done):
            self.slot.release()
            if not done.cancelled():
                done.exception()
        future.add_done_callback(finished)
        try:
            result = await asyncio.shield(future)
            if result is cancelled:
                raise asyncio.CancelledError()
            return result
        except asyncio.CancelledError:
            work.cancelled.set()
            raise
