"""Offline synthetic resize evidence and macOS Vision OCR comparison."""
import base64
import io
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.image_resize import _candidates, resize_request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build/qualification-0.1.15"

def form():
    im = Image.new("RGB", (1600, 2200), "white")
    d = ImageDraw.Draw(im)
    font = "/System/Library/Fonts/Helvetica.ttc"
    d.text((80, 55), "SYNTHETIC FORM - NOT PATIENT DATA", font=ImageFont.truetype(font, 38), fill="black")
    for row, size in enumerate((32, 24, 20, 16, 12)):
        y = 160 + row * 370
        f = ImageFont.truetype(font, size)
        d.text((80, y), f"Text size {size}: Dose 1.25 mg; value 0.05; ID 724914", font=f, fill="black")
        d.text((80, y + 55), "Label: YES / NO / UNKNOWN", font=f, fill="black")
        for x in (100, 250):
            d.rectangle((x, y + 115, x + 25, y + 140), outline="black", width=2)
        d.line((252, y + 126, 260, y + 137, 274, y + 116), fill="black", width=3)
        d.line([(450, y + 130), (470, y + 105), (480, y + 140), (500, y + 110), (520, y + 135)], fill="blue", width=3)
        d.text((650, y + 100), "Review annotation", font=f, fill="red")
    return im

def run():
    OUT.mkdir(parents=True, exist_ok=True)
    source = OUT / "form-original.jpg"
    form().save(source, quality=90)
    url = "data:image/jpeg;base64," + base64.b64encode(source.read_bytes()).decode()
    files = [source]
    with Image.open(source) as im:
        for candidate in _candidates(im, "JPEG"):
            data = base64.b64decode(candidate.split(",", 1)[1])
            with Image.open(io.BytesIO(data)) as resized:
                path = OUT / f"form-{max(resized.size)}.jpg"
            path.write_bytes(data); files.append(path)
    payload = {"model": "gpt-6.1-sol", "input": [{"role": "user", "content": [{"type": "input_image", "image_url": url}]} for _ in range(100)]}
    started = time.perf_counter()
    result = resize_request(payload, True)
    elapsed = time.perf_counter() - started
    ocr = json.loads(subprocess.check_output([str(OUT / "vision-ocr"), *map(str, files)], text=True))
    critical = ("1.25", "0.05", "724914")
    for entry in ocr:
        entry["critical_rows_retained"] = sum(all(value in line for value in critical) for line in entry["lines"])
    qualified = all(entry["critical_rows_retained"] >= ocr[0]["critical_rows_retained"] for entry in ocr)
    report = {"readability_qualified": qualified, "source_bytes": result.original_size, "transmitted_bytes": len(result.body),
              "resized_occurrences": result.resized_images, "seconds": elapsed,
              "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "synthetic_only": True, "ocr": ocr}
    (OUT / "resize-evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "ocr"}))

if __name__ == "__main__": run()
