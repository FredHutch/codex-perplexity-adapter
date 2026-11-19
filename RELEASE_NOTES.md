# v0.1.17 — GPT-6.2 model support

Choose `gpt-6.2-sol` or `gpt-6.2-astra` in Codex. Both models are included in the adapter's model list and the macOS About window, and route to their matching Perplexity models.

The adapter maps `none` and `minimal` reasoning effort to `low` for these models and removes unsupported sampling and log-probability options. These rules also apply through custom, pinned, and passthrough routes.

Existing model names, the 19 MB request guard, automatic oversized-image recovery, and conversation reuse remain available. Resizing can reduce small-text readability; use `--no-resize-images-on-limit` for precision form review.

See [release verification](VERIFICATION_0.1.17_RELEASE.md) for local client, live model, and distribution checks. The downloads include Apple Silicon and Intel macOS apps, a Python wheel, and SHA-256 checksums.
