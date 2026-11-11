"""Validate local evidence and emit a sanitized, shareable qualification report."""
import json
import re
from research.budget import OUT, ROOT, LiveBudget


def load(name):
    return json.loads((OUT / name).read_text())


def run():
    basic_all = load('basic-live.json')
    basic = [c for c in basic_all if not any(x.get('http_status') == 401 for x in c['checks'])]
    counts = {}
    for strategy in ('replay', 'direct', 'anchor'):
        chains = [c for c in basic if c['strategy'] == strategy]
        counts[strategy] = {'passed': sum(c['passed'] for c in chains), 'total': len(chains)}
        assert len(chains) == 8
        assert sum(c['passed'] for c in chains) == (0 if strategy == 'direct' else 8)
    images = load('image-live.json')
    parallel = load('parallel-live.json')
    assert len(images) == 4 and len(parallel) == 2
    assert all(c['passed'] for c in images + parallel)
    for chain in images + parallel:
        assert 16_000_000 <= chain['checks'][0]['full_body_bytes'] <= 17_000_000
        assert all(len(c['requests']) == 1 and c['requests'][0]['continued']
                   and c['requests'][0]['body_bytes'] <= 19_000_000 for c in chain['checks'][1:])
    clients = [load(f'client-images-live-anchor-codex-cli-{v}.json') for v in ('0.152.0', '0.167.1')]
    assert all(c['passed'] and c['followup_visual_retained'] for c in clients)
    matrix = {}
    for runtime in ('3.10', '3.12', '3.13', 'local', 'intel'):
        log = (OUT / f'matrix-{runtime}.log').read_text()
        count = re.search(r'Ran (\d+) tests', log)
        assert count and log.rstrip().endswith('OK'), runtime
        matrix[runtime] = int(count[1])
    for name in ('experimental-regressions.log', 'resize-regressions.log'):
        log = (OUT / name).read_text()
        assert log.count('\nOK') == 2 and 'FAILED' not in log
    ledger = load('live-ledger.json')
    money = {**LiveBudget().summary(),
             'reported_cost': round(sum(e.get('actual_cost', 0) for e in ledger), 6),
             'unresolved_cost_reserved': round(sum(e['reserved_max'] for e in ledger if 'actual_cost' not in e), 6),
             'baseline': load('budget-baseline.json')}
    data = {'date': '2026-11-11', 'source_commit': 'a50b5d463d8d6f15b264c1f04862475409d9a898',
            'model': 'gpt-6.1-sol', 'reasoning': 'medium', 'max_output_tokens': 512,
            'qualification': {'direct': 'failed', 'anchor': 'qualified', 'replay': 'control passed'},
            'matrix': matrix, 'basic_counts': counts, 'basic': basic,
            'authentication_failures_before_valid_key': len(basic_all) - len(basic),
            'image_chains': images, 'parallel_image_chains': parallel, 'live_clients': clients, 'spending': money}
    encoded = json.dumps(data, indent=2) + '\n'
    assert not re.search(r'(?i)(?:resp_|pplx-)[a-z0-9_-]{8,}', encoded)
    destination = ROOT / 'docs/evidence'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'tool-continuation-2026-11-11.json').write_text(encoded)
    rows = []
    for chain in images + parallel:
        checks = chain['checks']
        rows.append({'fixture': chain.get('fixture', 'parallel JPEG/PNG'), 'transport': 'SSE' if chain['stream'] else 'JSON',
                     'initial': checks[0]['full_body_bytes'], 'equivalent': max(c['full_body_bytes'] for c in checks),
                     'selected': max(c['requests'][0]['body_bytes'] for c in checks[1:])})
    print(json.dumps({'qualification': data['qualification'], 'spending': money, 'matrix': matrix, 'image_summary': rows}, indent=2))


if __name__ == '__main__':
    run()
