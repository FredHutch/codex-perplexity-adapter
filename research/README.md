# Tool-continuation research harness

This directory is qualification code, excluded from the Python wheel. Production now uses the qualified earlier-answer behavior. The direct strategy
remains research-only. Use one strategy per isolated
process; experiment() patches module globals while active.

Strategies: replay uses current production behavior, direct advances after
every successful response including tool dispatches, and anchor retains the
last successful answer without outstanding tools through the loop. Checkpoints
contain keyed hashes and response references, with existing bounds and locking.

Run offline checks from the checkout with its dependencies installed:

    python -m unittest discover -s tests
    python -m research.offline_regressions
    python -m research.resize_regressions
    python -m research.client_mock --strategy anchor --client /absolute/path/to/codex
    python -m research.client_images --strategy anchor --client /absolute/path/to/codex

Live commands spend money and require a separately authorized allowance. The
recorded run used at most $20 beyond its locked historical baseline. The first
LiveBudget initialization snapshots the earlier ledger; do not remove or reset
its files to extend the allowance. Paths are local to this research checkout;
inspect research/budget.py before reproducing elsewhere.

The key is read from owner-only /private/tmp/perplexity-tool-continuation-test-key,
or PERPLEXITY_API_KEY if the file is absent/empty. Keys are never written to
evidence. Loopback clients use synthetic local credentials, fresh temporary
CODEX_HOME, and random ports. No requests are relayed through the running app.

    python -m research.live --strategies replay direct anchor --repeats 3
    # Only run large/client live tests for strategies that passed basic probes.
    python -m research.image_live --strategies anchor
    python -m research.parallel_live
    python -m research.client_images --strategy anchor --client /absolute/path/to/codex --live
    python -m research.report

Each upstream attempt reserves against full effective history before network
transmission. There are no automatic transport/rate-limit retries. A structured
reference rejection may invoke the production one-time full replay; it receives
its own reservation. Unknown charges remain reserved. The primary comparison
leaves resizing disabled and caps output at 512 tokens. Live results append to
ignored build/tool-continuation/; basic probes skip previously recorded cases.

See [findings](../docs/TOOL_CONTINUATION_RESEARCH.md). The production implementation lives in codex_perplexity_adapter/continuation.py;
release packages do not include this injection.
