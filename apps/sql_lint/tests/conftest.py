"""Общие фикстуры: временная база, живой сервер и HTTP-клиент."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from apps.sql_lint import db, server
from apps.sql_lint.tests.helpers import request  # noqa: F401  (переиспользуется тестами)


@pytest.fixture(scope="session")
def sql_lint_db(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = tmp_path_factory.mktemp("sqllint") / "demo.sqlite3"
    connection = db.connect(path)
    db.seed(connection)
    connection.close()
    return str(path)


@pytest.fixture(scope="session")
def api_url(sql_lint_db: str):
    adapter = server.make_server(sql_lint_db, port=0, seed_data=False)
    thread = threading.Thread(target=adapter.serve_forever, daemon=True)
    thread.start()
    host, port = adapter.server_address
    yield f"http://{host}:{port}"
    adapter.shutdown()
    adapter.server_close()
