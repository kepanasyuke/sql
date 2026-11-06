"""CLI сверки: `python -m projects.recon_kit.cli run --source a.csv --target b.csv --key id`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .recon import compare, compare_by_buckets, read_csv


def cmd_run(args: argparse.Namespace) -> int:
    source = read_csv(args.source)
    target = read_csv(args.target)

    identical, left, right = compare_by_buckets(source, target, args.key, buckets=args.buckets)
    if identical and not args.force:
        print(f"checksum-бакеты совпали ({len(left)} шт.): данные идентичны, детальная сверка не требуется")
        print(f"строк: источник {len(source)}, приёмник {len(target)}")
        return 0

    columns = [column.strip() for column in args.columns.split(",")] if args.columns else None
    report = compare(source, target, key=args.key, columns=columns, sample_limit=args.samples)
    report.buckets_compared = len(left)
    report.buckets_matched = sum(1 for bucket, value in left.items() if right.get(bucket) == value)

    if args.report:
        Path(args.report).write_text(report.to_markdown(), encoding="utf-8")
        print(f"отчёт: {args.report}")
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.to_markdown())
    return report.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recon-kit", description="Сверка источника и приёмника")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="сверить два CSV")
    run.add_argument("--source", required=True)
    run.add_argument("--target", required=True)
    run.add_argument("--key", required=True)
    run.add_argument("--columns", help="список колонок через запятую (по умолчанию все)")
    run.add_argument("--buckets", type=int, default=8)
    run.add_argument("--samples", type=int, default=5)
    run.add_argument("--report", help="куда сохранить markdown-отчёт")
    run.add_argument("--json", action="store_true")
    run.add_argument("--force", action="store_true", help="сверять даже при совпавших бакетах")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, KeyError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
