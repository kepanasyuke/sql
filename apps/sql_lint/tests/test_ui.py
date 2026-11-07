"""Контракт интерфейса: страница содержит кнопки, маршруты и режим тишины."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "static" / "app.html"


@pytest.fixture(scope="module")
def html() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_page_exists() -> None:
    assert PAGE.exists()


def test_page_has_doctype(html: str) -> None:
    assert html.lstrip().startswith("<!DOCTYPE html>")


def test_page_has_title(html: str) -> None:
    assert "<title>" in html and "sql-lint" in html


@pytest.mark.parametrize("element", ["btn-analyze", "btn-lint", "btn-run", "btn-db", "btn-reset", "drawer", "code", "out"])
def test_required_elements(html: str, element: str) -> None:
    assert element in html


@pytest.mark.parametrize("endpoint", ["/api/lint", "/api/query", "/api/analyze", "/api/schema", "/api/table/"])
def test_page_calls_api(html: str, endpoint: str) -> None:
    assert endpoint in html


def test_no_external_resources(html: str) -> None:
    assert not re.search(r'src="https?://|href="https?://', html)


def test_reduced_motion_support(html: str) -> None:
    assert "prefers-reduced-motion" in html


def test_keyboard_shortcut(html: str) -> None:
    assert "ctrlKey" in html and "Enter" in html


def test_sample_code_contains_sql(html: str) -> None:
    assert "SELECT dt, debit, credit, balance" in html


def test_drawer_has_animation(html: str) -> None:
    assert "transform: translateY" in html


def test_escape_helper_present(html: str) -> None:
    assert "function esc(" in html
