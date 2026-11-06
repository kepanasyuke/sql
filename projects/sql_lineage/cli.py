"""CLI: разбор SQL, анализ влияния, экспорт графа."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph import build_model
from .parser import parse_script


def _load(paths: list[str]):
    statements = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        statements.extend(parse_script(text))
    return build_model(statements)


def cmd_parse(args: argparse.Namespace) -> int:
    model = _load(args.files)
    payload = model.to_dict()
    if args.output:
        Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"lineage сохранён: {args.output}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if model.warnings:
        print(f"предупреждений: {len(model.warnings)}", file=sys.stderr)
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    model = _load(args.files)
    result = model.impact(args.table)
    print(f"таблица: {result['table']}")
    print(f"  прямые потребители: {', '.join(result['direct_downstream']) or '—'}")
    print(f"  все потребители:    {', '.join(result['all_downstream']) or '—'}")
    print(f"  витрины на выходе:  {', '.join(result['leaf_marts']) or '—'}")
    print(f"  затронуто колонок:  {len(result['affected_columns'])}")
    for column in result["affected_columns"][: args.limit]:
        print(f"    - {column}")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_downstream"] else 1


def cmd_dot(args: argparse.Namespace) -> int:
    model = _load(args.files)
    print(model.to_dot())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sql-lineage", description="Lineage и анализ влияния по SQL-скриптам")
    sub = parser.add_subparsers(dest="command", required=True)

    parse = sub.add_parser("parse", help="разобрать скрипты и вывести граф в JSON")
    parse.add_argument("files", nargs="+")
    parse.add_argument("--output", "-o")
    parse.set_defaults(func=cmd_parse)

    impact = sub.add_parser("impact", help="что сломается, если таблица не пришла")
    impact.add_argument("files", nargs="+")
    impact.add_argument("--table", "-t", required=True)
    impact.add_argument("--limit", type=int, default=10)
    impact.add_argument("--json", action="store_true")
    impact.set_defaults(func=cmd_impact)

    dot = sub.add_parser("dot", help="экспорт графа в Graphviz DOT")
    dot.add_argument("files", nargs="+")
    dot.set_defaults(func=cmd_dot)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, OSError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
