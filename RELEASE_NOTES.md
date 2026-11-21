# v0.1.19 — Fix Codex subagent task delivery

Codex subagents can now receive assignments and follow-up messages through Perplexity. The adapter requests plaintext task arguments over HTTPS and turns Codex’s handoff into a standard Responses message. Subagent replies remain available when the parent replays conversation history.

If a delegation failed with the plaintext input error in v0.1.18, upgrade and retry the task with a fresh subagent. Assignments already encrypted by the earlier version cannot be recovered by the adapter.

Downloads include notarized macOS apps for Apple Silicon and Intel, a Python wheel, and SHA-256 checksums.
