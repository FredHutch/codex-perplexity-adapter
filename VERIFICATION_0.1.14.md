# v0.1.14 verification

The guard accepts a complete selected UTF-8 JSON body at 19,000,000 bytes and rejects larger bodies without truncation. It is enforced after normalization and continuation selection, including tool replay and prior-reference fallback. The app displays “Limit: 19 MB”. Roughly 1 MB remains below the observed downstream 20 MB limit; provider conversion and token limits remain independent constraints.

## Local checks

All 104 tests pass on Python 3.10.20, 3.12.13, 3.13.14, local 3.14.6, and Intel Python 3.12.13 under Rosetta. New tests exercise final normalized bodies of 18,000,001, 18,999,999, 19,000,000, and 19,000,001 bytes for JSON and SSE, with multibyte UTF-8, instructions, and metadata. Mock upstream receives exactly the measured body for accepted requests and receives no oversized request. Production-size tool-result replay and oversized continuation fallback are covered; existing continuation and error regressions remain active.

Codex CLI 0.167.1 accumulated synthetic history, then forced full replay. The adapter measured 19,840,156 bytes and Codex recognized contextWindowExceeded. Manual compaction completed; the following turn returned RETAIN_GUARD_724. The helper derives its accumulation threshold from the production constant and asserts measured oversize, recognized error, compaction, and retained marker. Evidence: build/qualification-0.1.14/client-guard-compaction.json.

Native AppKit tests pass on Apple Silicon and under Intel Rosetta: 19 MB text, all saved reuse preferences, keyboard controls, layout states, large counters, and wrapped diagnostics. Launcher syntax and whitespace checks pass.

## Live qualification

The existing https://api.perplexity.ai/v1/responses route and openai/gpt-6.1-sol completed:

| Case | Final upstream body | Result |
| --- | ---: | --- |
| JSON image request | 18,987,087 bytes | Completed; red identified |
| SSE image request | 18,987,086 bytes | Completed; red identified |
| Tool dispatch | 455 bytes | Completed with local function call |
| SSE tool-result full replay | 18,987,613 bytes | Completed; GUARD_MARKER_914 and blue retained |

The adapter recorded one tool-boundary replay. Synthetic valid 32×32 PNGs contain a large ignorable ancillary chunk to test transport bytes without excessive visual tokens. This does not establish acceptance of every request under 19 MB or qualify large visual-context inputs. One initial invalid-key attempt failed with HTTP 401; no automatic retry followed it.

Costs were reserved before every upstream attempt in the existing persistent ledger. Additional accounted spending is $0.15083, including $0.15 held for unknown costs and $0.00083 reported charges, below the additional $10 allowance. Existing reservations remain intact. No raw ledger, API keys, or private chat content is published. Evidence: live-guard.json and budget-baseline.json under build/qualification-0.1.14.

## Distribution

Both architecture packages embed Python modules matching the source, pass isolated server startup, reject unauthenticated status, expose authenticated numeric statistics, and return recognizable JSON/SSE guard errors without provider calls. Intel checks run under Rosetta rather than physical Intel hardware. Native launcher tests verify the 19 MB display. Installed and running adapters remain untouched.

Both packages pass strict deep code-signature verification, stapled-ticket validation, and Gatekeeper assessment as Notarized Developer ID. Apple notarization submissions 09d7f33f-3afa-4dc3-b752-19ee454338de (Apple Silicon) and 750bda61-472e-41fe-b3a2-5e8e1ef49fb0 (Intel) were accepted. Clean temporary copies exclude Finder metadata added by the Desktop workspace. Final wheel contents and published-asset checksums are verified during publication. GitHub Actions remains disabled by user instruction; verified local checks govern acceptance. Historical v0.1.13 evidence is preserved.
