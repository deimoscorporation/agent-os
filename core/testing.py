"""Simulação da API da OpenAI para os testes: nenhuma chamada sai para a rede."""

import io
import json
from contextlib import contextmanager
from unittest import mock
from urllib.error import HTTPError


class FakeResponse:
    def __init__(self, body: bytes):
        self._stream = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._stream.readlines())

    def read(self, *args):
        return self._stream.read(*args)


def sse(*chunks) -> bytes:
    """Corpo de uma resposta em streaming com os pedaços de texto dados."""
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": chunk}}]}, ensure_ascii=False)
        for chunk in chunks
    ]
    return ("\n\n".join([*lines, "data: [DONE]"]) + "\n\n").encode()


def json_reply(data: dict) -> bytes:
    """Corpo de uma resposta única cujo conteúdo é o JSON dado (como em complete_json)."""
    return json.dumps({"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}]}).encode()


def models_reply(*ids) -> bytes:
    return json.dumps({"data": [{"id": i} for i in ids]}).encode()


def http_error(status, message="erro"):
    body = json.dumps({"error": {"message": message}}).encode()
    return HTTPError("https://api.openai.test/v1", status, "erro", {}, io.BytesIO(body))


@contextmanager
def fake_openai(*responses):
    """Substitui a rede. Cada item é o corpo (bytes) de uma resposta ou uma exceção a levantar.

    Devolve a lista de requisições feitas: [{"url", "headers", "payload"}].
    """
    calls = []
    queue = list(responses)

    def urlopen(request, timeout=None):
        calls.append({
            "url": request.full_url,
            "headers": dict(request.header_items()),
            "payload": json.loads(request.data) if request.data else None,
        })
        if not queue:
            raise AssertionError("Chamada inesperada à OpenAI")
        response = queue.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)

    with mock.patch("core.llm.urlopen", urlopen):
        yield calls
