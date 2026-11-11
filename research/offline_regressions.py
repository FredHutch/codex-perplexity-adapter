"""Re-run lifecycle/retry/stream regressions with each experimental injection."""
import unittest
from research.tool_continuation import experiment
from tests.test_continuation import ContinuationTests,ManagerTests

NAMES=['test_no_retry_after_stream_output_or_ambiguous_connection',
       'test_malformed_incomplete_streams_missing_id_and_duplicate_completion',
       'test_disabled_and_explicit_continuation',
       'test_production_guard_oversized_continuation_fallback']
for strategy in ('direct','anchor'):
    with experiment(strategy):
        suite=unittest.TestSuite(ContinuationTests(n) for n in NAMES)
        suite.addTest(ManagerTests('test_same_chat_endpoint_serializes_until_stream_completion_and_releases_on_cancel'))
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():raise SystemExit(1)
