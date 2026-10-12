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
