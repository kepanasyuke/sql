"""CLI профилировщика: markdown-профиль и заготовки DQ-проверок."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

from .profiler import Profiler, profile_file


def demo_rows(rows: int = 60, seed: int = 5) -> list[dict[str, str]]:
    rng = random.Random(seed)
    segments = ["МСБ", "Крупный бизнес", "Средний бизнес", ""]
    result = []
    for index in range(rows):
        result.append(
            {
                "client_id": f"CL{index:03d}",
                "inn": "7707083893" if index % 7 else "7707083894",
                "amount": str(round(rng.uniform(1000, 900000), 2)),
                "segment": rng.choice(segments),
                "opened": f"2026-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}",
                "email": f"user{index}@example.ru",
            }
        )
    return result


def cmd_profile(args: argparse.Namespace) -> int:
    if args.demo:
        profiler = Profiler(max_distinct=args.max_distinct)
        profiler.feed_rows(demo_rows())
    else:
        profiler = profile_file(args.file, max_distinct=args.max_distinct)

    markdown = profiler.to_markdown(title=f"Профиль: {args.entity}")
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(markdown, encoding="utf-8")
        print(f"профиль: {args.markdown}")
    else:
        print(markdown)

    checks = profiler.suggested_checks(args.entity)
    if args.checks:
        Path(args.checks).parent.mkdir(parents=True, exist_ok=True)
        Path(args.checks).write_text(
            json.dumps({"version": 1, "checks": checks}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"заготовки проверок: {args.checks} ({len(checks)} шт.)")

    pii = [column.name for column in profiler.columns.values() if column.pii]
    print(f"строк: {profiler.rows}, колонок: {len(profiler.columns)}, PII: {', '.join(pii) or 'нет'}")
    return 0


def cmd_csv(args: argparse.Namespace) -> int:
    """Сгенерировать демо-CSV, чтобы профилировщик можно было показать без данных."""
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = demo_rows(rows=args.rows)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"демо-CSV: {target} ({len(rows)} строк)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="csv-profiler", description="Профилирование CSV и PII")
    parser.add_argument("--max-distinct", type=int, default=200)
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile", help="построить профиль")
    profile.add_argument("--file")
    profile.add_argument("--demo", action="store_true")
    profile.add_argument("--entity", default="mart.dataset")
    profile.add_argument("--markdown")
    profile.add_argument("--checks")
    profile.set_defaults(func=cmd_profile)

    generate = sub.add_parser("demo-csv", help="сгенерировать демо-CSV")
    generate.add_argument("--output", default="artifacts/demo.csv")
    generate.add_argument("--rows", type=int, default=60)
    generate.set_defaults(func=cmd_csv)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code = 2
    if args.command == "profile" and not args.demo and not args.file:
        print("ошибка: нужен --file или --demo", file=sys.stderr)
    else:
        try:
            code = int(args.func(args))
        except (OSError, ValueError) as exc:
            print(f"ошибка: {exc}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
