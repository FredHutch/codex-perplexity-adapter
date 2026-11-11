"""Repeated large-history anchor chains with parallel JPEG/PNG tool results."""
import asyncio
import json
from research.live import Session, msg, text
from research.fixtures import page, encode, parts, VALUES
from research.budget import OUT, LiveBudget


async def chain(stream, repeat):
    session = Session('anchor', stream, False)
    session.tools = [dict(tool, name=name) for name in ('page_a', 'page_b')
                     for tool in session.tools]
    result = {'strategy': 'anchor', 'stream': stream, 'repeat': repeat,
              'checks': session.results, 'passed': False}
    try:
        images = parts(0, 16_400_000, realistic=bool(repeat))
        response = await session.call('parallel-anchor', [{
            'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'Remember the first synthetic page for later. Reply ACK only without tools.'},
                *images]}])
        if response.get('status') != 'completed':
            return result
        result['initial_body_in_range'] = 16_000_000 <= session.results[-1]['full_body_bytes'] <= 17_000_000
        response = await session.call('parallel-start', [msg(
            'Call BOTH page_a and page_b in parallel with round 1. After BOTH results, '
            'call BOTH with round 2, and then BOTH with round 3. After round 3 finish: '
            'list Page ID, Dose and rectangle color for the first original page and '
            'all six tool pages in order. Do not call tools after round 3.')])
        for round_no in range(1, 4):
            calls = [item for item in response.get('output', []) if item.get('type') == 'function_call']
            if (response.get('status') != 'completed' or len(calls) != 2
                    or {call['name'] for call in calls} != {'page_a', 'page_b'}
                    or any(json.loads(call['arguments']).get('round') != round_no for call in calls)):
                result['parallel_dispatch_failed_round'] = round_no
                return result
            outputs = []
            for call in calls:
                offset = 0 if call['name'] == 'page_a' else 1
                index = (round_no - 1) * 2 + offset + 1
                image = page(index, realistic=True)
                if offset == 0:
                    url = encode(image)
                    padding = max(0, int((2_100_000 - len(url)) * 3 / 4) - 12)
                    url = encode(image, padding=padding)
                else:
                    url = encode(image, 'JPEG')
                outputs.append({'type': 'function_call_output', 'call_id': call['call_id'], 'output': [
                    {'type': 'input_text', 'text': f'{call["name"]} round {round_no} complete. '
                     + ('Continue to the next round after both results.' if round_no < 3 else 'All rounds complete; report the visual values now.')},
                    {'type': 'input_image', 'image_url': url}]})
            response = await session.call(f'parallel-result-{round_no}', outputs)
        result['visual_checks'] = [all(value.lower() in text(response).lower() for value in values)
                                   for values in VALUES[:7]]
        result['equivalent_exceeds_22mb'] = session.results[-1]['full_body_bytes'] > 22_000_000
        result['no_replays_or_retries'] = all(len(check['requests']) == 1
            and check['requests'][0]['continued'] for check in session.results[1:])
        result['passed'] = (result['initial_body_in_range'] and result['equivalent_exceeds_22mb']
            and result['no_replays_or_retries'] and all(result['visual_checks'])
            and response.get('status') == 'completed'
            and not any(item.get('type') == 'function_call' for item in response.get('output', [])))
        if result['passed']:
            response = await session.call('parallel-followup', [msg('Without tools repeat the first original Page ID and Dose, and the last tool Page ID and Dose.')])
            result['followup_visual_retained'] = all(value in text(response)
                for values in (VALUES[0], VALUES[6]) for value in values[:2])
            result['passed'] = response.get('status') == 'completed' and result['followup_visual_retained']
    finally:
        await session.close()
    return result


async def run():
    results = []
    for repeat in range(2):
        result = await chain(bool(repeat), repeat)
        results.append(result)
        (OUT / 'parallel-live.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps({'repeat': repeat, 'passed': result['passed'], 'budget': LiveBudget().summary()}), flush=True)


if __name__ == '__main__':
    asyncio.run(run())
