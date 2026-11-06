"""CLI: демонстрация идемпотентности и состояния загрузки."""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta

from .loader import Loader

DAYS = ["2026-11-01", "2026-11-02", "2026-11-03"]


def make_rows(day: str, seed: int = 1, clients: int = 40) -> list[dict]:
    rng = random.Random(f"{day}-{seed}")
    return [
        {
            "client_id": f"CL{index:03d}",
            "dt": day,
            "amount": round(rng.uniform(1000, 90000), 2),
            "segment": rng.choice(["МСБ", "Крупный бизнес", "Средний бизнес"]),
        }
        for index in range(clients)
    ]


def cmd_demo(args: argparse.Namespace) -> int:
    with Loader(dsn=args.dsn, table="client_daily_amount", key="client_id", watermark="dt") as loader:
        print("1) первая загрузка за день")
        first = loader.load(lambda: make_rows(DAYS[0]))
        print(f"   {first.as_dict()}")

        print("2) повторная загрузка того же дня (идемпотентность)")
        second = loader.load(lambda: make_rows(DAYS[0]))
        print(f"   строк в таблице: {loader.row_count()}, записано во второй раз: {second.rows_written}")

        print("3) источник отдал обновлённые значения")
        changed = make_rows(DAYS[0], seed=2)
        third = loader.load(lambda: changed)
        print(f"   обновлено строк: {third.rows_written}, всего строк: {loader.row_count()}")

        print("4) backfill за три дня")
        results = loader.backfill(DAYS, lambda day: make_rows(day, seed=3))
        print(f"   прогонов: {len(results)}, строк в таблице: {loader.row_count()}")
        print(f"   водяной знак: {loader.current_watermark()}")

        print("5) журнал прогонов")
        for row in loader.last_runs(limit=6):
            print(
                f"   {row['started_at']}  {row['status']:<7} attempts={row['attempts']}"
                f" received={row['rows_received']:<4} written={row['rows_written']:<4}"
                f" deduped={row['rows_deduped']}"
            )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with Loader(dsn=args.dsn, table=args.table, key="client_id", watermark="dt") as loader:
        print(json.dumps(
            {
                "table": args.table,
                "rows": loader.row_count(),
                "watermark": loader.current_watermark(),
                "runs": loader.last_runs(limit=args.limit),
            },
            ensure_ascii=False,
            indent=2,
        ))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="etl-watermark", description="Идемпотентная инкрементальная загрузка")
    parser.add_argument("--dsn", default="artifacts/etl.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="показать идемпотентность, upsert и backfill").set_defaults(func=cmd_demo)
    status = sub.add_parser("status", help="состояние загрузки")
    status.add_argument("--table", default="client_daily_amount")
    status.add_argument("--limit", type=int, default=10)
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, RuntimeError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
