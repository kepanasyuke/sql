"""CLI миграций: status / apply / down / demo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .migrator import MigrationError, Migrator, discover

DEFAULT_DIR = Path(__file__).parent / "migrations"


def _print_status(rows: list[dict]) -> None:
    print("версия | миграция                  | состояние | checksum")
    for row in rows:
        print(
            f"{row['version']:>6} | {row['name']:<24} | {row['state']:<9} | {row['checksum']}"
        )


def cmd_status(args: argparse.Namespace) -> int:
    with Migrator(args.dsn, args.dir) as migrator:
        rows = migrator.status()
    _print_status(rows)
    drift = [row for row in rows if row["state"] == "DRIFT"]
    if drift:
        print(f"ВНИМАНИЕ: дрейф в {len(drift)} миграциях", file=sys.stderr)
        return 1
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    with Migrator(args.dsn, args.dir) as migrator:
        applied = migrator.apply(target_version=args.to)
        rows = migrator.status()
    for item in applied:
        print(f"применена: {item['version']:04d}_{item['name']}")
    if not applied:
        print("нет неприменённых миграций")
    _print_status(rows)
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    with Migrator(args.dsn, args.dir) as migrator:
        rolled = migrator.rollback(version=args.version)
    if not rolled:
        print("нечего откатывать")
    for item in rolled:
        print(f"откатана: {item['version']:04d}_{item['name']}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    print(f"каталог миграций: {args.dir}")
    for migration in discover(args.dir):
        print(f"  {migration.version:04d}_{migration.name}  up={len(migration.up)} симв., down={len(migration.down)} симв.")

    with Migrator(args.dsn, args.dir) as migrator:
        print("\n1) применение всех миграций")
        for item in migrator.apply():
            print(f"   applied {item['version']:04d}_{item['name']}")
        print("\n2) повторный apply (идемпотентность)")
        print(f"   применено: {len(migrator.apply())}")
        print("\n3) статус")
        _print_status(migrator.status())
        print("\n4) откат последней миграции")
        for item in migrator.rollback():
            print(f"   rolled back {item['version']:04d}_{item['name']}")
        print("\n5) дрейф: правим уже применённую миграцию")
        first = discover(args.dir)[0]
        original = first.path.read_text(encoding="utf-8")
        first.path.write_text(original + "\n-- правка после применения\n", encoding="utf-8")
        try:
            migrator.apply()
        except MigrationError as exc:
            print(f"   ожидаемая ошибка: {exc}")
        finally:
            first.path.write_text(original, encoding="utf-8")

        if args.json:
            print(json.dumps(migrator.status(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sql-migrations", description="Миграции с контролем дрейфа")
    parser.add_argument("--dsn", default="artifacts/migrations.sqlite3")
    parser.add_argument("--dir", default=str(DEFAULT_DIR))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="состояние миграций").set_defaults(func=cmd_status)

    apply = sub.add_parser("apply", help="применить неприменённые")
    apply.add_argument("--to", type=int, help="применить до версии включительно")
    apply.set_defaults(func=cmd_apply)

    down = sub.add_parser("down", help="откатить миграцию")
    down.add_argument("--version", type=int, help="какую откатить (по умолчанию последнюю)")
    down.set_defaults(func=cmd_down)

    demo = sub.add_parser("demo", help="показать полный цикл")
    demo.add_argument("--json", action="store_true")
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (MigrationError, OSError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
