"""Тесты CLI: линт файлов, запросы, схема, коды выхода."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.sql_lint import cli


@pytest.fixture()
def clean_file(tmp_path: Path) -> Path:
    path = tmp_path / "clean.py"
    path.write_text("def f():\n    return 1\n", encoding="utf-8")
    return path


@pytest.fixture()
def bad_file(tmp_path: Path) -> Path:
    path = tmp_path / "bad.py"
    path.write_text("def f(x):\n    if x:\n        return 1\n    return 2\n", encoding="utf-8")
    return path


def test_lint_clean_file_exit_zero(clean_file: Path, capsys) -> None:
    assert cli.main(["lint", str(clean_file)]) == 0
    assert "0 нарушений" in capsys.readouterr().out


def test_lint_bad_file_exit_one(bad_file: Path, capsys) -> None:
    assert cli.main(["lint", str(bad_file)]) == 1
    assert "single-exit" in capsys.readouterr().out


def test_lint_reports_line_and_column(bad_file: Path, capsys) -> None:
    cli.main(["lint", str(bad_file)])
    assert "1:1" in capsys.readouterr().out


def test_lint_max_lines_flag(clean_file: Path, capsys) -> None:
    assert cli.main(["lint", str(clean_file), "--max-lines", "1"]) == 1
    assert "small-function" in capsys.readouterr().out


def test_lint_several_files(clean_file: Path, bad_file: Path, capsys) -> None:
    cli.main(["lint", str(clean_file), str(bad_file)])
    output = capsys.readouterr().out
    assert output.count("нарушений") >= 2


def test_query_ok(sql_lint_db: str, capsys) -> None:
    code = cli.main(["query", "--db", sql_lint_db, "--sql", "SELECT 1 AS one"])
    assert code == 0
    assert "one" in capsys.readouterr().out


def test_query_shows_rows(sql_lint_db: str, capsys) -> None:
    cli.main(["query", "--db", sql_lint_db, "--sql", "SELECT client_id FROM client ORDER BY client_id LIMIT 1"])
    assert "CL0000" in capsys.readouterr().out


def test_query_invalid_sql(sql_lint_db: str, capsys) -> None:
    assert cli.main(["query", "--db", sql_lint_db, "--sql", "DELETE FROM client"]) == 1
    assert "ошибка" in capsys.readouterr().err


def test_query_without_sql(sql_lint_db: str, capsys) -> None:
    assert cli.main(["query", "--db", sql_lint_db]) == 2
    assert "нужен --sql" in capsys.readouterr().err


def test_query_from_file(sql_lint_db: str, tmp_path: Path, capsys) -> None:
    path = tmp_path / "q.sql"
    path.write_text("SELECT 3 AS three", encoding="utf-8")
    assert cli.main(["query", "--db", sql_lint_db, "--file", str(path)]) == 0
    assert "3" in capsys.readouterr().out


def test_schema_command(sql_lint_db: str, capsys) -> None:
    assert cli.main(["schema", "--db", sql_lint_db]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["client"] == 120


def test_schema_lists_tables(sql_lint_db: str, capsys) -> None:
    cli.main(["schema", "--db", sql_lint_db])
    payload = json.loads(capsys.readouterr().out)
    assert [item["table"] for item in payload["tables"]] == ["account", "client", "dq_run", "loan", "turnover"]


def test_parser_has_all_commands() -> None:
    parser = cli.build_parser()
    assert {"lint", "query", "schema", "serve"} <= parser._subparsers._group_actions[0].choices.keys()


def test_unknown_command_exits(capsys) -> None:
    with pytest.raises(SystemExit):
        cli.main(["unknown"])


def test_missing_file_error(tmp_path: Path, capsys) -> None:
    assert cli.main(["lint", str(tmp_path / "nope.py")]) == 2
    assert "ошибка" in capsys.readouterr().err


def test_allow_implicit_exit_flag(tmp_path: Path, capsys) -> None:
    path = tmp_path / "implicit.py"
    path.write_text("def f():\n    pass\n", encoding="utf-8")
    assert cli.main(["lint", str(path), "--allow-implicit-exit"]) == 0
    assert "0 нарушений" in capsys.readouterr().out
