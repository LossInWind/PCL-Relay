#!/usr/bin/env python3
"""Opt-in live Responses acceptance; only changes a temporary test directory."""
import argparse
import concurrent.futures
import json
import tempfile
import time
import urllib.request
from pathlib import Path


def post(base, path, body):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        base.rstrip('/') + path,
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with opener.open(request, timeout=180) as response:
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


def verify(base, model):
    started = time.monotonic()
    result = {'model': model, 'checks': {}}
    try:
        response = post(base, '/responses', {
            'model': model, 'stream': True, 'max_output_tokens': 4096,
            'reasoning': {'effort': 'low'},
            'input': [{'role': 'user', 'content': 'Reply only LIVE_OK.'}],
        })
        text = ''.join(c.get('text', '') for i in response.get('output', [])
                       for c in i.get('content', []) if isinstance(c, dict))
        result['checks']['stream_text'] = 'LIVE_OK' in text
        compact = post(base, '/responses/compact', {
            'model': model,
            'input': [{'role': 'user', 'content': 'Remember checkpoint PCL_CHECKPOINT_927. No files changed.'}],
        })
        result['checks']['compaction'] = any(
            i.get('type') == 'compaction' for i in compact.get('output', [])
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
        }]
        final = post(base, '/responses', {
            'model': model, 'stream': True, 'max_output_tokens': 8192,
            'reasoning': {'effort': 'high'}, 'input': inputs,
            'tools': [tool], 'tool_choice': 'none',
        })
        text = ''.join(c.get('text', '') for i in final.get('output', [])
                       for c in i.get('content', []) if isinstance(c, dict))
        result['checks']['tool_roundtrip'] = 'TOOL_ROUNDTRIP_OK' in text
        result['ok'] = all(result['checks'].values())
    except Exception as exc:
        result.update(ok=False, error=f'{type(exc).__name__}: {exc}')
    result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:15725/v1')
    parser.add_argument('--models', nargs='+', default=[
        'pcl/GLM-5.2', 'pcl/DeepSeek-V4-Pro', 'pcl/DeepSeek-V4-Flash-0731', 'pcl/Kimi-K3',
    ])
    args = parser.parse_args()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(verify, args.base_url, model) for model in args.models]
        results = []
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if all(r['ok'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
