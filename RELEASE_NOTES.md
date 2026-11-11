# v0.1.14 — 19 MB request guard

The adapter now accepts complete requests up to **19,000,000 bytes**, increased from 18 MB. The macOS app displays **Limit: 19 MB**. Conversation reuse and tool handling are unchanged.

Provider and model limits still apply. If a chat reaches the request limit, use `/compact` and retry; an individually oversized attachment or tool result must be reduced.

See the [verification report](VERIFICATION_0.1.14.md) for testing details and [GitHub Releases](https://github.com/FredHutch/codex-perplexity-adapter/releases) for previous versions.
