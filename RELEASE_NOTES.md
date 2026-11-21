# v0.1.18 — Codex subagent handoffs

The adapter translates plaintext Codex subagent tasks and replies into standard user messages. This fixes the `unknown item type "agent_message"` error during task delivery and when a conversation replays a child’s reply.

Handoffs containing `encrypted_content` return a local `unsupported_agent_message` error with the original input index. Codex must provide a readable task for the child to receive it.

The downloads include Apple Silicon and Intel macOS apps, a Python wheel, and SHA-256 checksums.
