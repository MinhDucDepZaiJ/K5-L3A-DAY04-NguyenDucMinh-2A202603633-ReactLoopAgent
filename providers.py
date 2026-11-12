"""Các provider cùng một interface. Không chứa agent loop."""
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
import uuid

CONFIG = {
    'openai': ('OPENAI_API_KEY', 'https://api.openai.com/v1/chat/completions'),
    'gemini': ('GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
    'openrouter': ('OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1/chat/completions'),
    'opencode-go': ('OPENCODE_GO_API_KEY', 'https://opencode.ai/inference/go/openai/v1/chat/completions'),
    'anthropic': ('ANTHROPIC_API_KEY', 'https://api.anthropic.com/v1/messages'),
}


# Một bộ giới hạn chung cho Agent và Judge trong cùng tiến trình.
_RATE_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


def _wait_for_slot():
    global _LAST_REQUEST
    interval = float(os.getenv('API_REQUEST_INTERVAL', '10'))
    with _RATE_LOCK:
        remaining = max(0.0, _LAST_REQUEST + interval - time.monotonic())
        if remaining:
            print(f'Rate limiter: đợi {remaining:.1f}s...', flush=True)
            time.sleep(remaining)
        _LAST_REQUEST = time.monotonic()


def post_json(url, headers, payload):
    max_attempts = 3
    for attempt in range(max_attempts):
        _wait_for_slot()
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', **headers}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            code = 'unknown'
            try:
                body = json.loads(exc.read().decode('utf-8'))
                error = body.get('error', {})
                if isinstance(error, dict):
                    code = error.get('code') or error.get('type') or 'unknown'
            except (ValueError, UnicodeError, TypeError, AttributeError):
                pass
            if exc.code == 429 and code == 'insufficient_quota':
                raise RuntimeError('HTTP 429 insufficient_quota: hết quota/credit; dừng chạy.') from exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f'Provider HTTP {exc.code} ({code})') from exc
            if attempt == max_attempts - 1:
                raise RuntimeError(f'Provider HTTP {exc.code} ({code}) sau {max_attempts} lần thử') from exc
            delay = min(60.0, 5.0 * (2 ** attempt) + random.uniform(0, 1))
            retry_after = exc.headers.get('Retry-After')
            if retry_after:
                try:
                    delay = max(delay, min(120.0, float(retry_after)))
                except ValueError:
                    pass
            print(f'HTTP {exc.code} ({code}); chờ {delay:.1f}s rồi thử lại...', flush=True)
            time.sleep(delay)
        except urllib.error.URLError as exc:
            raise RuntimeError(f'Không kết nối được provider: {exc.reason}') from exc


class Provider:
    """complete(messages, schemas) -> {message, calls, finish, usage}.

    messages: system/user/assistant/tool theo format Chat Completions. message trả về
    cần được append nguyên vẹn; tool result có role, tool_call_id, content (JSON string).
    """
    def __init__(self, name, model):
        if name not in CONFIG:
            raise ValueError(f'Provider không hợp lệ: {name}')
        if not model:
            raise ValueError('Cần đặt MODEL là model có hỗ trợ tool calling.')
        self.name, self.model = name, model
        # One Provider instance represents one conversation/run.
        self.session_id = str(uuid.uuid4()) if name == 'opencode-go' else None
        key_name, self.url = CONFIG[name]
        self.key = os.getenv(key_name)
        if not self.key:
            raise ValueError(f'Thiếu {key_name}; xem README.md.')

    def complete(self, messages, schemas):
        if self.name == 'anthropic':
            return self._anthropic(messages, schemas)
        payload = {'model': self.model, 'messages': messages}
        if schemas:
            payload['tools'] = schemas
        headers = {'Authorization': f'Bearer {self.key}'}
        if self.name == 'opencode-go':
            headers.update({'User-Agent': 'StatusAgentLab/1.0', 'x-opencode-session': self.session_id})
        data = post_json(self.url, headers, payload)
        choice = data['choices'][0]
        message = choice['message']
        calls = [{'id': c['id'], 'name': c['function']['name'], 'arguments': c['function']['arguments']} for c in message.get('tool_calls', [])]
        return {'message': message, 'calls': calls, 'finish': choice.get('finish_reason'), 'usage': data.get('usage', {})}

    def _anthropic(self, messages, schemas):
        system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        native = []
        for m in messages:
            if m['role'] == 'system':
                continue
            if m['role'] == 'assistant':
                blocks = []
                if m.get('content'):
                    blocks.append({'type': 'text', 'text': m['content']})
                for call in m.get('tool_calls', []):
                    blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'], 'input': json.loads(call['function']['arguments'])})
                native.append({'role': 'assistant', 'content': blocks})
            elif m['role'] == 'tool':
                block = {'type': 'tool_result', 'tool_use_id': m['tool_call_id'], 'content': m['content']}
                if native and native[-1]['role'] == 'user' and isinstance(native[-1]['content'], list):
                    native[-1]['content'].append(block)
                else:
                    native.append({'role': 'user', 'content': [block]})
            else:
                native.append({'role': 'user', 'content': m['content']})
        tools = [{'name': x['function']['name'], 'description': x['function']['description'], 'input_schema': x['function']['parameters']} for x in schemas]
        payload = {'model': self.model, 'max_tokens': 1024, 'system': system, 'messages': native}
        if tools:
            payload['tools'] = tools
        data = post_json(self.url, {'x-api-key': self.key, 'anthropic-version': '2023-06-01'}, payload)
        blocks = data['content']
        calls = [{'id': b['id'], 'name': b['name'], 'arguments': json.dumps(b['input'], ensure_ascii=False)} for b in blocks if b['type'] == 'tool_use']
        text = '\n'.join(b['text'] for b in blocks if b['type'] == 'text')
        message = {'role': 'assistant', 'content': text or None}
        if calls:
            message['tool_calls'] = [{'id': c['id'], 'type': 'function', 'function': {'name': c['name'], 'arguments': c['arguments']}} for c in calls]
        reason = {'end_turn': 'stop', 'tool_use': 'tool_calls', 'max_tokens': 'length'}.get(data.get('stop_reason'), data.get('stop_reason'))
        return {'message': message, 'calls': calls, 'finish': reason, 'usage': data.get('usage', {})}
