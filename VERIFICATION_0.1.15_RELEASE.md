# v0.1.15 release verification

The user approved Anchor Test 3 after local use and requested notarization and publication on November 11, 2026. This release promotes its earlier-answer continuation and expanded image preparation policy into the production app and Python package. The installed and running app was not replaced during release work. GitHub Actions stays disabled; verified local checks govern delivery.

## Behavior and limitations

The adapter reuses the latest completed answer during tool rounds, sending that reference and the complete suffix after its saved prefix. It never anchors on a tool-bearing response. A final answer advances the checkpoint. Without a matching completed-answer checkpoint, the adapter replays full history. Original-history fingerprints, tool associations, successful-delivery accounting, bounded checkpoints, serialization and structured-reference fallback are preserved.

Image resizing is initially enabled in the macOS app and can be disabled using its saved checkbox. CLI resizing remains opt-in. It runs only above the 19,000,000-byte guard, aims for 18,000,000 bytes, and keeps the 1,024-pixel long-edge floor. The work allowance is 500 million decoded pixels and 60 seconds of active preparation, shared across initial selection and fallback. The deadline is soft; native work already running can finish. Candidates are generated lazily from original pixels. Formats, metadata rules and encoding settings are unchanged from the approved test build.

**Small-text readability remains unqualified.** Synthetic OCR loses some decimal punctuation and identifiers at lower resolutions. The release retains this documented limitation and advises disabling resizing for precision form review. Reuse and resizing cannot guarantee every conversation fits provider/model limits. Images after the anchor still accumulate; full replay or its suffix can exceed 19 MB and require compaction.

## Checks

- **138 tests pass on each** of Python 3.10.20, 3.12.13, 3.13.14, local 3.14.6, and Intel 3.12.13 under Rosetta. The suite compares production continuation with exact full-history reconstruction over three tool rounds, parallel results, standard/custom/namespaced tools, reasoning and mixed JPEG/PNG results in JSON and incremental SSE.
- Existing failure, cancellation, changed-history/settings, compaction, explicit continuation, cache lifecycle, retry, fallback and original-history resizing regressions pass. Exact size/pixel boundaries, memo eviction/redecoding, lazy termination, soft-deadline behavior and shared fallback work are covered.
- Installed Codex 0.152.0 and supported Codex 0.167.1 complete actual mock image-tool turns and continue from the completed answer. Their oversized image requests recover from 21,586,178 / 21,599,901 bytes to 17,933,998 / 17,947,721 bytes, then complete follow-ups using continuation.
- The 50-occurrence, 49-unique-image fixture includes tool definitions and complete serialization overhead. Its 22,900,000-byte body fails under the old allowance at 19,106,508 bytes and fits under the new allowance at 17,993,816 bytes after 130 million decoded pixels. Packaged JSON/SSE bodies are 17,993,855 / 17,993,854 bytes.
- Packaged Apple Silicon and Intel servers pass authenticated loopback TLS recovery and JSON/SSE rejection with synthetic credentials. Oversized requests do not reach upstream. Frozen production modules, entry points and Pillow codecs match source; research injection code is excluded from the app and wheel.
- Native AppKit launcher tests pass on arm64 and Intel Rosetta: states, wrapping, 580-point width, 20-point bottom margin, large counters, keyboard controls and resize preference handling. Launcher syntax and whitespace checks pass.
- Earlier fresh-process memory tests found no retained decoded images and measured about 90 MiB / 214 MiB incremental peak RSS on unique-page / maximum-image fixtures, below 512 MiB. See [work-budget evidence](VERIFICATION_RESIZE_WORK_BUDGET.md).

No paid API calls were made for this release. Prior [live qualification](docs/TOOL_CONTINUATION_RESEARCH.md) establishes earlier-answer continuation on the tested Perplexity route and gpt-6.1-sol; direct tool continuation failed and is not used. Broader model/provider behavior is not guaranteed.

## Signing and distribution

Both architecture packages are Developer ID signed, notarized, stapled, and accepted by Gatekeeper as Notarized Developer ID after final ZIP extraction. Versions are 0.1.15, port 4000, with the normal release app name and bundle identifier. Apple accepted submissions:

- Apple Silicon: cfc072ff-8077-49f9-aca9-d43c2c7df352.
- Intel: c571f926-e378-4d05-b052-6accdfb5e7f7.

Release assets are the two macOS ZIPs, the Python wheel and SHA256SUMS.txt. The wheel is built from merged release source; published downloads are checked against the checksum file. Historical candidate reports remain as records of their earlier state. [Machine-readable release evidence](docs/evidence/release-0.1.15.json) records validation results.
