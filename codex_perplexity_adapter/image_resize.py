"""Request-local, bounded image resizing after continuation selection."""
from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import io
import math
import warnings
from dataclasses import dataclass
from collections import OrderedDict
from contextlib import ExitStack

from PIL import Image, ImageOps, JpegImagePlugin, PngImagePlugin

from .request_size import REQUEST_BUDGET_BYTES, encode_request, prepare_request_body

TARGET_BYTES = 18_000_000
MIN_LONG_EDGE = 1024
MAX_ENCODED_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_REQUEST_PIXELS = 100_000_000
MAX_MEMO_BYTES = 32 * 1024 * 1024
RESIZE_POLICY = "oldest-first-v1:0.85:1024:18000000:jpeg85:keep-format"


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


def _open_image(url, remaining_pixels):
    if not isinstance(url, str):
        return None
    prefix, separator, encoded = url.partition(",")
    mime = {"data:image/jpeg;base64": "JPEG", "data:image/png;base64": "PNG"}.get(prefix)
    if not separator or mime is None or len(encoded) > MAX_ENCODED_BYTES:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(raw))
        pixels = image.width * image.height
        if (image.format != mime or image.mode not in {"RGB", "L", "RGBA", "LA", "P"}
                or getattr(image, "n_frames", 1) != 1
                or pixels > min(MAX_IMAGE_PIXELS, remaining_pixels)
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
        image.load()  # Strict decoding; never enable LOAD_TRUNCATED_IMAGES.
        return image, pixels, mime
    except (ValueError, OSError, SyntaxError, binascii.Error, Image.DecompressionBombWarning, Image.DecompressionBombError):
        return None


def _candidates(image, mime):
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
    with ImageOps.exif_transpose(image) as oriented, ExitStack() as stack:
        if oriented.mode == "P" or (mime == "PNG" and "transparency" in info):
            oriented = stack.enter_context(oriented.convert("RGBA" if "transparency" in info else "RGB"))
        edge = max(oriented.size)
        while edge > MIN_LONG_EDGE:
            edge = max(MIN_LONG_EDGE, math.floor(edge * 0.85))
            scale = edge / max(oriented.size)
            size = tuple(max(1, round(d * scale)) for d in oriented.size)
            with oriented.resize(size, Image.Resampling.LANCZOS) as resized:
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


def resize_request(payload, enabled=False):
    original = encode_request(payload)
    if len(original) <= REQUEST_BUDGET_BYTES or not enabled:
        return PreparedRequest(prepare_request_body(payload), len(original))
    working = copy.deepcopy(payload)
    current_size = len(original)
    changed = 0
    pixels_used = 0
    memo = OrderedDict()
    memo_bytes = 0
    decoded_urls = set()
    for part in image_parts(working):
        if current_size <= TARGET_BYTES:
            break
        url = part.get("image_url")
        if not isinstance(url, str):
            continue
        if url not in memo:
            opened = _open_image(url, MAX_REQUEST_PIXELS if url in decoded_urls else MAX_REQUEST_PIXELS - pixels_used)
            candidates = []
            if opened is not None:
                image, pixels, mime = opened
                if url not in decoded_urls:
                    pixels_used += pixels
                    decoded_urls.add(url)
                try:
                    with image:
                        smallest = len(url)
                        for candidate in _candidates(image, mime):
                            if len(candidate) < smallest:
                                candidates.append(candidate)
                                smallest = len(candidate)
                except (OSError, ValueError, SyntaxError, TypeError, KeyError, OverflowError):
                    candidates = []
            cost = len(url) + sum(map(len, candidates))
            while memo and memo_bytes + cost > MAX_MEMO_BYTES:
                old_url, old_candidates = memo.popitem(last=False)
                memo_bytes -= len(old_url) + sum(map(len, old_candidates))
            if cost <= MAX_MEMO_BYTES:
                memo[url] = candidates
                memo_bytes += cost
        else:
            candidates = memo[url]
        best = url
        for candidate in candidates:
            if len(candidate) < len(best):
                current_size += len(candidate) - len(best)
                best = candidate
            if current_size <= TARGET_BYTES:
                break
        if best != url:
            part["image_url"] = best
            changed += 1
    # Re-measure the complete selected UTF-8 JSON; estimates never bypass guard.
    return PreparedRequest(prepare_request_body(working), len(original), changed)


class ImageResizer:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.slot = asyncio.Semaphore(1)

    async def prepare(self, payload):
        await self.slot.acquire()
        future = asyncio.get_running_loop().run_in_executor(None, resize_request, payload, self.enabled)
        # shield prevents cancellation from releasing the slot while native
        # decoding still runs. No other image worker can start until it finishes.
        def finished(done):
            self.slot.release()
            if not done.cancelled():
                done.exception()  # Retrieve exceptions after a cancelled await.
        future.add_done_callback(finished)
        return await asyncio.shield(future)
