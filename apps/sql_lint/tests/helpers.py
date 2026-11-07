"""HTTP-клиент для тестов: единый разбор ответов и ошибок."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def request(url: str, method: str = "GET", payload=None, headers: dict | None = None, raw: bytes | None = None) -> tuple[int, dict, dict]:
    data = raw
    options = {"method": method, "headers": dict(headers or {})}
    if data is None and payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if data is not None:
        options["data"] = data
        options["headers"].setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, **options)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read()
            parsed = json.loads(body) if body.startswith(b"{") or body.startswith(b"[") else {"raw": body.decode("utf-8", "replace")}
            return response.status, parsed, dict(response.headers)
    except urllib.error.HTTPError as error:
        body = error.read()
        parsed = json.loads(body) if body.startswith(b"{") else {"raw": body.decode("utf-8", "replace")}
        return error.code, parsed, dict(error.headers)
