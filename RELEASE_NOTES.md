# v0.1.20 — Recover subagent handoffs in older chats

Earlier adapter versions could leave encrypted subagent task arguments in conversation history. When Codex replayed that history, the model could return another unreadable assignment. The adapter now omits those arguments during replay and preserves the paired tool results. If a new assignment is unreadable, Codex receives a tool validation error before launching a child so the parent can try again with readable instructions.

Retry failed delegations after upgrading. An existing subagent whose original assignment was encrypted needs a fresh assignment.

Downloads include notarized macOS apps for Apple Silicon and Intel, a Python wheel, and SHA-256 checksums.
