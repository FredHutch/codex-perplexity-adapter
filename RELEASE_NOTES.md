# v0.1.14 — 19 MB request guard

An unpublished **0.1.15rc1** candidate adds opt-in oversized-image recovery. It remains unqualified because synthetic form readability checks failed. See [candidate notes and evidence](VERIFICATION_0.1.15.md). Stable v0.1.14 and its assets are unchanged.

The adapter now accepts complete requests up to **19,000,000 bytes**, increased from 18 MB. The macOS app displays **Limit: 19 MB**. Conversation reuse and tool handling are unchanged.

Perplexity's API reports a **20 MB downstream transport limit** on the route used by this adapter. The adapter's **19 MB limit** leaves roughly 1 MB of headroom below it. This applies to the complete request, including conversation history, tools, and images; model token limits also apply. If a chat reaches the request limit, use `/compact` and retry; an individually oversized attachment or tool result must be reduced.

See the [verification report](VERIFICATION_0.1.14.md) for testing details and [GitHub Releases](https://github.com/FredHutch/codex-perplexity-adapter/releases) for previous versions.
