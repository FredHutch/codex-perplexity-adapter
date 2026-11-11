"""Qualification costs must survive stale writers and concurrent reservations."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import qualify_continuation as qualification

PAYLOAD = {"model": "openai/gpt-6.1-sol", "input": "synthetic", "max_output_tokens": 32}


class QualificationBudgetTests(unittest.TestCase):
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
