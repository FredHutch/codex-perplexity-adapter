"""Original-history binding and savings accounting with experiments enabled."""
import unittest
from research.tool_continuation import experiment
from tests.test_image_resize import ResizeTests
for strategy in ('direct','anchor'):
    with experiment(strategy):
        suite=unittest.TestSuite(ResizeTests(n) for n in (
            'test_resumed_continuation_uses_original_fingerprints_and_fallback',
            'test_stateful_provider_reconstruction_after_resized_turn',
            'test_parallel_custom_namespaced_tool_replay_and_explicit_continuation',
            'test_counters_not_credited_after_delivery_cancellation'))
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():raise SystemExit(1)
