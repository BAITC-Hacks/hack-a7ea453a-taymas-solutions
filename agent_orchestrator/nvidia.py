"""Optional NVIDIA HTTPS adapter. Standard library only; no retries or disk I/O."""

import http.client
import json
import os
import socket
import time
from dataclasses import dataclass, field

from .contracts import MAX_CONTEXT_BYTES, json_text

HOST = "integrate.api.nvidia.com"
ENDPOINT = "/v1/chat/completions"
MAX_TOKENS = 768
MAX_RESPONSE_BYTES = 32_000
TIMEOUT_SECONDS = 8.0


class ProviderError(RuntimeError):
    """Public error codes only; never propagate HTTP bodies, prompts or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _post(payload: bytes, key: str, timeout: float) -> bytes:
    # Fixed HTTPS destination. http.client does not follow redirects or use
    # environment proxy URLs; Authorization cannot be redirected to another host.
    deadline = time.monotonic() + timeout
    connection = http.client.HTTPSConnection(HOST, timeout=timeout)
    try:
        connection.request("POST", ENDPOINT, body=payload,
                           headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            code = "quota" if response.status == 429 else "auth" if response.status in (401, 403) else "api_error"
            raise ProviderError(code)
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("timeout")
            # read1 makes one socket read; a drip-fed response cannot reset the
            # full deadline for every byte. The total response is bounded too.
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(4096, MAX_RESPONSE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ProviderError("response_limit")
        return bytes(body)
    except (TimeoutError, socket.timeout):
        raise ProviderError("timeout") from None
    except (OSError, http.client.HTTPException):
        raise ProviderError("api_error") from None
    finally:
        connection.close()


@dataclass(frozen=True)
class NvidiaClient:
    api_key: str = field(repr=False)
    model: str
    timeout: float = TIMEOUT_SECONDS
    max_tokens: int = MAX_TOKENS

    def __post_init__(self):
        if not self.api_key or not self.model or not self.model.strip():
            raise ProviderError("configuration")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= MAX_TOKENS:
            raise ProviderError("configuration")
        if type(self.timeout) not in (int, float) or not 0 < self.timeout <= TIMEOUT_SECONDS:
            raise ProviderError("configuration")

    @classmethod
    def from_env(cls):
        key = os.environ.get("NVIDIA_API_KEY", "").strip()
        model = os.environ.get("NVIDIA_MODEL", "").strip()
        if not key:
            raise ProviderError("no_api_key")
        if not model:
            raise ProviderError("no_model")
        return cls(key, model)

    def complete(self, messages: list[dict], tools: list[dict], *, timeout: float | None = None) -> dict:
        payload = json_text({"model": self.model, "messages": messages, "tools": tools,
                             "tool_choice": "required", "temperature": 0, "stream": False,
                             "max_tokens": self.max_tokens}).encode("utf-8")
        # This caps the entire request including tool schemas, not just the data.
        if len(payload) > MAX_CONTEXT_BYTES:
            raise ProviderError("context_limit")
        try:
            raw = _post(payload, self.api_key, min(self.timeout, timeout or self.timeout))
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ProviderError("response_limit")
            data = json.loads(raw)
            choice = data["choices"][0]
            if choice.get("finish_reason") not in ("tool_calls", "stop"):
                raise ProviderError("incomplete_response")
            message = choice["message"]
            if not isinstance(message, dict) or not isinstance(message.get("tool_calls"), list):
                raise ProviderError("invalid_response")
            return message
        except (ValueError, KeyError, IndexError, TypeError, RecursionError):
            raise ProviderError("invalid_response") from None
