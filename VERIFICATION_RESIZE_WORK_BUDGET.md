# Anchor Test 3 resize work-budget verification

Verified locally on November 11, 2026. This is a separate **0.1.15 test build 3**, based on the Anchor Test source at research commit 7e5b56e, on branch codex/resize-work-budget. All content was synthetic; no paid API calls were made. The existing draft PR, published release, GitHub Actions and running app were left unchanged.

## Behavior

The decode allowance is now **500,000,000 pixels**, charged before loading source pixels. Failed decode attempts and repeated decoding after memo eviction count again; memo hits are free. The **60-second active preparation allowance** is shared by the initial selected body and structured-reference fallback, excluding queue and upstream network time. It uses a monotonic clock and is a soft deadline: native work already running can finish. The best completed candidate is retained, and the final serialized body must still fit 19,000,000 bytes.

Candidates are generated one at a time from original pixels and closed immediately at the target. One worker remains occupied until native processing ends after cancellation. Disconnects during preparation cancel the work before upstream transmission. Decoded image cores are explicitly closed; there is no decoded-image cache. A request-local 32 MiB encoded-candidate memo remains. Content-free operational diagnostics record time, decoded pixels, processed/skipped counts, byte sizes and stop reason. The policy fingerprint changed. No new CLI control, checkbox or status field was added.

Oldest-first ordering, 15% steps, 1,024-pixel floor, JPEG quality/subsampling, PNG transparency, metadata rules, original-history fingerprints and earlier-answer continuation remain in place. Bodies at or below 19 MB are unchanged. Exhausted work sends its best result only when within the guard; otherwise the existing compaction-recognizable code is returned with budget details.

## Results

| Check | Result |
| --- | --- |
| Full suite: Python 3.10.20, 3.12.13, 3.13.14, local 3.14.6, Intel 3.12 under Rosetta | 138 tests passed on each |
| Pixel boundaries, failed decode charging, memo hits/eviction | Passed |
| Fake-clock soft deadline and completed slow encode | Passed |
| Lazy stop, explicit image closing, cancelled worker serialization, HTTP disconnect | Passed |
| Exhaustion with 18–19 MB best body versus oversized rejection, JSON/SSE | Passed |
| Shared fallback allowances and identical retry bytes | Passed |
| Original-history continuation, image savings after complete delivery, tool images | Passed |
| Existing anchor/direct offline lifecycle and resize regressions | Passed |
| AppKit wrapping, 580-point width, 20-point bottom margin, keyboard and state checks | Passed for base and generated Anchor Test 3 launcher |
| Signed extracted bundle, embedded production and anchor source, Pillow codec | Passed |
| Packaged JSON/SSE synthetic tool-image recovery, authentication and local rejection | Passed against loopback TLS |
| Whitespace and launcher syntax | Passed |

The complete many-page regression has **50 image occurrences, 49 unique JPEGs, tool calls/results and definitions**, and is **22,900,000 bytes** including serialization overhead. Its old 100-million allowance stops at **19,106,508 bytes**, failing the guard. The new policy decodes **130 million pixels** and reaches **17,993,816 bytes**, resizing 13 occurrences and preserving newer pages. The packaged normalized JSON and SSE bodies reach **17,993,855** and **17,993,854 bytes**.

Fresh-process local-runtime preparation took **5.99 seconds** for the many-page fixture and **3.05 seconds** for a fixture with 20-million-pixel images. Incremental peak RSS above the parsed-request baseline was **89.5 MiB** and **214.1 MiB**, respectively, below the 512 MiB requirement. No open decoded image cores remained after preparation. The bundled Python 3.13 runtime also passed: 6.03/3.10 seconds and 89.7/212.4 MiB incremental peak RSS. These are fixture measurements, not a guarantee for arbitrary inputs.

Installed Codex **0.152.0** recovered a **21,586,178-byte** request to **17,933,998 bytes**; supported client **0.167.1** recovered **21,599,901** to **17,947,721 bytes**. Both completed, then used continuation for their follow-up. Both also passed mock image-tool continuation and compaction recovery with the marker retained. Codex preprocesses attached images, so client qualification uses 150 unique synthetic pages to cross the actual serialized guard after that preprocessing.

**Readability remains unqualified for precision forms.** The unchanged synthetic OCR regression retained all five critical rows at 1,870 and 1,589 pixels, but only four at 1,350, 1,147 and 1,024. Visual inspection confirmed that checkboxes, blue handwriting-like marks and red annotations remain present at 1,024 pixels, while the smallest printed values are harder to distinguish. Small decimal punctuation and identifiers can be lost. The larger work allowance improves recovery capacity; it does not improve image readability or remove independent provider/model limits.

## Local app

- App: /private/tmp/codex-perplexity-anchor-test3/Codex Perplexity Adapter Anchor Test 3.app
- Version/build: 0.1.15 / 0.1.15.3; Apple Silicon arm64.
- Bundle identifier/preferences: com.local.codex-perplexity-adapter.anchor-test3.
- State/PID directory: ~/Library/Application Support/Codex Perplexity Adapter Anchor Test 3/.
- Log: ~/Library/Logs/Codex Perplexity Adapter Anchor Test 3.log.
- Port: **4000**. Resize control initially enabled, matching the earlier Anchor Test, and saved separately.
- Signed with Developer ID Application: Andrew Portuguese (SFC29VVF27), hardened runtime; strict nested signatures verified after ZIP extraction. This local test bundle was not notarized.
- Build entry point: script/build_and_run.sh --build-only; no app switch was performed.
- ZIP: dist/anchor-test3/Codex-Perplexity-Adapter-Anchor-Test-0.1.15-build3-arm64.zip.
- SHA-256: 2a5331d93fefd28f16b252cc45964aa2885e9d2a972c63a2e7f571df6caac3cc.

[Machine-readable evidence](docs/resize-work-budget-evidence.json) records final counters and measurements. Reproduction helpers: scripts/qualify_work_budget.py, scripts/qualify_resize_client.py, scripts/verify_resize_packages.py --app &lt;bundle&gt;, and scripts/verify_bundle_source.py &lt;server&gt; --anchor. Raw local logs remain under ignored build directories.
