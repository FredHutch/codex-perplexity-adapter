# v0.1.16 release verification

This release makes oversized-image recovery automatic, collapses statistics behind Details, and adds menu-bar operation. All verification used synthetic content and local mocks; no paid API calls were made. The installed app and its running server were left untouched. GitHub Actions remains disabled.

## Behavior

The app always starts with resizing and continuation enabled, ignoring old resizing preferences. CLI and Settings default resizing to enabled; --no-resize-images-on-limit preserves original image detail. The compatibility positive flag remains accepted, and conflicting flags fail before startup. The image preparation algorithm, 19 MB guard, 18 MB target, 1,024-pixel floor, 500-million-pixel allowance, 60-second soft deadline, earlier-answer continuation and successful-delivery accounting are unchanged.

Details starts collapsed on every launch and retains its state only for the current process. It contains all five statistic rows and the resizing readability note; connection status and errors remain visible. Polling runs only while the window is visible, not minimized, Details is expanded and the server is running. It refreshes immediately when eligible; hiding, collapsing and stopping cancel the task and invalidate stale callbacks.

The menu-bar item exists from launch. Closing the window or using Hide to Menu Bar removes the Dock icon without stopping the server. Show Adapter restores the same window and Dock icon. Menu Start reveals key entry; Stop remains available while starting/running. Quit stops the server. There is no automatic hiding, login item or persisted hidden-state preference; credentials remain memory-only.

**Readability limitation:** resizing can lose decimal punctuation, labels and identifiers in small text. Use the CLI opt-out for precision form review. Earlier [readability and work-budget evidence](VERIFICATION_RESIZE_WORK_BUDGET.md) remains applicable; this release does not change image encoding or qualify small-text accuracy. Provider/model limits and provider persistence still apply.

## Local checks

- All **138 tests pass on each** of Python 3.10.20, 3.12.13, 3.13.14, local 3.14.6, and Intel Python 3.12.13 under Rosetta. Coverage retains exact UTF-8 size boundaries, resized-image handling, metadata/orientation/transparency, work budgets, continuation/tool reconstruction, fallback, compaction, errors, cancellation and concurrent requests.
- Native AppKit tests pass on Apple Silicon and Intel Rosetta with old resizing preferences absent, true and false. Every launch argument list enables resizing on port 4000. Details accessibility action, collapsed/expanded layouts, stopped/starting/running/error/unavailable states, large counters, wrapping, keyboard menu equivalents, stable window top and 20-point bottom margin pass. Ordinary collapsed content height is **234 points**, expanded running height **367 points**. Visual inspection confirms readable wrapping and no reserved statistics area while collapsed.
- Lifecycle tests create an isolated local HTTP child. Close/show changes regular/accessory activation policy, preserves the child and a request in flight, retains Details state, and restores polling. Stale callback generations are rejected after hide/collapse; Quit stops the child. Menu Start reveals the key-entry flow. No production PID file or port is used by this test.
- Packaged arm64 and x86_64 servers pass authenticated loopback TLS JSON/SSE checks with synthetic credentials. A 22.9 MB image-heavy request recovers to **17,993,855 / 17,993,854 bytes** with default settings. Oversized text is rejected before upstream transmission. CLI opt-out rejects the original oversized image request without image savings or upstream traffic; conflicting flags return exit code 2.
- Actual Codex clients **0.152.0** and **0.167.1** recover image requests from **21,586,178 / 21,599,901** to **17,933,998 / 17,947,721 bytes**, complete their turns and complete continuation follow-ups. Both also encounter the production size error for unresizable history, complete manual compaction, and succeed afterward with the retained marker.
- Launcher syntax, shell syntax and whitespace checks pass. Frozen production modules and entry point match source, Pillow native codecs are included, and research injection code is excluded. Architecture and version checks pass for both bundles.

## Distribution

Both apps are Developer ID signed, notarized and stapled. Fresh final-ZIP extractions pass strict deep signature verification, stapled ticket validation, and Gatekeeper as **Notarized Developer ID**. Apple accepted these submissions:

- Apple Silicon: 08ff072c-3615-4ec4-a6ac-79445b7c9e76.
- Intel: 899ea453-a488-4187-93c0-7e6f13d78277.

Release assets contain both macOS ZIPs, the Python wheel and SHA256SUMS.txt. The wheel is built from merged source and inspected for matching module contents, version, dependencies and package scope. Published downloads are checked against the checksum file. See [machine-readable evidence](docs/evidence/release-0.1.16.json).
