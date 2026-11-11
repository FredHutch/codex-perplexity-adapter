import json
import tempfile
import unittest
from pathlib import Path
from research.budget import LiveBudget
from scripts.qualify_continuation import image

class ResearchBudgetTests(unittest.TestCase):
    def test_baseline_and_unknowns_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);prior=root/'prior.json';prior.write_text('[{"reserved_max": 9}]')
            b=LiveBudget(root/'new',prior)
            p={'input':'synthetic','max_output_tokens':512}
            one=b.reserve(p,100,'one');b.settle(one,{'status':'failed'})
            self.assertEqual(b.summary()['accounted'],.05)
            self.assertEqual(json.loads(prior.read_text()),[{'reserved_max':9}])
            self.assertEqual(json.loads(b.baseline_path.read_text())['baseline'],9)
            for i in range(399):b.reserve(p,100,'unknown')
            with self.assertRaises(RuntimeError):b.reserve(p,100,'blocked')
    def test_effective_images_count_every_occurrence(self):
        url=image((255,0,0),0)
        one=LiveBudget.input_allowance({'input':[{'image_url':url}]})
        two=LiveBudget.input_allowance({'input':[{'image_url':url},{'image_url':url}]})
        self.assertGreater(one,4096);self.assertGreater(two,one*1.9)
    def test_stale_writers_and_settlements(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);prior=root/'prior.json';prior.write_text('[]')
            a,b=LiveBudget(root/'new',prior),LiveBudget(root/'new',prior)
            p={'input':'x','max_output_tokens':32}
            one=a.reserve(p,1,'a');two=b.reserve(p,1,'b')
            a.settle(one,{'usage':{'cost':{'total_cost':.001}}})
            b.settle(two,{'usage':{'cost':.002}})
            self.assertEqual(a.summary()['accounted'],.003)
