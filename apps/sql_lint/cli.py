"""CLI: линт файлов, выполнение запроса, запуск сервера, схема базы."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import db, query, rules, server


def cmd_lint(args: argparse.Namespace) -> int:
    report = rules.lint_files(args.paths, max_lines=args.max_lines, allow_implicit_exit=args.allow_implicit_exit)
    total = 0
    for path, violations in report.items():
        print(f"{path}: {len(violations)} нарушений")
        for item in violations:
            total += 1
            print(f"  {item.line}:{item.column}  {item.rule:<15} {item.message}")
    print(f"итого нарушений: {total}")
    return 1 if total else 0


def cmd_query(args: argparse.Namespace) -> int:
    sql = args.sql or (Path(args.file).read_text(encoding="utf-8") if args.file else "")
    if not sql:
        print("ошибка: нужен --sql или --file", file=sys.stderr)
        code = 2
    else:
        connection = query.open_readonly(args.db)
        try:
            result = query.run_query(connection, sql, limit=args.limit)
        finally:
            connection.close()
        if not result.ok:
            print(f"ошибка: {result.error}", file=sys.stderr)
            code = 1
        else:
            print(" | ".join(result.columns))
            for row in result.rows:
                print(" | ".join(str(row[column]) for column in result.columns))
            print(f"строк: {result.rowcount}, {result.duration_ms:.1f} мс")
            code = 0
    return code


def cmd_schema(args: argparse.Namespace) -> int:
    connection = db.connect(args.db)
    try:
        payload = {"tables": db.tables(connection), "counts": db.table_counts(connection)}
    finally:
        connection.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    adapter = server.make_server(args.db, port=args.port, seed_data=not args.no_seed)
    host, port = adapter.server_address
    print(f"sql-lint: http://{host}:{port}")
    print("маршруты: /  /api/health  /api/schema  /api/table/<name>  /api/lint  /api/query  /api/analyze")
    try:
        adapter.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлено")
    finally:
        adapter.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sql-lint", description="Правила оформления функций и песочница запросов")
    sub = parser.add_subparsers(dest="command", required=True)

    lint = sub.add_parser("lint", help="проверить файлы правилами линтера")
    lint.add_argument("paths", nargs="+")
    lint.add_argument("--max-lines", type=int, default=rules.DEFAULT_MAX_LINES)
    lint.add_argument("--allow-implicit-exit", action="store_true", help="неявный return считать выходом")
    lint.set_defaults(func=cmd_lint)

    run = sub.add_parser("query", help="выполнить запрос и показать строки")
    run.add_argument("--db", default="artifacts/sql_lint.sqlite3")
    run.add_argument("--sql")
    run.add_argument("--file")
    run.add_argument("--limit", type=int, default=query.DEFAULT_LIMIT)
    run.set_defaults(func=cmd_query)

    schema = sub.add_parser("schema", help="таблицы и количество строк")
    schema.add_argument("--db", default="artifacts/sql_lint.sqlite3")
    schema.set_defaults(func=cmd_schema)

    serve = sub.add_parser("serve", help="запустить веб-приложение")
    serve.add_argument("--db", default="artifacts/sql_lint.sqlite3")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--no-seed", action="store_true")
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code = int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        code = 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
