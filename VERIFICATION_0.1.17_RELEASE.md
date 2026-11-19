# v0.1.17 verification

Adds `gpt-6.2-sol` and `gpt-6.2-astra`, matching Perplexity routes and the macOS About list. Unsupported reasoning, sampling, and log-probability options are normalized according to the GPT-6.2 migration guide. Other model routes and production request handling remain compatible.

## Source and client checks

All **141 tests pass in each** of Python 3.10.20, 3.12.13, 3.13.14, local 3.14.6, and Intel Python 3.12.13 under Rosetta, using the unchanged pinned dependencies. Coverage includes model discovery and routing, custom/pinned/passthrough normalization, transparent overrides, reasoning effort through ultra, retained encrypted-reasoning requests, JSON/SSE tool round trips, size limits, image recovery, continuation, fallback, cancellation, and concurrency. Added budget checks cover restarts, both models sharing one allowance, nonfinite reported costs, and full-history reservation before continuation and retries.

All **16 actual-client qualification runs pass**: installed Codex CLI 0.152.0 and supported CLI 0.167.1, both GPT-6.2 models, and CLI tool loops, app-server tool/follow-up/compaction, oversized-image recovery, and production-guard compaction. These use local synthetic providers, isolated Codex homes and ephemeral ports. Compaction is recognized as `contextWindowExceeded`, retains its marker and permits a successful next turn. Image recovery remains under the 19 MB guard.

Native launcher tests pass on Apple Silicon and Intel under Rosetta. The captured About text includes version 0.1.17 and both models. Existing window, Details, wrapping, keyboard, menu-bar, polling, and child-process lifecycle checks pass. Rendered expanded content remains readable.

## Live Perplexity checks

Both models pass six checks each through the production adapter and Perplexity: JSON completion, SSE completion, streamed function dispatch, returned function result, streamed continuation follow-up, and a small blue PNG. Each completes with the expected synthetic marker, tool or color. Initial none/minimal reasoning and unsupported sampling/log-probability options successfully pass through normalization.

A dedicated persistent **$5 maximum** ledger reserves before every attempt, including retries and full effective conversation history. Output is capped at 1,024 tokens, with no paid hosted tools. There were **13 attempts**, including an initial rejected environment credential. Accounted cost is **$0.752664**, including **7 unresolved conservative reservations**; reported cost for the remaining calls is **$0.004480**. Unresolved reservations are not released or presented as confirmed charges.

The key is read from an owner-only temporary file; no credential, raw paid-testing ledger, private content, or provider response identifier is included in the repository. GitHub Actions remains disabled and the installed adapter remains untouched.

## Distribution verification

Final apps and the wheel are built from the merged source commit. Publication requires matching frozen production modules and entry point, correct architectures/version/model metadata, included Pillow codecs, excluded research injection, authenticated packaged JSON/SSE checks, strict deep signatures, accepted notarization, stapled tickets, and fresh-extraction Gatekeeper checks. Published downloads must match SHA256SUMS.txt.

[Source and test evidence](docs/evidence/release-0.1.17.json) records the completed checks above. [Final distribution verification](https://github.com/FredHutch/codex-perplexity-adapter/releases/download/v0.1.17/verification-v0.1.17.json) records the merged commit, package checks, notarization submissions and artifact hashes after the final build.

Earlier small-text readability limitations still apply; this model-routing release does not change image encoding or qualify precision form accuracy.

## Reproduction

Run `python -m unittest discover -s tests -v` in the pinned Python environments. The `qualify_clients`, `qualify_app_server`, `qualify_resize_client`, and `qualify_compaction` helpers accept `--model` and `--output-dir`. Use synthetic local providers for client/transport checks. `scripts/qualify_gpt62.py --key-file /path/to/owner-only-key.md` performs the separately budgeted live checks; changing its report directory does not create another allowance. Run native tests in isolated app bundles containing the current Info.plist. Retain historical ledgers and evidence.
