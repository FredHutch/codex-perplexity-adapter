# v0.1.7

Fix conversations that continue following obsolete Plan instructions after Codex switches to Default mode. The adapter now retains the latest explicit developer collaboration mode and removes superseded mode blocks from replayed history. Permissions and other instructions remain intact; user messages, assistant replies, and tool outputs do not select the mode.

Both macOS downloads are Developer ID signed, notarized by Apple, and include stapled notarization tickets. Gatekeeper accepts both as “Notarized Developer ID.”

## Verification

- All 41 local tests passed, including transitions in both directions, repeated transitions, text content blocks, preservation of unrelated instructions, and rejection of mode changes from untrusted content. A long replay regression covers 30 rounds of refusals and tool call/result pairs through both JSON and streaming requests; a streaming regression verifies commentary/final-answer phases and text preservation.
- Final live Perplexity checks against `openai/gpt-6.1-sol` passed with a user-provided temporary key: Default returned a tool call after 30 prior Plan-mode refusals; switching back to Plan returned planning text without a write tool call; repeated mode switches followed the latest mode. A streamed namespaced tool call and its streamed result follow-up passed; the follow-up confirmed completion without another tool call or Plan-mode refusal. Tools were synthetic and did not execute file writes. The initial environment key returned HTTP 401.
- All 41 tests also passed on Intel Python 3.12. Both macOS ZIPs passed architecture and code-signature checks, bundle metadata reported 0.1.7, and the frozen adapter modules matched the release source. Both bundled servers started and returned the expected model registry with dummy credentials. The Python wheel built successfully.
- Short synthetic probes also passed before the fix; they did not independently reproduce the original long-conversation failure. The affected local developer history confirmed the obsolete Plan block remained alongside the later Default block.
- Apple accepted both packages on November 9, 2026. Tickets were stapled and validated, and both apps passed Gatekeeper and strict code-signature checks. Submission IDs: `4bf1dd85-f88c-482a-a4cc-a34a69fb2b76` (Apple Silicon) and `b5a19c9f-d89e-4774-b7b0-aa511ffbdc15` (Intel).

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
