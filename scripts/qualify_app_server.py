"""Synthetic actual-client tool, follow-up and compaction qualification."""
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_perplexity_adapter.app import Settings, create_app
from codex_perplexity_adapter.request_size import encode_request
from scripts.qualify_continuation import Budget, MeteredTransport

DIRECTORY = Path(__file__).resolve().parents[1] / 'build/qualification-0.1.13'


class RPC:
    def __init__(self, process):
        self.process, self.events, self.serial = process, queue.Queue(), 0
        self.pending = []
        def read():
            for line in process.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    pass
        threading.Thread(target=read, daemon=True).start()

    def send(self, method, params=None, ident=None):
        message = {'method': method}
        if params is not None:
            message['params'] = params
        if ident is not None:
            message['id'] = ident
        self.process.stdin.write(json.dumps(message) + chr(10))
        self.process.stdin.flush()

    def until(self, predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.events.get(timeout=max(.01, deadline-time.monotonic()))
            self.pending.append(message)
            if 'id' in message and 'method' in message:
                # Synthetic tests do not authorize requests to execute extra actions.
                self.process.stdin.write(json.dumps({'id': message['id'], 'error': {'code': -32601, 'message': 'Not available in qualification'}}) + chr(10))
                self.process.stdin.flush()
            if predicate(message):
                return message
        raise TimeoutError('App-server event deadline exceeded')

    def call(self, method, params):
        self.serial += 1
        ident = self.serial
        self.send(method, params, ident)
        result = self.until(lambda event: event.get('id') == ident)
        if 'error' in result:
            raise RuntimeError(str(result['error']))
        return result['result']


def run(binary):
    seen = []
    meter = MeteredTransport(Budget())
    app = create_app(Settings(api_key='unused-local-relay', local_token='qualification-token', timeout_seconds=60), meter)
    class Bounded:
        async def __call__(self, scope, receive, send):
            if scope.get('path') != '/v1/responses':
                return await app(scope, receive, send)
            data = b''
            while True:
                event = await receive()
                data += event.get('body', b'')
                if not event.get('more_body', False):
                    break
            payload = json.loads(data)
            payload['max_output_tokens'] = 1024
            seen.append(payload)
            sent = False
            async def once():
                nonlocal sent
                if not sent:
                    sent = True
                    return {'type': 'http.request', 'body': encode_request(payload), 'more_body': False}
                return await receive()
            return await app(scope, once, send)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(Bounded(), log_level='error', access_log=False))
    worker = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    worker.start()
    while not server.started:
        time.sleep(.01)
    report = {'checks': []}
    try:
        with tempfile.TemporaryDirectory(prefix='codex-013-appserver-') as folder:
            root = Path(folder)
            config_home = root / 'codex-home'
            config_home.mkdir()
            (config_home / 'config.toml').write_text(f'''model="gpt-6.1-sol"
model_provider="probe"
model_reasoning_effort="low"
web_search="disabled"
[model_providers.probe]
name="Qualification"
wire_api="responses"
env_key="QUALIFICATION_LOCAL_TOKEN"
supports_websockets=false
base_url="http://127.0.0.1:{port}/v1"
''')
            env = dict(os.environ, CODEX_HOME=str(config_home), QUALIFICATION_LOCAL_TOKEN='qualification-token')
            env.pop('PERPLEXITY_API_KEY', None)
            version = subprocess.check_output([binary, '--version'], env=env, text=True).strip()
            with (root / 'stderr.log').open('w') as errors:
                process = subprocess.Popen([binary, 'app-server', '--stdio'], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, bufsize=1)
                try:
                    rpc = RPC(process)
                    rpc.call('initialize', {'clientInfo': {'name': 'adapter-qualification', 'version': '0.1.13'}, 'capabilities': {'experimentalApi': True}})
                    rpc.send('initialized')
                    result = rpc.call('thread/start', {'cwd': str(root), 'model': 'gpt-6.1-sol', 'modelProvider': 'probe', 'approvalPolicy': 'never', 'sandbox': 'read-only', 'baseInstructions': 'Follow synthetic test requests precisely. Use only the requested shell command. Keep responses short.', 'config': {'model_reasoning_effort': 'low'}})
                    thread = result['thread']['id']
                    for label, prompt in [('tool-loop', 'Run a shell command that prints DESKTOP_ISOLATED_137, then repeat exactly its output.'), ('followup', 'Without using tools, repeat the result from the preceding command.')]:
                        start = len(rpc.pending)
                        before = len(meter.records)
                        rpc.call('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': prompt}]})
                        done = rpc.until(lambda event: event.get('method') == 'turn/completed')
                        recent = rpc.pending[start:]
                        report['checks'].append({'check': label, 'status': done.get('params', {}).get('turn', {}).get('status'), 'marker_received': 'DESKTOP_ISOLATED_137' in json.dumps(recent), 'provider_requests': len(meter.records)-before, 'stats': app.state.continuation.snapshot()})
                        print(json.dumps(report['checks'][-1]), flush=True)
                    before = len(meter.records)
                    start = len(rpc.pending)
                    rpc.call('thread/compact/start', {'threadId': thread})
                    rpc.until(lambda event: event.get('method') == 'item/completed' and event.get('params', {}).get('item', {}).get('type') == 'contextCompaction')
                    rpc.until(lambda event: event.get('method') == 'turn/completed')
                    report['checks'].append({'check': 'manual-client-compaction', 'completed': True, 'provider_requests': len(meter.records)-before})
                    rpc.call('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': 'Without tools, repeat the synthetic command result retained in the summary.'}]})
                    start = len(rpc.pending)
                    done = rpc.until(lambda event: event.get('method') == 'turn/completed')
                    report['checks'].append({'check': 'after-compaction', 'status': done.get('params', {}).get('turn', {}).get('status'), 'marker_received': 'DESKTOP_ISOLATED_137' in json.dumps(rpc.pending[start:]), 'stats': app.state.continuation.snapshot()})
                    report['client'] = version
                    report['request_shapes'] = [{'body_bytes': len(encode_request(p)), 'input_items': len(p.get('input', [])), 'has_tools': bool(p.get('tools'))} for p in seen]
                    report['tool_output_received'] = any(i.get('type') in {'function_call_output','custom_tool_call_output'} and 'DESKTOP_ISOLATED_137' in str(i.get('output')) for p in seen for i in p.get('input',[]) if isinstance(i,dict))
                except Exception as exc:
                    report['error'] = str(exc)
                    print(json.dumps({'last_events': rpc.pending[-8:]}), flush=True)
                finally:
                    process.terminate()
                    process.wait(timeout=10)
            report['stderr_tail'] = (root / 'stderr.log').read_text()[-2000:] if 'error' in report else ''
            output = DIRECTORY / (version.replace(' ', '-') + '-appserver-live.json')
            output.write_text(json.dumps(report, indent=2) + chr(10))
            print(json.dumps(report), flush=True)
    finally:
        server.should_exit = True
        worker.join(timeout=10)
    return report

if __name__ == '__main__':
    run(str(Path(sys.argv[1]).resolve()))
