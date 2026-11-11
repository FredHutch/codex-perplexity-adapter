# v0.1.15 — Image recovery and tool conversation reuse

- Reuse the latest completed answer during tool rounds, preserving earlier images without resending their payloads. Before an answer exists, full history is sent.
- Resize older inline JPEG/PNG images only when a selected request exceeds **19 MB**, aiming for **18 MB** with a **1,024-pixel** minimum long edge. The app’s saved resizing control is initially enabled; CLI users opt in with --resize-images-on-limit.
- Allow **500 million decoded pixels** and **60 seconds of active preparation** per request. Candidates are generated lazily; cancelled work earns no savings. The deadline is soft: native processing already running can finish.

**Small-text limitation:** Resizing can lose decimal punctuation or misread identifiers. Disable it for precision form review. Conversation reuse and resizing cannot guarantee that every chat fits Perplexity’s observed 20 MB downstream transport limit or independent model limits. The adapter keeps its 19 MB guard; /compact and retry if needed.

Apple Silicon and Intel apps are Developer ID signed, notarized and stapled. See [release verification](VERIFICATION_0.1.15_RELEASE.md) and [GitHub Releases](https://github.com/FredHutch/codex-perplexity-adapter/releases) for previous versions.
