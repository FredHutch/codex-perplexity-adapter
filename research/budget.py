"""Separate $20 ledger, anchored to a locked snapshot of prior reservations."""
import base64
import fcntl
import io
import json
import math
import os
import tempfile
import uuid
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/tool-continuation'
PRIOR = ROOT.parents[1] / 'build/qualification-0.1.13/live-ledger.json'


class LiveBudget:
    def __init__(self, directory=OUT, prior=PRIOR):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'live-ledger.json'
        self.baseline_path = self.directory / 'budget-baseline.json'
        self.lock_path = self.directory / 'budget.lock'
        with self.lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not self.baseline_path.exists():
                with prior.with_suffix('.lock').open('a') as oldlock:
                    fcntl.flock(oldlock, fcntl.LOCK_EX)
                    records = json.loads(prior.read_text())
                    baseline = sum(r.get('actual_cost', r['reserved_max']) for r in records)
                    self._write(self.directory / 'prior-ledger-snapshot.json', records)
                    self._write(self.baseline_path, {'baseline': baseline, 'additional_allowance':20,
                        'prior_records':len(records), 'unresolved_prior_records':sum('actual_cost' not in r for r in records)})
            if not self.path.exists():
                self._write(self.path, [])

    @staticmethod
    def _write(path, value):
        with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as f:
            json.dump(value, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(f.name, path)

    @staticmethod
    def input_allowance(payload):
        def count(value):
            if isinstance(value,str):
                if value.startswith('data:image/'):
                    with Image.open(io.BytesIO(base64.b64decode(value.split(',',1)[1], validate=True))) as im:
                        # Deliberately much larger than documented pixel/750:
                        # one token per 64 pixels, plus 4096 per occurrence.
                        return 4096 + math.ceil(im.width*im.height/64)
                return len(value.encode('utf-8'))
            if isinstance(value,dict): return sum(count(k)+count(v) for k,v in value.items())
            if isinstance(value,list): return sum(map(count,value))
            return 16
        return count(payload)

    def reserve(self, effective_payload, body_bytes, label):
        output=effective_payload.get('max_output_tokens')
        if not isinstance(output,int) or not 1<=output<=512:
            raise ValueError('Qualification requires output <=512')
        allowance=self.input_allowance(effective_payload)
        # Doubled conservative gpt-6.1-sol rates, no caching discount.
        cost=max(.05,2*(allowance*4+output*15)/1_000_000)
        record={'reservation_id':uuid.uuid4().hex,'label':label,'reserved_max':round(cost,6),
                'effective_input_allowance':allowance,'selected_body_bytes':body_bytes}
        with self.lock_path.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            records=json.loads(self.path.read_text())
            if round(sum(r.get('actual_cost',r['reserved_max']) for r in records)+cost,6)>20:
                raise RuntimeError('New $20 experiment budget exhausted before transmission')
            records.append(record); self._write(self.path,records)
        return record

    def settle(self,record,response):
        cost=response.get('usage',{}).get('cost')
        if isinstance(cost,dict): cost=cost.get('total_cost')
        if isinstance(cost,bool) or not isinstance(cost,(int,float)) or not math.isfinite(cost) or cost<0: return
        with self.lock_path.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            records=json.loads(self.path.read_text())
            target=next(r for r in records if r['reservation_id']==record['reservation_id'])
            target['actual_cost']=cost; self._write(self.path,records)

    def summary(self):
        records=json.loads(self.path.read_text())
        used=sum(r.get('actual_cost',r['reserved_max']) for r in records)
        return {'additional_ceiling':20,'accounted':round(used,6),'remaining':round(20-used,6),
                'attempt_reservations':len(records),'unknown_reservations':sum('actual_cost' not in r for r in records)}
