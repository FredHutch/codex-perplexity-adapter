# Adapter v0.1.13 verification

**Documented release qualification passed.** Local tool loops use full-history replay; ordinary continuation resumes after a successful answer without outstanding local calls. Live client, fresh desktop, compaction, deterministic, and packaging checks pass within the limits described below. The macOS launcher always enables reuse and fits its window to the content. Both distribution ZIPs are Developer ID signed, notarized, stapled, and accepted by Gatekeeper. The installed app was not replaced.

## Final behavior

- The macOS app always launches with `--continuation auto`, without a checkbox or preference access. Old saved values are inert. The CLI retains `--continuation auto|disabled`, defaulting to auto.
- Normalize before inspecting tool items or matching fingerprints. Automatic continuation requires an exact prefix and a new suffix without local tool calls or results.
- Standard, custom, and namespaced local tool dispatch invalidates the chat checkpoint. Repeated tool rounds replay complete normalized history, preserving order, arguments, images, reasoning, and call IDs. A successfully delivered answer without outstanding local calls establishes the next checkpoint.
- Failed, incomplete, malformed, or cancelled responses invalidate state and earn no saved-byte credit. Checkpoints and savings commit after final delivery and successful application completion.
- Top-level streaming provider failures use Codex's expected failed-response envelope while preserving provider fields. Only structured prior-reference errors qualify for one-time replay before stream output. Generic invalid requests, ambiguous connection failures, and failures after output do not qualify.
- Explicit client continuation, store: false, chat isolation, request serialization, cache bounds, and the 18,000,000-byte guard remain active. Oversized replay returns the compaction-recognizable error without truncation. Tool replay does not remove the provider cap.
- Authenticated numeric tool_boundary_replays is shown as Tool replays. It counts transmitted tool-related full replay once per client request, excluding retries and preflight rejection. Status retains no conversation content.
- Fresh desktop chats can include namespaced tool notifications without call IDs. Preserve these as user-role notification text. Paired results retain call IDs and omit unsupported client namespace/name fields. The desktop test discovered and verified this additional HTTP 400 fix.

## Deterministic and client verification

All **101 tests** pass on Python **3.10.20, 3.12.13, 3.13.14, and local 3.14.6**, plus Intel Python 3.12.13 under Rosetta. Launcher syntax and diff whitespace checks pass.

The clean committed source also passes all 101 tests. GitHub Actions is disabled at repository level; the dispatched matrix remains queued without jobs. The user explicitly chose to keep Actions disabled and release using the verified local checks. No GitHub CI pass is claimed.

Coverage includes stateful provider rejection of tool-bearing response references, exact history reconstruction, first-turn and later tools, parallel/multiple/custom/namespaced rounds, images and reasoning, JSON and incremental SSE, resumed continuation, cancellation and failed final delivery, narrow retries, preserved errors, exact UTF-8 boundaries, compaction/configuration resets, cache lifecycle, concurrency, authentication, desktop notifications, and qualification-ledger concurrency/ceilings.

`macos/tests/LauncherTests.m` exercises old preferences absent/true/false in an isolated volatile defaults domain. Every case selects auto and has no checkbox. Native AppKit layout checks cover stopped, starting, running, large counters, unavailable statistics, stopped again, startup error, and a long diagnostic. Width remains 580 points; bottom margin remains 20 points; the top edge stays fixed; wrapping labels have no line limit or clipped height; the main stack is unambiguous. Content heights are 233 points stopped/starting/unavailable/error, 284 running, 314 with 18-digit counters, and 257 with the long diagnostic. No constraint warnings were emitted.

Build and run the native check with `clang -fobjc-arc -framework AppKit macos/tests/LauncherTests.m -o build/launcher-tests` then `build/launcher-tests`. A separate test-only GUI subclass overrides server start/stop, so inspection cannot affect the running adapter. Visible stopped and running layouts are readable with no checkbox or clipping. Paste, Return, Tab focus, Stop, and Command-M were exercised. Evidence: compact-launcher-tests.json and the GUI inspection. No provider requests were made for the launcher checks.

Installed CLI 0.152.0 and current CLI 0.167.1 both pass isolated HTTP/SSE mock tool loops. Current CLI 0.167.1 also completed a live shell command, submitted its actual custom-tool result, and delivered the final answer. Its first reporter falsely marked the tool result absent because it checked only function_call_output; the reporter now recognizes custom_tool_call_output and verifies successful command execution. The corrected report passes. Evidence: codex-cli-0.167.1-live.json.

Dedicated app-server clients using current CLI **0.167.1** and the desktop's bundled **0.167.0-alpha.3.2** each completed a live tool round, ordinary follow-up continuation, manual client compaction, and a successful follow-up retaining the command result. Each isolated adapter observed one tool replay and two continuations with zero fallback. These tests use separate temporary configuration and synthetic content. Evidence: codex-cli-*-appserver-live.json. The current CLI run preceded the additive desktop-notification fix; the bundled desktop-backend run and final test matrix include it.

## Desktop acceptance

A separate desktop chat, **Perplexity v0.1.13 desktop qualification**, was created with user authorization. Its first request received HTTP 400 because Perplexity rejected namespace on an unpaired desktop tool notification. After the translation fix, the user started the corrected extracted candidate with --continuation auto. The chat then completed one actual shell command with exit code 0, received DESKTOP_013_REAL_681, and repeated that exact result. Its next tool-free turn also completed and retained the exact marker.

The chat's client logs attribute three successful HTTP 200 Responses requests to the running candidate on port 4000. The recorded server executable resolves to the final-extracted arm64 app. Dedicated desktop-backend tests independently prove full replay followed by resumed continuation. GUI-chat continuation counters are adapter-wide, so they do not independently attribute an optimized body to that chat. A project-local provider override was attempted but was not applied by the existing desktop session; that file was removed during cleanup. No global Codex configuration was changed. Evidence: isolated-desktop-acceptance.json and desktop-preview-smoke.json.

## Size and compaction

Live synthetic tool dispatch completed, its result used a **747-byte full replay**, and the ordinary follow-up resumed continuation at **499 bytes versus 1,061 bytes** full replay, retaining the result. Evidence: tool-proof-results.json.

All seven built-in upstream models completed ordinary continuation and retained the synthetic marker. Streaming continuation, tool replay, instruction reset, and synthetic compacted-input recovery pass. Evidence: models-results.json and features-results.json.

Three valid 32-by-32 images with ignorable PNG padding created **21,001,804 bytes** of accumulated full history. A follow-up transmitted **309 bytes**, saving **99.9985%**, and retained the marker and image color. Each selected transmitted body still passed the guard. This controlled result does not establish removal of downstream expansion or provider limits. Evidence: images-results.json.

A real current Codex client accumulated more than 18 MB of synthetic history against a local mock provider. An instruction/setting change forced full replay and triggered the production 18,000,000-byte guard. The client recognized contextWindowExceeded. Manual thread/compact/start completed, and the next turn succeeded. This is actual client recovery with the real guard, not a paid 18 MB provider test. Oversized compaction can require the client's own history reduction; it is not automatic provider-cap removal. Evidence: client-18mb-compaction.json. Live manual compaction and retained-context recovery are separately established above.

## Packages

Final artifacts are in build/release-0.1.13-compact. They replace the earlier pre-UI packages; earlier package hashes are obsolete.

| Architecture | SHA-256 |
| --- | --- |
| Apple Silicon | 3e81a8145705a9d0941fa2ae1530fc61013614b1567169a9fc5c51f5dc5b8470 |
| Intel | d4e7307aa64b8fbb5625320d3f37b9b4b76bb8f29d93f8a2c48e8a65d7aac7f1 |

Both packages contain version 0.1.13 and the final always-on compact launcher. Launcher and server architectures match their targets. All seven frozen adapter modules match current source when compared under each bundle's Python version. The final ZIPs were freshly extracted and passed strict nested signatures, stapled-ticket validation, and Gatekeeper acceptance as Notarized Developer ID. Apple accepted submissions **52f43987-8362-4f71-9f9a-5812439db82e** (arm64) and **b63567c9-4776-4881-a0de-527f773d333b** (Intel).

Both rebuilt extracted servers pass health, unauthenticated status rejection, authenticated numeric status, and JSON/SSE 18 MB errors with dummy credentials and no provider calls. Intel server execution and the native launcher tests pass under Rosetta, not physical Intel hardware. The earlier live-qualified arm64 candidate remains running; these launcher-only changes reuse that live evidence. The rebuilt launcher is verified in an isolated GUI preview and native tests as described above. UI control of the Codex desktop itself is restricted in this environment; purpose-built thread tools and chat-attributed client logs supplied desktop evidence. Evidence: packaged-server-checks.json, compact-launcher-tests.json, compact-launcher-intel-tests.json, notarization submissions, build logs, and final-source-summary.json. The release wheel is built from the merged source and checked for exact adapter module contents and version metadata; SHA256SUMS.txt covers all three distribution assets.

## Cost audit

The original accounted total was $4.80326. An earlier $5 allowance had an absolute $10 ceiling. Concurrent qualification processes overwrote CLI reservations; conservative reconciliation raised the total to **$9.940426**, $0.137166 over the stricter original-plus-$5 allowance but below $10. Old unknown/error reservations were preserved. The ledger now uses an interprocess lock, reload-before-write, unique reservation IDs, and atomic replacement, with process-concurrency regression coverage.

The user subsequently authorized **up to $10 more**, then **up to $5 more solely to finish the desktop retry**. The final conservative total is **$23.644674**, below the current **$24.883652** ceiling. It includes unknown/error charges and unused retry reservations; it is not confirmed billing. The metered scripted calls reserve each possible attempt before transmission and cap output tokens. Existing unknown reservations were not refunded.

Two desktop actions bypassed the intended dedicated metered endpoint: its initial HTTP 400 and successful retry used the running candidate directly because the project-local override was not applied. Those reservations were reconciled afterward using the saved synthetic context for the rejected request and observed usage at doubled upper rates for the successful requests, retaining three possible attempts. This did not satisfy the reserve-before-attempt requirement for those actions. The final direct desktop follow-up had three conservative reservations made before submission. No additional paid qualification calls are planned. Ordinary user conversation traffic is outside the scripted qualification ledger.

Evidence: live-ledger.json, tests/test_qualification_budget.py, client reports, and isolated-desktop-acceptance.json.

## LiteLLM comparison

Research used public commit 461b56b14b2a024ff73e59f56c01db169dfb902f. Its native Perplexity integration forwards previous_response_id; the inspected test verifies forwarding, not live tool continuation. Its Responses-to-Chat bridge reconstructs retained history and tool associations, which does not solve the provider transport cap. Its handling of HTTP-200 failed responses supports distinguishing terminal success from HTTP status. The large MCP SSE fix concerns a separate event-size limit. No LiteLLM dependency or stored-content cache was added.

Sources:

- https://github.com/BerriAI/litellm/blob/461b56b14b2a024ff73e59f56c01db169dfb902f/litellm/llms/perplexity/responses/transformation.py
- https://github.com/BerriAI/litellm/blob/461b56b14b2a024ff73e59f56c01db169dfb902f/litellm/responses/litellm_completion_transformation/transformation.py
- https://docs.perplexity.ai/docs/agent-api/conversation-state
- https://developers.openai.com/codex/app-server/

Historical raw evidence is retained locally under the ignored build/qualification-0.1.13 directory; logs and cost ledgers are excluded from the release source. Qualification accepts the documented full replay during tools. Installation remains a separate action.
