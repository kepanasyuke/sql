"""Тесты сборщика покрытия: разбор строк, проценты, HTML-страницы."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.sql_lint.tools import coverage


@pytest.mark.parametrize(
    "source,expected",
    [
        ("", set()),
        ("\n\n", set()),
        ("# комментарий\n", set()),
        ("x = 1\n", {1}),
        ("x = 1\ny = 2\n", {1, 2}),
        ("def f():\n    return 1\n", {1, 2}),
        ("class A:\n    x = 1\n", {1, 2}),
        ("if True:\n    x = 1\n", {1, 2}),
        ('"""Док."""\nx = 1\n', {1, 2}),
        ("def f(:\n    pass\n", set()),
    ],
)
def test_executable_lines(source: str, expected: set[int]) -> None:
    assert coverage.executable_lines(source) == expected


@pytest.mark.parametrize(
    "covered,total,expected",
    [(0, 0, 100.0), (0, 10, 0.0), (5, 10, 50.0), (10, 10, 100.0), (1, 3, 33.3), (240, 300, 80.0)],
)
def test_percent(covered: int, total: int, expected: float) -> None:
    assert coverage.percent(covered, total) == expected


@pytest.mark.parametrize(
    "path,expected",
    [("apps/sql_lint/rules.py", "rules.html"), ("/tmp/x/query.py", "query.html"), ("server.py", "server.html")],
)
def test_page_name(path: str, expected: str) -> None:
    assert coverage.page_name(path) == expected


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.py"
    path.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    return path


def test_file_report_hits(sample_file: Path) -> None:
    report = coverage.file_report(sample_file, {1, 2})
    assert report["hit"] == 2
    assert report["total"] == 3
    assert report["missing"] == [3]


def test_file_report_percent(sample_file: Path) -> None:
    assert coverage.file_report(sample_file, {1})["percent"] == 33.3


def test_file_report_full(sample_file: Path) -> None:
    report = coverage.file_report(sample_file, {1, 2, 3})
    assert report["percent"] == 100.0
    assert report["missing"] == []


def test_render_index_lists_files(sample_file: Path) -> None:
    report = coverage.file_report(sample_file, {1})
    html = coverage.render_index([report])
    assert "sample.html" in html
    assert "Покрытие тестами" in html


def test_render_index_shows_totals(sample_file: Path) -> None:
    html = coverage.render_index([coverage.file_report(sample_file, {1, 2, 3})])
    assert "операторов: <b>3</b>" in html


def test_render_file_page_marks_covered(sample_file: Path) -> None:
    html = coverage.render_file_page(coverage.file_report(sample_file, {1}))
    assert 'class="ln hit"' in html


def test_render_file_page_marks_missing(sample_file: Path) -> None:
    html = coverage.render_file_page(coverage.file_report(sample_file, {1}))
    assert 'class="ln miss"' in html


def test_render_file_page_escapes_code(tmp_path: Path) -> None:
    path = tmp_path / "x.py"
    path.write_text("x = 1 < 2\n", encoding="utf-8")
    html = coverage.render_file_page(coverage.file_report(path, {1}))
    assert "&lt;" in html


def test_build_site_creates_files(sample_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "site"
    coverage.build_site([coverage.file_report(sample_file, {1})], target)
    assert (target / "index.html").exists()
    assert (target / "sample.html").exists()
    assert (target / "coverage.json").exists()


def test_build_site_json_contents(sample_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "site2"
    coverage.build_site([coverage.file_report(sample_file, {1, 2})], target)
    payload = json.loads((target / "coverage.json").read_text(encoding="utf-8"))
    assert payload[0]["hit"] == 2
    assert "source" not in payload[0]


def test_collector_ignores_foreign_files(sample_file: Path) -> None:
    collector = coverage.Collector(root=sample_file.parent)
    code = compile("x = 1", "<not-a-file>", "exec")
    collector._callback(code, 1)
    assert collector.hits == {}


def test_collector_keeps_root_files(sample_file: Path) -> None:
    collector = coverage.Collector(root=sample_file.parent)
    code = compile("x = 1", str(sample_file), "exec")
    collector._callback(code, 1)
    assert collector.hits[str(sample_file)] == {1}


def test_callback_disables_foreign_lines(sample_file: Path) -> None:
    import sys

    collector = coverage.Collector(root=sample_file.parent)
    code = compile("x = 1", "<foreign>", "exec")
    assert collector._callback(code, 1) == sys.monitoring.DISABLE


def test_callback_marks_own_line_once(sample_file: Path) -> None:
    code = compile("x = 1\ny = 2", str(sample_file), "exec")
    collector = coverage.Collector(root=sample_file.parent)
    collector._callback(code, 1)
    collector._callback(code, 1)
    assert collector.hits[str(sample_file)] == {1}
