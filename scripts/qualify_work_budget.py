"""Fresh-process synthetic resize peak RSS and work-budget measurements."""
import argparse
import gc
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.image_resize import ResizeWorkBudget, resize_request
from codex_perplexity_adapter.request_size import encode_request
from scripts.work_budget_fixtures import many_images

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/work-budget'


def worker(kind):
    if kind == 'unique': p = many_images()
    else: p = many_images(size=(4000, 5000), unique=3, count=12, body_bytes=24_000_000)
    # Roundtrip models the parsed-request baseline; discard the fixture's bytes.
    p = json.loads(encode_request(p)); gc.collect()
    baseline = int(subprocess.check_output(["/bin/ps", "-o", "rss=", "-p", str(os.getpid())], text=True).strip()) * 1024
    work = ResizeWorkBudget(); start = time.monotonic()
    result = resize_request(p, True, work)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    elapsed = time.monotonic() - start
    old = None
    if kind == 'unique':
        from codex_perplexity_adapter.request_size import RequestTooLarge
        try:
            resize_request(p, True, ResizeWorkBudget(pixel_limit=100_000_000))
        except RequestTooLarge as error:
            old = error.size
        else: raise AssertionError('old allowance unexpectedly fit')
    report = {'fixture': kind, 'parsed_request_rss': baseline, 'peak_rss': peak,
              'incremental_peak_rss': peak - baseline, 'seconds': elapsed,
              'original_bytes': result.original_size, 'final_bytes': len(result.body),
              'old_100m_final_bytes': old, 'decoded_pixels': work.decoded_pixels, 'resized_occurrences': result.resized_images,
              'stop_reason': work.stop_reason or 'target_or_images_exhausted'}
    assert peak - baseline <= 512 * 1024 * 1024, report
    assert len(result.body) <= 19_000_000, report
    # Pillow cores must have been closed, even if Python wrappers survive.
    from PIL import Image
    live = [im for im in gc.get_objects() if isinstance(im, Image.Image) and getattr(im, '_im', None) is not None]
    assert not live, [(im.size, im.mode) for im in live]
    report['live_decoded_images_after_prepare'] = len(live)
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--worker', choices=['unique', 'maximum'])
    args = parser.parse_args()
    if args.worker: worker(args.worker)
    else:
        OUT.mkdir(parents=True, exist_ok=True)
        reports = [json.loads(subprocess.check_output([sys.executable, __file__, '--worker', kind], text=True)) for kind in ('unique', 'maximum')]
        (OUT / 'performance.json').write_text(json.dumps(reports, indent=2) + '\n')
        print(json.dumps(reports))
