# Perplexity tool-continuation qualification

Historical qualification report. The approved earlier-answer approach is now released in v0.1.15; see [release verification](../VERIFICATION_0.1.15_RELEASE.md). The observations and limitations below are preserved.

2026-11-11. Source: a50b5d463d8d6f15b264c1f04862475409d9a898 (0.1.15 test
candidate), isolated branch codex/test-tool-continuation.

**Recommendation: use earlier-answer continuation in a future production change.**
It qualified on the existing Perplexity route with gpt-6.1-sol. Direct continuation
from a response requesting tools failed. This experiment changes no production
module, default, app package, running adapter, existing PR, or published release.

| Strategy | Result | Evidence |
|---|---|---|
| Direct tool continuation | Failed | 0/8 live chains: JSON tool-result requests returned HTTP 400 invalid_request; SSE returned HTTP 200 with terminal failed and the same error. |
| Earlier-answer continuation | Qualified | 8/8 basic chains, six image-heavy chains, and live image tools and follow-ups in both actual clients passed. |
| Current full replay | Control passed | 8/8 small chains; complete image bodies above 22 MB rejected locally with zero upstream attempts. |

## Method

The harness injects checkpoint handling only in research processes. It uses
production normalization, streaming translation, serialization, original-history
fingerprints, locking, and the 19,000,000-byte guard. Image resizing was disabled
in live comparisons. Transport and rate-limit retries were disabled. Each
large-chain step made exactly one upstream attempt, with no fallback.

For each strategy, basic probes used three independent JSON and three independent
SSE chains with store: false, plus one of each with store: true. Output was capped
at 512 tokens with medium reasoning. Success required actual tool dispatch,
matching returned synthetic facts, a completed terminal response, and retained
facts on an ordinary follow-up. Neither storage value rescued direct continuation.
Direct was excluded from paid large-payload qualification after failing these
prerequisites. Four initial authentication failures, before the valid key was
supplied, are recorded separately and excluded from strategy counts.

## Images and transport

All six chains established a 16–17 MB answer without outstanding tools, then used
that answer as their reference during three tool rounds. The reference advanced
after the final answer; the ordinary follow-up used that new reference. Expected
visual values stayed outside prompt and tool-result text. Answers correctly
included earlier and new Page IDs, decimal doses, and rectangle colors.

Bytes below are complete serialized UTF-8 JSON bodies. Maximum continuation body
excludes the initial anchor request. Equivalent history includes the follow-up
and represents what full replay would transmit.

| Fixture | Transport | Initial body | Maximum equivalent history | Maximum continuation body | Result |
|---|---|---:|---:|---:|---|
| Transport PNG | JSON | 16,400,613 | 23,902,861 | 7,502,012 | Pass |
| Transport PNG | SSE | 16,400,612 | 23,902,869 | 7,502,015 | Pass |
| Realistic JPEG/PNG | JSON | 16,648,808 | 24,151,084 | 7,502,028 | Pass |
| Realistic JPEG/PNG | SSE | 16,651,431 | 24,153,693 | 7,502,015 | Pass |
| Parallel JPEG/PNG | JSON | 16,400,803 | 23,621,712 | 7,220,673 | Pass |
| Parallel JPEG/PNG | SSE | 16,648,997 | 23,869,910 | 7,220,672 | Pass |

The transport fixture uses valid PNG ancillary padding to isolate byte limits
without independently exceeding the token window. Realistic initial fixtures
use actual noisy-border JPEG/PNG encodings, repeated to reach 16–17 MB. The first
four chains return one padded PNG per tool round. Two additional chains return
two parallel results per round: a realistic PNG with transport padding and a
realistic JPEG. Both transports preserved the first original image and all six
new image values. No provider error indicating a reconstructed-history transport
limit occurred in these chains.

These are synthetic visual checks, not qualification of private clinical forms,
handwriting, or arbitrary small text.

## Actual clients

Isolated app-server sessions ran installed Codex 0.152.0 and supported Codex
0.167.1. Each live session completed an initial local-image answer, an actual
image-bearing tool turn, and a follow-up. The tool answer read ID 731842, dose
1.25, and blue. The follow-up independently repeated the ID and dose. All three
requests after the initial answer used continuation, with no fallback.

The installed client used view_image; the supported client used its namespaced
execution bridge to call view_image. Temporary configuration and random ports
isolated both clients from the running app. Mock checks also covered first-turn
shell tools and image tools in both clients for direct and anchor strategies.
Initial mock runs that assumed the older tool interface were repaired and
superseded; they are harness errors, not live provider results. The live checks
were repeated with separate assertions for the tool answer and follow-up.

## Offline verification

The full suite passed 127 tests on Python 3.10.20, 3.12.13, 3.13.14, local 3.14,
and Intel Python 3.12 under Rosetta. Stateful reconstruction compares normalized
history exactly, including first-turn and anchored tools, three rounds, parallel
results, custom/namespaced calls, reasoning, and mixed JPEG/PNG. Incremental SSE
arrives in 31-byte chunks with complete arguments in the initial tool event.
The anchor mock rejects references to tool-bearing responses.

Coverage includes changed settings/history, compaction, isolation, expiration,
cancellation, failed delivery, malformed/incomplete responses, serialization,
explicit continuation, structured reference rejection with one replay, generic
errors without replay, and oversized selected/fallback bodies. Ten additional
retry/lifecycle checks and eight resizing checks passed across the injected
strategies. A stateful anchored tool-loop check verifies resized provider history
against original client fingerprints and keeps continuation and image savings
separate. The full-replay image control exceeds 22 MB and returns JSON/SSE
compaction-recognizable errors without transmitting upstream.

## Spending

The previous ledger was snapshotted under its lock: $23.795504 accounted,
217 records, and 134 unresolved reservations. This experiment used only its
separate $20 allowance, with no unused earlier allowance added.

Every upstream attempt was reserved before transmission against complete
effective history, including images retained through references. Estimates count
every image occurrence at 4,096 + ceil(pixels/64) input tokens, conservatively
count text bytes, double input/output rates, and assume no cache discount.
Unknown costs remain held.

New reported charges: **$3.05248**. Unresolved reservations: **$0.60** across
12 attempts. Total accounted: **$3.65248 / $20**; unspent allowance:
**$16.34752**. There were 128 reserved upstream attempts, including authentication
failures and repeated client verification. Raw ledgers, keys, logs, and provider
response IDs are excluded from shareable evidence.

## Limitations and recommendation

Earlier-answer continuation avoids retransmitting images before a valid anchor.
Before a completed tool-free answer exists, it uses full replay. Images added
during a tool loop still accumulate in the suffix until a successful final answer
establishes a newer anchor. That suffix can itself exceed 19 MB. Expired
references may require full replay, which can also exceed the guard. Provider and
model context, retention, and other limits still apply.

Qualification applies to the tested route, model, and clients. It does not
promise that every large conversation succeeds or establish stable provider
behavior across future versions. Implementing and enabling this approach is a
separate production task. The current app, affected chat, existing PR, and
published release remain unchanged.

See [harness instructions](../research/README.md) and
[machine-readable evidence](evidence/tool-continuation-2026-11-11.json).
Raw local outputs remain in ignored build/tool-continuation/. Evidence includes
sanitized terminal outcomes, strategy/transport, exact transmitted and equivalent
bytes, latency, visual checks, client versions, and spending.
