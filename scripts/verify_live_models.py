#!/usr/bin/env python3
"""Opt-in live Responses acceptance; only changes a temporary test directory."""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pcl_codex_bridge.http_client import safe_http_error


def isolated_server(port, gateway_url):
    """Use the shipped provider configuration without touching real Codex state."""
    from contextlib import contextmanager

    @contextmanager
    def running():
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from pcl_codex_bridge.opencodex_sidecar import (
            configure_sidecar, installed_runtime, sidecar_environment,
        )
        runtime = installed_runtime()
        with tempfile.TemporaryDirectory(prefix='pcl-live-sidecar-') as temp:
            home = Path(temp)
            (home / 'codex').mkdir(mode=0o700)
            previous = os.environ.get('CODEX_HOME')
            os.environ['CODEX_HOME'] = str(home / 'codex')
            try:
                configure_sidecar(runtime, gateway_url,
                                  ['GLM-5.2', 'DeepSeek-V4-Pro', 'DeepSeek-V4-Flash-0731', 'Kimi-K3'],
                                  port=port, config_home=home / 'opencodex')
                with (home / 'server.log').open('w') as log:
                    process = subprocess.Popen(
                        [str(runtime.bun), str(runtime.cli), 'start', '--port', str(port)],
                        env=sidecar_environment(home / 'opencodex'), stdout=log, stderr=log,
                    )
                    try:
                        for _ in range(60):
                            if process.poll() is not None:
                                raise RuntimeError('Isolated OpenCodex failed to start')
                            try:
                                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                                    f'http://127.0.0.1:{port}/healthz', timeout=1).close()
                                break
                            except OSError:
                                time.sleep(0.5)
                        else:
                            raise RuntimeError('Isolated OpenCodex did not become healthy')
                        yield
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            finally:
                if previous is None:
                    os.environ.pop('CODEX_HOME', None)
                else:
                    os.environ['CODEX_HOME'] = previous
    return running()


def post(base, path, body):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        base.rstrip('/') + path,
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'},
    )
    try:
        response = opener.open(request, timeout=180)
    except urllib.error.HTTPError as exc:
        raise safe_http_error(exc) from None
    with response:
        if 'text/event-stream' not in response.headers.get('Content-Type', ''):
            return json.load(response)
        for line in response:
            if not line.startswith(b'data: '):
                continue
            raw = line[6:].strip()
            if raw == b'[DONE]':
                continue
            event = json.loads(raw)
            if event.get('type') == 'response.failed':
                raise RuntimeError(str(event.get('response', {}).get('error')))
            if event.get('type') == 'response.completed':
                return event['response']
        raise RuntimeError('Stream ended without response.completed')


def verify(base, model, compaction_version=1):
    started = time.monotonic()
    result = {'model': model, 'compaction_version': compaction_version, 'checks': {}}
    try:
        response = post(base, '/responses', {
            'model': model, 'stream': True, 'max_output_tokens': 4096,
            'reasoning': {'effort': 'low'},
            'input': [{'role': 'user', 'content': 'Reply only LIVE_OK.'}],
        })
        text = ''.join(c.get('text', '') for i in response.get('output', [])
                       for c in i.get('content', []) if isinstance(c, dict))
        result['checks']['stream_text'] = 'LIVE_OK' in text
        compact_request = {
            'model': model,
            'input': [{'role': 'user', 'content': 'This is a bounded tool verification task. Remember checkpoint PCL_CHECKPOINT_927. Next, write that checkpoint using write_note, then acknowledge the verified tool result. No files changed yet.'}],
        }
        if compaction_version == 2:
            compact_request.update(stream=True, max_output_tokens=4096)
            compact_request['input'].append({'type': 'compaction_trigger'})
        compact = post(base, '/responses' if compaction_version == 2 else '/responses/compact', compact_request)
        # OpenCodex v1 returns retained messages plus the Codex checkpoint
        # summary; the encrypted compaction item belongs to the v2 endpoint.
        result['checks']['compaction'] = any(
            c.get('text', '').startswith('Another language model started to solve this problem')
            for i in compact.get('output', []) for c in i.get('content', [])
            if isinstance(c, dict)
        )
        if compaction_version == 2:
            items = compact.get('output', [])
            result['checks']['compaction'] = (
                len(items) == 1 and items[0].get('type') == 'compaction'
                and items[0].get('encrypted_content', '').startswith('ocx1:')
            )
        inputs = compact['output'] + [{
            'role': 'user',
            'content': 'Call write_note with the remembered checkpoint as text. After its result, return only TOOL_ROUNDTRIP_OK.',
        }]
        tool = {'type': 'function', 'name': 'write_note', 'description': 'Write a test note.',
                'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                               'required': ['text'], 'additionalProperties': False}}
        response = post(base, '/responses', {
            'model': model, 'stream': True, 'max_output_tokens': 8192,
            'reasoning': {'effort': 'high'}, 'input': inputs,
            'tools': [tool], 'tool_choice': 'required',
        })
        calls = [i for i in response.get('output', []) if i.get('type') == 'function_call']
        result['tool_response'] = {
            'status': response.get('status'), 'usage': response.get('usage'),
            'output_types': [i.get('type') for i in response.get('output', [])],
        }
        if len(calls) != 1 or calls[0]['name'] != 'write_note':
            raise RuntimeError('Expected exactly one write_note tool call')
        arguments = json.loads(calls[0]['arguments'])
        if not isinstance(arguments.get('text'), str):
            raise RuntimeError('Tool text argument is not a string')
        with tempfile.TemporaryDirectory(prefix='pcl-relay-acceptance-') as temp:
            note = Path(temp) / 'note.txt'
            note.write_text(arguments['text'], encoding='utf-8')
            result['checks']['checkpoint_and_file'] = 'PCL_CHECKPOINT_927' in note.read_text()
        inputs += response['output'] + [{
            'type': 'function_call_output', 'call_id': calls[0]['call_id'],
            'output': 'Note written and verified successfully.',
        }, {
            'role': 'user',
            'content': 'The verification task is complete: the tool has written and verified the checkpoint. No further task or clarification is needed. Acknowledge this successful tool result by replying only TOOL_ROUNDTRIP_OK.',
        }]
        final = post(base, '/responses', {
            'model': model, 'stream': True, 'max_output_tokens': 8192,
            'reasoning': {'effort': 'high'}, 'input': inputs,
            'tools': [tool], 'tool_choice': 'none',
        })
        text = ''.join(c.get('text', '') for i in final.get('output', [])
                       for c in i.get('content', []) if isinstance(c, dict))
        result['checks']['tool_roundtrip'] = 'TOOL_ROUNDTRIP_OK' in text
        result['final_response'] = {
            'status': final.get('status'), 'usage': final.get('usage'),
            'output_types': [i.get('type') for i in final.get('output', [])],
            'text': text[:300],
        }
        result['ok'] = all(result['checks'].values())
    except Exception as exc:
        result.update(ok=False, error=f'{type(exc).__name__}: {exc}')
    result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:15725/v1')
    parser.add_argument('--compaction-version', type=int, choices=(1, 2), default=1)
    parser.add_argument('--isolated-port', type=int)
    parser.add_argument('--gateway-url', default='http://100.113.234.58:15722/v1')
    parser.add_argument('--models', nargs='+', default=[
        'pcl/GLM-5.2', 'pcl/DeepSeek-V4-Pro', 'pcl/DeepSeek-V4-Flash-0731', 'pcl/Kimi-K3',
    ])
    args = parser.parse_args()
    if args.isolated_port:
        with isolated_server(args.isolated_port, args.gateway_url):
            command = [sys.executable, __file__, '--base-url', f'http://127.0.0.1:{args.isolated_port}/v1',
                       '--compaction-version', str(args.compaction_version), '--models', *args.models]
            return subprocess.call(command)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(verify, args.base_url, model, args.compaction_version) for model in args.models]
        results = []
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if all(r['ok'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
