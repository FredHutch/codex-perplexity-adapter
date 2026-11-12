# v0.1.16 — Compact launcher and menu-bar controls

- Oversized-image recovery is automatic in the app and enabled by default in the CLI. CLI users can preserve original detail with --no-resize-images-on-limit; the positive flag remains compatible.
- **Details** starts collapsed and contains all statistics and the resizing readability note. Connection status and errors stay visible.
- Close the window or use **Hide to Menu Bar** to keep the adapter running without a Dock icon. The menu bar provides Show, Start, Stop, About and Quit. **⌘Q** stops the adapter.

The 19 MB guard, 18 MB resizing target, continuation behavior and work allowances are unchanged. Resizing can reduce small-text readability and cannot guarantee that requests fit independent provider or model limits. Use /compact when needed.

See [release verification](VERIFICATION_0.1.16_RELEASE.md) and [GitHub Releases](https://github.com/FredHutch/codex-perplexity-adapter/releases) for past versions.
