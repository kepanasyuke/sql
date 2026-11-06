"""Тесты HTTP API: коды ответов, пагинация, ETag, агрегаты."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from projects.bank_metrics_api.api import make_server


@pytest.fixture()
def base_url(tmp_path: Path):
    server = make_server(str(tmp_path / "api.sqlite3"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def call(url: str, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
            return response.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as error:
        body = error.read()
        return error.code, (json.loads(body) if body else {})


def test_health(base_url: str) -> None:
    status, payload = call(f"{base_url}/health")
    assert status == 200
    assert payload == {"status": "ok", "database": "ok"}


def test_clients_pagination_and_filter(base_url: str) -> None:
    status, payload = call(f"{base_url}/clients?limit=5")
    assert status == 200
    assert payload["total"] == 25
    assert len(payload["items"]) == 5
    assert [item["client_id"] for item in payload["items"]] == ["CL000", "CL001", "CL002", "CL003", "CL004"]

    segment = urllib.parse.quote("МСБ")
    status, filtered = call(f"{base_url}/clients?segment={segment}&limit=200")
    assert status == 200
    assert all(item["segment"] == "МСБ" for item in filtered["items"])


def test_turnover_aggregates(base_url: str) -> None:
    status, payload = call(f"{base_url}/clients/CL001/turnover?from=2026-10-20&to=2026-10-25")
    assert status == 200
    assert payload["summary"]["days"] == 6
    assert len(payload["items"]) == 6
    assert payload["summary"]["debit"] == pytest.approx(
        sum(item["debit"] for item in payload["items"]), rel=1e-6
    )


def test_unknown_client_returns_404(base_url: str) -> None:
    status, payload = call(f"{base_url}/clients/CL999/turnover")
    assert status == 404
    assert "не найден" in payload["error"]


def test_bad_parameters_return_400(base_url: str) -> None:
    assert call(f"{base_url}/clients?limit=abc")[0] == 400
    assert call(f"{base_url}/clients?limit=999")[0] == 400
    assert call(f"{base_url}/clients/CL001/turnover?from=31-12-2026")[0] == 400
    assert call(f"{base_url}/clients/CL001/turnover?from=2026-11-05&to=2026-10-01")[0] == 400


def test_etag_gives_304(base_url: str) -> None:
    request = urllib.request.Request(f"{base_url}/portfolio")
    with urllib.request.urlopen(request, timeout=5) as response:
        etag = response.headers["ETag"]
    status, _ = call(f"{base_url}/portfolio", headers={"If-None-Match": etag})
    assert status == 304


def test_portfolio_filter(base_url: str) -> None:
    status, payload = call(f"{base_url}/portfolio?min_debt=10000000")
    assert status == 200
    assert all(item["debt"] >= 10_000_000 for item in payload["items"])
    assert {"product", "clients", "debt", "avg_rate", "utilisation_pct"} <= set(payload["items"][0])


def test_dq_status_worst_wins(base_url: str) -> None:
    status, payload = call(f"{base_url}/dq")
    assert status == 200
    assert payload["overall"] == "FAIL"


def test_unknown_route_and_method(base_url: str) -> None:
    assert call(f"{base_url}/nope")[0] == 404
    request = urllib.request.Request(f"{base_url}/clients", method="POST", data=b"{}")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 405
