"""Qualification costs must survive stale writers and concurrent reservations."""
import json
import subprocess
import sys
import tempfile
import unittest
import httpx
from pathlib import Path
from unittest.mock import patch

from scripts import qualify_continuation as qualification

PAYLOAD = {"model": "openai/gpt-6.1-sol", "input": "synthetic", "max_output_tokens": 32}


class QualificationBudgetTests(unittest.TestCase):
    def test_release_budget_isolated_and_shared_across_models_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            budget = qualification.Budget(directory=directory, ceiling=5.0)
            for model in ("openai/gpt-6.2-sol", "openai/gpt-6.2-astra"):
                budget.reserve({**PAYLOAD, "model": model, "input": "X" * 10000}, 10000)
            restarted = qualification.Budget(directory=directory, ceiling=5.0)
            while True:
                try:
                    restarted.reserve({**PAYLOAD, "model": "openai/gpt-6.2-astra", "input": "X" * 10000}, 10000)
                except RuntimeError:
                    break
            records = json.loads(restarted.ledger.read_text())
            self.assertLessEqual(sum(r["reserved_max"] for r in records), 5.0)
            self.assertEqual({r["model"] for r in records}, {"openai/gpt-6.2-sol", "openai/gpt-6.2-astra"})
            with self.assertRaises(RuntimeError):
                qualification.Budget(directory=directory, ceiling=5.0).reserve(
                    {**PAYLOAD, "input": "X" * 10000, "model": "openai/gpt-6.2-astra"}, 10000)

    def test_invalid_output_caps_and_nonfinite_costs_cannot_release_reservations(self):
        with tempfile.TemporaryDirectory() as folder:
            budget = qualification.Budget(directory=folder, ceiling=5.0)
            for value in (True, False, 0, -1, 1025, 1.5, None):
                with self.subTest(output=value), self.assertRaises(RuntimeError):
                    budget.reserve({**PAYLOAD, "max_output_tokens": value}, 100)
            record = budget.reserve(PAYLOAD, 100)
            for cost in (float("nan"), float("inf"), -1, True):
                budget.settle(record, {"usage": {"cost": cost}})
            self.assertNotIn("actual_cost", json.loads(budget.ledger.read_text())[0])

    def test_stale_instances_merge_and_settle_only_their_reservation(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(qualification, "DIRECTORY", Path(folder)), \
             patch.object(qualification, "LEDGER", Path(folder) / "ledger.json"):
            first, second = qualification.Budget(), qualification.Budget()
            one = first.reserve(PAYLOAD, 100)
            two = second.reserve(PAYLOAD, 100)
            first.settle(one, {"usage": {"cost": {"total_cost": .001}}})
            second.settle(two, {"usage": {"cost": .002}})
            records = json.loads(qualification.LEDGER.read_text())
            self.assertEqual(len(records), 2)
            self.assertEqual([r["actual_cost"] for r in records], [.001, .002])
            self.assertEqual(len({r["reservation_id"] for r in records}), 2)

    def test_unknown_costs_remain_reserved_and_ceiling_blocks_before_send(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(qualification, "DIRECTORY", Path(folder)), \
             patch.object(qualification, "LEDGER", Path(folder) / "ledger.json"), \
             patch.object(qualification, "LIVE_CEILING", .05):
            budget = qualification.Budget()
            record = budget.reserve(PAYLOAD, 100)
            budget.settle(record, {"status": "failed"})
            with self.assertRaises(RuntimeError):
                qualification.Budget().reserve(PAYLOAD, 100)
            records = json.loads(qualification.LEDGER.read_text())
            self.assertEqual(len(records), 1)
            self.assertNotIn("actual_cost", records[0])
            self.assertEqual(records[0]["reserved_max"], .05)

    def test_processes_share_one_ceiling_without_losing_reservations(self):
        script = '''
import sys
from pathlib import Path
from scripts import qualify_continuation as q
q.DIRECTORY = Path(sys.argv[1])
q.LEDGER = q.DIRECTORY / "ledger.json"
q.LIVE_CEILING = .151
b = q.Budget()
for _ in range(2):
    try:
        b.reserve({"model":"openai/gpt-6.1-sol","input":"synthetic","max_output_tokens":32}, 100)
    except RuntimeError:
        pass
'''
        with tempfile.TemporaryDirectory() as folder:
            processes = [subprocess.Popen([sys.executable, "-c", script, folder],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
            for process in processes:
                out, err = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, err.decode())
            records = json.loads((Path(folder) / "ledger.json").read_text())
            self.assertEqual(len(records), 3)
            self.assertAlmostEqual(sum(r["reserved_max"] for r in records), .15)

    def test_explicit_additional_allowance_preserves_old_reservations(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(qualification, "DIRECTORY", Path(folder)), \
             patch.object(qualification, "LEDGER", Path(folder) / "ledger.json"), \
             patch.object(qualification, "LIVE_CEILING", 10.06):
            qualification.LEDGER.write_text(json.dumps([{"reserved_max": 10.0}]))
            qualification.Budget().reserve(PAYLOAD, 100)
            with self.assertRaises(RuntimeError):
                qualification.Budget().reserve(PAYLOAD, 100)
            records = json.loads(qualification.LEDGER.read_text())
            self.assertEqual(records[0], {"reserved_max": 10.0})
            self.assertEqual(len(records), 2)


class ReleaseTransportBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuation_reserves_full_effective_history_before_every_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            budget = qualification.Budget(directory=folder, ceiling=5.0)
            meter = qualification.MeteredTransport(budget)
            await meter.inner.aclose()
            attempts = []
            async def handler(request):
                attempts.append(request)
                return httpx.Response(200, json={"status": "completed", "output": []})
            meter.inner = httpx.MockTransport(handler)
            meter.relay = None
            full = {"model": "openai/gpt-6.2-astra", "input": "X" * 100000, "max_output_tokens": 1024}
            meter.effective_payload = full
            request = httpx.Request("POST", "https://api.perplexity.ai/v1/responses",
                                   json={**full, "input": "new", "previous_response_id": "synthetic"})
            response = await meter.handle_async_request(request)
            await response.aread()
            await response.aclose()
            records = json.loads(budget.ledger.read_text())
            self.assertGreater(records[0]["reserved_max"], 3.0)
            with self.assertRaises(RuntimeError):
                await meter.handle_async_request(request)
            self.assertEqual(len(attempts), 1)
