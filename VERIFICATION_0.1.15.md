# 0.1.15rc1 image-resizing candidate

**Engineering checks pass; readability qualification failed. Do not merge or publish this candidate for precision form review.** It is an opt-in evaluation build, separate from stable v0.1.14. No paid API calls or private forms were used.

## Behavior

`--resize-images-on-limit` and the default-off app checkbox enable resizing only after normalization and conversation selection produce a body above 19,000,000 bytes. Images are visited oldest first, keeping JPEG/PNG format, aspect ratio, orientation, ICC and supported metadata. Each candidate comes from original pixels; the long edge steps down by 15% to 1,024 pixels. The target is 18,000,000 bytes; a final body up to the hard guard is accepted. Candidates that grow are discarded. Text, tool arguments, call IDs and source files remain intact.

Resizing is bounded to one worker, 32 MiB of encoded image data per image, 20 million pixels per image and 100 million unique pixels per request. A 32 MiB request-local memo is released after preparation. Unsupported formats/metadata pass unchanged. Continuation checkpoints use original normalized client-history hashes plus the resize policy and completed response; they retain no images. Retries reuse prepared bytes. Structured fallback prepares original full history independently.

Image request/count/byte savings commit only after successful complete delivery, including when automatic continuation is disabled or chat headers are absent. Existing “Saved” measures continuation before resizing; “Image saved” is separate.

## Local verification

All **118 tests** pass on Python **3.10.20, 3.12.13, 3.13.14, local 3.14.6**, and **Intel 3.12.13 under Rosetta**. Coverage includes exact complete UTF-8 bodies at 18,000,001, 18,999,999, 19,000,000 and 19,000,001 bytes; unchanged eligible bodies; substantial recovery; floor and resource skips; growing PNGs; metadata/orientation/transparency; malformed/CMYK inputs; custom/namespaced parallel tool results; continuation and fallback; cancellation and worker serialization; and separate counters. Existing compaction, errors, continuation lifecycle and concurrency regressions remain active.

Native AppKit checks pass on Apple Silicon and Intel Rosetta for saved absent/true/false preferences, always-on reuse, resize checkbox arguments and disabled-running state, stopped/starting/running/error/unavailable-statistics layouts, large wrapping counters, Return controls, preserved top edge and 20-point bottom margin. Launcher syntax, shell syntax and whitespace checks pass.

### Actual Codex clients, local providers only

| Client | Original body | Transmitted resized body | Following continuation | Outcome |
| --- | ---: | ---: | ---: | --- |
| Installed CLI 0.152.0 | 30,915,412 | 17,938,704 | 34,793 | Both turns completed |
| Supported CLI 0.167.1 | 30,928,655 | 17,951,947 | 25,369 | Both turns completed |

Synthetic local images passed through actual Codex app-server parsing, adapter selection/resizing, mock completion and subsequent continuation. Codex may prepare images itself; reported sizes are measured after that preparation. These checks do not establish live Perplexity visual accuracy.

Both clients also recognized a request above the guard as `contextWindowExceeded`, completed manual compaction, and completed the next turn with the retained marker. The production guard remained active throughout.

## Readability and performance

Generated forms contain printed sizes 32/24/20/16/12 pixels, decimal values, an identifier, checked/unchecked boxes, colored annotations and handwriting-like marks. macOS Vision offline OCR retained all five critical rows at the original 2,200-pixel long edge and at 1,870/1,589. It retained only four at 1,350/1,147/1,024: an ID was misread at 1,350 and decimal punctuation was lost at 1,024. The proposed acceptance rule therefore fails.

Visual comparison of enlarged original and 1,024-pixel crops shows blur in the smallest text and annotations. Checked/unchecked boxes and the synthetic blue mark remain distinguishable in this fixture. This limited fixture cannot establish safety for handwritten or clinical content.

A synthetic batch of 100 repeated form images fell from **20,259,733 to 17,981,421 bytes**, changing the oldest 15 occurrences and leaving later ones intact. On this Apple Silicon host, preparation took approximately **0.17 seconds**, with process peak RSS approximately **165 MB**. Repeated-image memoization makes this favorable; unique high-resolution batches can require more CPU/memory. A separate JPEG-heavy test recovered a body above 28 MB while leaving its newer images unchanged. Text PNGs sometimes grew after Lanczos and were retained unchanged.

Reproduction helpers: `scripts/qualify_resize.py`, `scripts/vision_ocr.swift`, `scripts/qualify_resize_client.py`, `scripts/qualify_compaction.py`, and `scripts/verify_resize_packages.py`. Synthetic images, OCR output and machine logs live under ignored `build/qualification-0.1.15/`; no private images or raw paid-testing ledger are included in the PR.

## Test distribution

The candidates have distinct app name/bundle ID, port **45015**, preferences, PID directory and log name. Stable v0.1.14 stays on 4000. Use an isolated Codex configuration pointed at `http://127.0.0.1:45015/v1`; do not replace your existing provider settings merely to inspect the candidate. Resizing starts off. Enable the checkbox before starting to evaluate synthetic images.

Both extracted architecture servers match all eight adapter source modules, including `image_resize`, and contain the Pillow JPEG/PNG codecs. Tests run actual packaged binaries through an authenticated temporary loopback TLS proxy that accepts only the expected mock destination, synthetic key and request path. JSON and SSE image recovery, authenticated numeric counters, rejected unauthenticated status and oversized non-image rejection pass. No request reaches Perplexity. Intel execution is under Rosetta, not physical Intel hardware.

Both final ZIPs pass extracted strict deep signature verification, stapled-ticket validation and Gatekeeper assessment as **Notarized Developer ID**. Apple accepted submissions `ca5ea9aa-39a3-4931-8128-c570526cd925` (Apple Silicon) and `a33a9ce9-7469-41c5-9ce1-ee73c6bf2635` (Intel). Clean temporary staging avoids Finder metadata added by the Desktop workspace.

| Candidate ZIP | SHA-256 |
| --- | --- |
| `Codex-Perplexity-Adapter-Test-0.1.15-rc1-macOS-arm64.zip` | `c947bb9a781ef87f7f00cc303c6759f58aa2e4cae5330468a53b0c01a3f2f516` |
| `Codex-Perplexity-Adapter-Test-0.1.15-rc1-macOS-x86_64.zip` | `861856c11e64a86a99e39f4db0731faa6c19462999e4f6494d12d7e6f9bc2de4` |

Signing/notarization establish distribution integrity, not image readability. GitHub Actions remains disabled. The PR remains unmerged and no release or tag is created. Installed/running apps are untouched.
