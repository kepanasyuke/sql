"""CLI лаборатории: подготовить данные, прогнать сценарии, выгрузить отчёт."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bench import benchmark, default_scenarios, prepare_database, statistics_note, to_markdown


def cmd_bench(args: argparse.Namespace) -> int:
    dsn = args.dsn
    if args.rows:
        print(f"готовлю данные: {args.rows} строк в {dsn}")
        prepare_database(dsn, rows=args.rows, clients=args.clients or 120, days=args.days or 60)

    results = benchmark(dsn, repeats=args.repeats)
    markdown = to_markdown(results)
    print(markdown)

    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(markdown, encoding="utf-8")
        print(f"отчёт: {args.markdown}")

    mismatched = [
        f"{variant.scenario}/{variant.variant}"
        for result in results
        for variant in result.variants
        if not variant.matches_baseline
    ]
    if args.json:
        print(json.dumps(
            {
                "scenarios": [
                    {
                        "name": result.name,
                        "variants": [
                            {
                                "variant": variant.variant,
                                "p50_ms": variant.p50_ms,
                                "p95_ms": variant.p95_ms,
                                "speedup": variant.speedup,
                                "matches_baseline": variant.matches_baseline,
                                "rows": variant.rows,
                            }
                            for variant in result.variants
                        ],
                    }
                    for result in results
                ],
                "mismatched": mismatched,
            },
            ensure_ascii=False,
            indent=2,
        ))

    print(f"методика: {statistics_note()}")
    code = 0
    if mismatched:
        print(f"ошибка: варианты расходятся в результате: {mismatched}", file=sys.stderr)
        code = 1
    return code


def cmd_scenarios(args: argparse.Namespace) -> int:
    for scenario in default_scenarios():
        print(f"{scenario.name}: {scenario.description}")
        for variant in scenario.variants:
            print(f"  - {variant.name}: {variant.note}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="perf-lab", description="Бенчмарк SQL-вариантов и индексов")
    sub = parser.add_subparsers(dest="command", required=True)

    bench = sub.add_parser("bench", help="прогнать сценарии")
    bench.add_argument("--dsn", default="artifacts/perf.sqlite3")
    bench.add_argument("--rows", type=int, default=0, help="пересоздать данные (0 — использовать готовую БД)")
    bench.add_argument("--clients", type=int)
    bench.add_argument("--days", type=int)
    bench.add_argument("--repeats", type=int, default=5)
    bench.add_argument("--markdown")
    bench.add_argument("--json", action="store_true")
    bench.set_defaults(func=cmd_bench)

    sub.add_parser("scenarios", help="список сценариев и вариантов").set_defaults(func=cmd_scenarios)
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
