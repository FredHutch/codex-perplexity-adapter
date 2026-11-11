"""Opt-in 19 MB live qualification, preserving the prior cost ledger."""
import asyncio
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from scripts import qualify_continuation as q
from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.request_size import REQUEST_BUDGET_BYTES, encode_request

DIRECTORY = Path('build/qualification-0.1.14')

async def run():
    baseline_path = DIRECTORY / 'budget-baseline.json'
    budget = q.Budget()
    if not baseline_path.exists():
        baseline = sum(c.get('actual_cost', c['reserved_max']) for c in budget.calls)
        baseline_path.write_text(json.dumps({'baseline': baseline, 'additional_allowance': 10}))
    baseline = json.loads(baseline_path.read_text())['baseline']
    q.LIVE_CEILING = baseline + 10
    transport = q.MeteredTransport(budget)
    key_path = Path('/private/tmp/perplexity-v014-test-key')
    if key_path.exists() and key_path.stat().st_mode & 0o077:
        raise RuntimeError('Test key file must have owner-only permissions')
    key = key_path.read_text().strip() if key_path.exists() else os.environ['PERPLEXITY_API_KEY']
    app = create_app(Settings(api_key=key, local_token='qualification-token', timeout_seconds=180), transport)
    checks = []
    report = {'guard_bytes': REQUEST_BUDGET_BYTES, 'checks': checks, 'passed': False}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated', timeout=240) as client:
        async def call(label, payload):
            before = len(transport.records)
            response = await client.post('/v1/responses', headers=q.HEADERS, json=payload)
            result = q.completed(response)
            records = transport.records[before:]
            checks.append({'label': label, 'http_status': response.status_code, 'status': result.get('status'),
                           'bodies': [r['body_bytes'] for r in records], 'text': q.text(result),
                           'error': result.get('error')})
            (DIRECTORY / 'live-progress.json').write_text(json.dumps(report, indent=2) + chr(10))
            assert result.get('status') == 'completed', checks[-1]
            return result
        for stream in (False, True):
            payload = {'model': 'gpt-6.1-sol', 'stream': stream, 'max_output_tokens': 128,
                'input': [{'role': 'user', 'content': [
                    {'type': 'input_text', 'text': 'Reply only with the dominant color of this synthetic image.'},
                    {'type': 'input_image', 'image_url': q.image((255, 0, 0), padding=14_240_000)}]}]}
            result = await call('near-limit-sse' if stream else 'near-limit-json', payload)
            assert 'red' in q.text(result).lower()
            assert 18_900_000 <= checks[-1]['bodies'][0] <= REQUEST_BUDGET_BYTES
        tools = [{'type': 'function', 'name': 'guard_probe', 'description': 'Return a synthetic test marker.',
                  'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'strict': True}]
        history = [q.msg('Call guard_probe, then report its marker and the dominant image color.')]
        first = await call('tool-dispatch', {'model': 'gpt-6.1-sol', 'input': history, 'tools': tools,
            'tool_choice': {'type': 'function', 'name': 'guard_probe'}, 'max_output_tokens': 128})
        calls = [i for i in first['output'] if i.get('type') == 'function_call']
        assert len(calls) == 1
        history += first['output'] + [{'type': 'function_call_output', 'call_id': calls[0]['call_id'],
            'output': [{'type': 'input_text', 'text': 'GUARD_MARKER_914'},
                       {'type': 'input_image', 'image_url': q.image((0, 0, 255), padding=14_240_000)}]}]
        result = await call('near-limit-tool-full-replay', {'model': 'gpt-6.1-sol', 'input': history,
            'tools': tools, 'tool_choice': 'none', 'max_output_tokens': 128, 'stream': True})
        assert 'GUARD_MARKER_914' in q.text(result) and 'blue' in q.text(result).lower()
        assert 18_900_000 <= checks[-1]['bodies'][0] <= REQUEST_BUDGET_BYTES
        report['status'] = app.state.continuation.snapshot()
        assert report['status']['tool_boundary_replays'] >= 1
        report['passed'] = True
    return report

if __name__ == '__main__':
    try:
        report = asyncio.run(run())
    except Exception as exc:
        report = {'passed': False, 'error': str(exc)}
    (DIRECTORY / 'live-guard.json').write_text(json.dumps(report, indent=2) + chr(10))
    print(json.dumps(report))
    if not report['passed']:
        raise SystemExit(1)
