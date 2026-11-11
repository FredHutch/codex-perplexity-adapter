"""Local Anchor Test app entry point; never used by release packaging."""
from unittest.mock import patch
from codex_perplexity_adapter import app as production
from codex_perplexity_adapter.cli import main
from research.tool_continuation import experiment


if __name__ == '__main__':
    retries = production.RATE_LIMIT_RETRIES
    # The qualification harness disables retries for diagnostic probes. This
    # manually started test app retains the existing production retry policy.
    with experiment('anchor'), patch.object(production, 'RATE_LIMIT_RETRIES', retries):
        main()
