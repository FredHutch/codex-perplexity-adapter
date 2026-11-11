# v0.1.14 — 19 MB request guard

Raise the complete serialized UTF-8 request budget from 18,000,000 to 19,000,000 bytes. Bodies exactly at the limit are accepted locally; larger bodies retain the compaction-recognizable JSON/SSE error. The app displays “Limit: 19 MB”. Continuation, tool replay, content preservation, and CLI controls are unchanged.

This leaves roughly 1 MB below the 20 MB downstream limit observed on the existing route. Provider conversion and model context limits still apply. Synthetic JSON, SSE, and tool-result replay requests of 18,987,086–18,987,613 bytes completed on the existing Perplexity route. These valid 32×32 PNGs contain large ancillary chunks to exercise transport bytes; they do not qualify large visual-context workloads.

All 104 tests pass on Python 3.10, 3.12, 3.13, local 3.14, and Intel 3.12 under Rosetta. A real Codex client recognized a 19,840,156-byte rejection, compacted, and continued with its marker retained. See [verification](VERIFICATION_0.1.14.md). GitHub Actions remains disabled; release acceptance uses local checks.

# v0.1.13

Fix Perplexity continuation failures by replaying full normalized history during local tool loops. A response dispatching standard, custom, or namespaced tools invalidates its checkpoint. A successful delivered answer with no outstanding tool calls establishes a new checkpoint, allowing ordinary continuation to resume. Explicit continuation, store: false, exact history checks, cache bounds, and the 18 MB guard remain active. Tool replay does not remove the provider cap.

The macOS app always enables automatic conversation reuse. The checkbox is removed, and an old saved off preference no longer affects startup. CLI users retain `--continuation auto|disabled` for troubleshooting. Provider retention is unchanged: `store: false` and disabling adapter reuse do not disable Perplexity's server-side persistence.

The 580-point-wide AppKit window now fits its content with 20-point outer margins and 10-point vertical spacing. Four wrapping, 12-point statistics rows show every counter. Status changes resize the window while preserving its top edge. The normal stopped content is 233 points high and the running content is 284, replacing the fixed 425-point height.

The app and authenticated status show tool replay counts. Checkpoints and byte savings commit only after successful completion and delivery. Failed, incomplete, malformed, and cancelled responses invalidate state. Top-level provider stream failures retain provider fields inside Codex's expected failed-response envelope. Injected budget notices are removed.

Fresh desktop chats can contain a namespaced tool notification without a call ID. Preserve its content as user-role text, and remove unsupported namespace/name fields from paired tool results while retaining call IDs. This fixes the additional HTTP 400 discovered during desktop qualification.

All **101 tests** pass on Python 3.10.20, 3.12.13, 3.13.14, local 3.14.6, and Intel Python 3.12.13 under Rosetta. Installed CLI 0.152.0 and current CLI 0.167.1 pass isolated mock tool loops. Current CLI 0.167.1 and the desktop backend 0.167.0-alpha.3.2 also complete live tool loops. Dedicated current-client and desktop-backend tests prove ordinary continuation after tools, manual compaction, and recovery with the retained result. A separate real desktop chat completes its tool round and follow-up; its successful HTTP traffic is attributed in client logs. A real client recognizes an accumulated-history rejection at the actual 18 MB guard and recovers after manual compaction using a local mock provider. The controlled live image-history follow-up saves 99.9985% of request bytes.

Native launcher tests verify saved preference absent/true/false, stopped/starting/running/error/unavailable states, large counters, wrapped diagnostics, preserved window top, and 20-point bottom margins. A separate GUI preview verifies the stopped and running layouts, paste, Return, Tab, Stop, and minimize controls without starting a provider connection.

Both macOS packages are rebuilt from the final launcher source, Developer ID signed, notarized, and stapled. Fresh final ZIP extractions pass strict nested signatures, embedded-source and architecture checks, ticket validation, Gatekeeper acceptance, startup, authenticated status, and JSON/SSE size errors. Intel execution was checked under Rosetta. Submission IDs and current package hashes are recorded in the [verification report](VERIFICATION_0.1.13.md).

Qualification accounting is **$23.644674**, below the renewed **$24.883652** ceiling. This includes unknown charges and unused retry reservations, rather than confirmed billing. The launcher changes required no additional paid calls. See [verification report](VERIFICATION_0.1.13.md) for the cost audit, evidence, and limitations. The installed app remains untouched.

---

# v0.1.12

Add stateless request-byte notices and authenticated adapter-wide numeric status, shown in the macOS app. Exact UTF-8 measurement includes all forwarded fields and notice overhead. Warnings at 10, 12, and 15 decimal MB guide batch sizes and compaction; the 18 MB guard and 0.1.11 streaming compaction recovery remain authoritative. This historical notice feature is removed in 0.1.13.

No content store, archive, retained content cache, summaries, retrieval interface, image upload, or additional provider calls are introduced. Status retains only numerical operational counters in process memory. Notices never include client-derived text and do not alter client history. Request buffers are scoped to active requests, without a promise of secure erasure or protection from OS swap/crash capture.

All 71 tests pass on Python 3.10.20, 3.12.13, 3.13.14, and local Python 3.14. Coverage includes exact notice overhead and thresholds, Unicode, original payload preservation, mixed tool images, authentication, status reset, failures, cancellation, secret markers, and absence of content file writes.

Actual Codex CLI 0.152.0 `/compact` completed with notices enabled against a synthetic upstream and a 100,000-byte test budget. Retries shrank from 203,628 through 199,869 and 199,262 to 19,088 bytes. The signed arm64 packaged server passed health, status authentication, and oversized JSON/SSE checks. All six bundled adapter modules match source; metadata reports 0.1.12 and strict nested signature verification passes.

The running local app passed live text, incremental streaming, ordinary text-tool follow-up, namespaced and custom image tools in JSON and streaming mode, commentary completion, and image replay. Actual Codex CLI image input and follow-up replay identified withheld synthetic colors correctly. The CLI reported fallback model metadata.

Synthetic review requests at 10.1, 12.1, and 15.1 MB produced calls to save findings, reduced batches, and requested compaction pauses. At 15.1 MB the model selected 800,000 bytes of new output, below the notice's 1 MB recommendation. The verification client saved only synthetic findings in its designated test file; the adapter executed no tools. An unexpectedly oversized single result returned an actionable recognized failure. Large whitespace-only text padding produced a generic upstream HTTP 400; valid PNG ancillary padding allowed high-byte tests without excessive text-token input. Model behavior is observational and cannot guarantee compliance.

All 71 tests also pass under Intel Python 3.12. Both macOS apps are Developer ID signed, notarized, and stapled. Fresh final ZIP extractions pass strict nested signature checks, ticket validation, and Gatekeeper acceptance. Intel packaged health, authenticated status, and JSON/SSE oversized-request recovery pass under Rosetta. The Intel frozen modules and Python wheel match source. Apple submission IDs: 339b138b-1df8-4464-8497-68e6521a04e7 (Apple Silicon) and b57a9327-0bca-4548-ae96-9bed872a0fa6 (Intel).

---

# v0.1.11

Fix /compact failing repeatedly at the request-size limit. Streaming requests now receive a response.failed SSE event containing context_length_exceeded for local preflight failures and recognized upstream HTTP size errors. Nonstreaming requests keep HTTP 400 behavior. No history is trimmed by the adapter.

An actual Codex CLI 0.152.0 /compact test against the patched adapter with a synthetic upstream and reduced budget retried progressively smaller history and completed successfully. Request sizes fell from 198,892 bytes to 18,377 bytes under a 100,000-byte test budget. The user subsequently confirmed that compaction of the original affected conversation succeeded with the local 0.1.11 app. All 61 tests pass on Python 3.10.20, 3.12.13, 3.13.14, and local Python 3.14.

Both packaged servers pass streaming and nonstreaming oversized-text checks. All 61 tests also pass under Intel Python 3.12. Both apps are Developer ID signed, notarized, and stapled; strict signatures and Gatekeeper acceptance were verified after final ZIP extraction. Apple submission IDs: 219d03a0-1bab-4219-a41a-0c4a2ab5814f (Apple Silicon) and 1c0453ca-c405-45ca-846c-b69047bdd00c (Intel). The wheel and bundled adapter modules match source. GitHub Actions is disabled; validation used the local matrix.


---

# v0.1.10

Fix image-bearing tool results being flattened into JSON text. Custom and ordinary tool results now preserve ordered native image and text parts, image data, and tool-call associations. Codex's `original` image detail hint maps to `high`; text-only behavior remains compatible.

Handle the transport-size limit for complete requests, including instructions, tool definitions, text history, tool results, and attachments. The adapter measures the actual UTF-8 JSON body against an 18,000,000-byte budget, leaving headroom below Perplexity's downstream 20 MB limit. Oversized requests and recognized provider transport-size errors return `context_length_exceeded`, including failed streaming responses. Other provider errors retain their behavior.

Use `/compact` and retry when a chat reaches the limit. This change does not raise the provider's limit or guarantee automatic compaction. It does not discard history, truncate tool results, or reduce image quality; a single oversized attachment or result may need reduction at its source.

## Verification

- All 60 tests pass on Python 3.10.20, 3.12.13, 3.13.14, and local Python 3.14. Coverage includes image preservation, replay, text compatibility, exact UTF-8 size boundaries, text-only oversized requests, provider HTTP 400/413 conversion, failed-stream conversion, and unrelated-error preservation.
- The running Apple Silicon packaged app passed live text, incremental streaming, namespaced and custom image tool calls in JSON and streaming mode, image-history replay, ordinary text-tool follow-up, commentary completion, and event-number checks. Synthetic images were recognized against expected answers withheld from prompts.
- Actual Codex CLI 0.152.0 image viewing and follow-up replay passed against the image fix; the CLI used fallback metadata for the model.
- Both JSON and streaming requests containing over 22 MB of text returned the recognized context-limit error from the packaged server. Recovery of the original oversized user conversation was not verified.
- The Intel frozen server matches release source, starts under Rosetta, serves its model registry, and rejects oversized text requests correctly. All 60 tests also pass under Intel Python 3.12.
- Both macOS builds are Developer ID signed and accepted by Apple. Stapled tickets, strict nested signatures, and Gatekeeper acceptance were verified, including fresh extraction of final ZIPs. Submission IDs: da8c09e6-503e-41bc-8429-9090e8e416f8 (Apple Silicon) and 23a8d002-266f-4eef-8dbf-29d7d55fbbe8 (Intel).
- The Python wheel's adapter modules match the release source.

---

# v0.1.8

Fix streamed commentary disappearing from conversation history and being repeated on later turns. A live diagnostic reproduced the defect in v0.1.7: “Checking the fixture.” arrived as text deltas, but had no message completion event and was absent from the terminal response output. A controlled follow-up repeated the update with that history and stopped repeating it when the missing commentary was restored.

The adapter now completes unfinished commentary before tool calls and includes it in completed response output. It preserves known assistant phases across stream events; when upstream omits phases, messages before tool calls become commentary and terminal answers become final answers. Recognized events share one sequence counter, fixing backwards numbering when ordinary function calls mixed with normalized text events. Failed and incomplete responses remain visible, and existing completion events are not duplicated.

## Verification

- All 49 local tests pass, including the live-shaped missing-commentary stream, phase present only at message start, mixed event numbering, multiple messages/tools/content parts, duplicate completion prevention, terminal-only messages, and failure preservation.
- All 49 tests also pass on Python 3.10.20, 3.12.13, and 3.13.14. The Python wheel builds successfully and its adapter modules match the repaired source.
- Live checks through the patched source adapter passed: commentary appears in both item-completion events and terminal output, sequence numbers increase consistently, and ordinary follow-up history returns “PASS” without repeating “Checking the fixture.”
- An actual Codex CLI 0.152.0 run completed three separate shell tool-result turns. Persisted history contains each unique commentary marker exactly once with `phase: "commentary"`, followed by the expected final answer with `phase: "final_answer"`. The tools printed synthetic markers and did not modify files.
- Both Apple Silicon and Intel packaged servers passed live commentary persistence, consistent sequence numbering, tool-result follow-up, and repetition checks. App metadata reports 0.1.8, launcher/server architectures match, all frozen adapter modules match the release source, and the bundled certificate data matches patched `certifi` 2026.10.18.
- Apple accepted both builds on November 9, 2026. Notarization tickets are stapled and validated; strict nested signatures, hardened runtime, expected server entitlements, and Gatekeeper acceptance passed. The final ZIPs were re-extracted and their tickets, signatures, version, and Gatekeeper acceptance were verified again. Submission IDs: `1128197e-1fe3-45ab-986e-536787e5d63f` (Apple Silicon) and `243e0a28-f3c9-4f4d-b0d4-4020c2c7c116` (Intel).

---

# v0.1.7

Fix conversations that continue following obsolete Plan instructions after Codex switches to Default mode. The adapter now retains the latest explicit developer collaboration mode and removes superseded mode blocks from replayed history. Permissions and other instructions remain intact; user messages, assistant replies, and tool outputs do not select the mode.

Pin the patched `certifi` 2026.10.18 certificate bundle, removing affected emSign roots identified by GitHub's critical dependency advisory. Both macOS apps include the patched bundle.

Both macOS downloads are Developer ID signed, notarized by Apple, and include stapled notarization tickets. Gatekeeper accepts both as “Notarized Developer ID.”

## Verification

- All 41 local tests passed, including transitions in both directions, repeated transitions, text content blocks, preservation of unrelated instructions, and rejection of mode changes from untrusted content. A long replay regression covers 30 rounds of refusals and tool call/result pairs through both JSON and streaming requests; a streaming regression verifies commentary/final-answer phases and text preservation.
- Final live Perplexity checks against `openai/gpt-6.1-sol` passed with a user-provided temporary key: Default returned a tool call after 30 prior Plan-mode refusals; switching back to Plan returned planning text without a write tool call; repeated mode switches followed the latest mode. A streamed namespaced tool call and its streamed result follow-up passed; the follow-up confirmed completion without another tool call or Plan-mode refusal. Tools were synthetic and did not execute file writes. The initial environment key returned HTTP 401.
- All 41 tests also passed on Intel Python 3.12. Both macOS ZIPs passed architecture and code-signature checks, bundle metadata reported 0.1.7, and the frozen adapter modules matched the release source. Both bundled servers started and returned the expected model registry with dummy credentials. The Python wheel built successfully.
- After the certificate dependency update, both rebuilt apps passed startup and model-registry checks, and the bundled certificate version and CA data matched patched `certifi` 2026.10.18. Final live checks through both notarized packaged servers passed with a replacement temporary key: each connected to Perplexity and returned a Default-mode tool call after 30 prior Plan-mode refusals. The earlier temporary key had returned HTTP 401 during the packaged recheck.
- Short synthetic probes also passed before the fix; they did not independently reproduce the original long-conversation failure. The affected local developer history confirmed the obsolete Plan block remained alongside the later Default block.
- Apple accepted both rebuilt packages with patched `certifi` on November 9, 2026. Tickets were stapled and validated, and both apps passed Gatekeeper and strict code-signature checks. Submission IDs: `bdc34444-0986-4a4b-839f-4d95f31b0667` (Apple Silicon) and `45c1694e-ecbe-4477-874f-4cec53a036ba` (Intel).

---

# v0.1.6

Live adapter verification passed for the scenarios below. Both macOS packages were accepted by Apple for notarization.

## Changes

- Accept `gpt-6.1-sol` and `gpt-6.1` by default; both route to `openai/gpt-6.1-sol`.
- Normalize GPT-6.1 Sol reasoning effort `none` and `minimal` to `low`, preserving other reasoning fields, supported efforts, and omitted reasoning.
- Apply compatibility based on the resolved upstream model for built-in, custom, pinned, and passthrough routes. Remove GPT-6.1 `temperature` and `top_p`; live requests demonstrated that Perplexity rejects either field individually.
- Update package and app metadata to 0.1.6 and include both aliases in the macOS About dialog.
- Add standard Minimize (⌘M) and Hide (⌘H) menu commands. Both keep the adapter running and allow restoring the window from the Dock.

## Verification

Verification on October 12, 2026:

| Check | Result |
| --- | --- |
| Python 3.10.20, 3.12.13, and 3.13.14 | All 36 tests passed on each runtime |
| Local Python 3.14 | All 36 tests passed |
| Apple Silicon and Intel ZIPs | Built with the existing scripts |
| Extracted bundle versions and model aliases | Matched 0.1.6 and the adapter registry |
| Launcher and server architectures | Matched arm64 and x86_64 respectively |
| Nested code signatures | Passed `codesign --verify --deep --strict` outside the sandbox; Developer ID signed with hardened runtime |
| Bundled server startup and `/v1/models` | Passed for both architectures on temporary local ports with dummy credentials |
| Window shortcuts | ⌘M minimized an isolated test app and Window menu restored it; ⌘H hid the app and raising the window restored it |
| Live aliases and reasoning | Both aliases returned `openai/gpt-6.1-sol`; low, medium, high, xhigh, max, omitted reasoning, and normalized none/minimal passed |
| Live incremental text streaming | Passed; first delta arrived before stream completion |
| Live namespaced and custom tools | JSON and streaming calls plus result follow-ups passed; namespaced arguments arrived in completed events without separate argument deltas |
| Sampling compatibility | `temperature` and `top_p` each returned HTTP 400; adapter now removes both, rebuilt-app requests with both sampling fields and minimal reasoning passed for both aliases (HTTP 200, expected model and output) |
| Apple notarization | Accepted for both architectures; tickets stapled and validated, Gatekeeper accepted both apps |

Automated tests cover both aliases, routing sources, reasoning efforts, sampling removal, overrides, JSON and streaming tools, and tool-result history. Existing regression coverage includes incremental stream delivery and upstream error forwarding.

The environment credential returned HTTP 401 during the initial probe. A user-entered key in the preview app subsequently passed live text, reasoning, streaming, and tool checks against `openai/gpt-6.1-sol`. Sampling checks demonstrated HTTP 400 for each field individually, so both are removed for this upstream route. Automated tests passed again on all three matrix runtimes and both signed ZIPs were rebuilt. Final sampling-bearing requests through the restarted preview app passed for both aliases: HTTP 200, completed response from `openai/gpt-6.1-sol`, and the expected `OK` output. Live adapter verification is complete for these scenarios; this does not cover every Codex feature or workload.

Apple accepted both signed packages on October 12, 2026. Notarization submission IDs: `103bc9cc-a179-430d-ad48-5df522a0c6de` (Apple Silicon) and `96c32458-5f40-4e9d-b971-9d965af459aa` (Intel). The distribution ZIPs contain the stapled apps.
