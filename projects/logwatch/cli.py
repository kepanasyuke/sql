"""CLI: разобрать лог, выпустить алерты, выгрузить метрики."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from .export import render
from .parser import parse_stream
from .rules import RuleEngine, default_rules


def demo_lines(seed: int = 3, requests: int = 40) -> list[str]:
    rng = random.Random(seed)
    lines = []
    minute = 0
    for index in range(requests):
        minute += rng.choice([0, 0, 1])
        status = rng.choices([200, 200, 404, 500, 503], weights=[70, 10, 8, 7, 5])[0]
        lines.append(
            f'10.0.0.{rng.randint(1, 30)} - - [06/Nov/2026:10:{minute:02d}:00 +0300] '
            f'"GET /api/clients/{index} HTTP/1.1" {status} {rng.randint(200, 9000)} '
            f'"-" "python-requests/2.32"'
        )
    lines.append(
        '{"ts": "2026-11-06T10:20:00+0300", "level": "error", "method": "POST", '
        '"path": "/api/transfer", "status": 500, "msg": "db pool exhausted", "duration_ms": 2310}'
    )
    lines.append(
        '{"ts": "2026-11-06T10:20:05+0300", "level": "error", "method": "POST", '
        '"path": "/api/transfer", "status": 500, "msg": "db pool exhausted", "duration_ms": 1980}'
    )
    return lines


def cmd_analyze(args: argparse.Namespace) -> int:
    lines = demo_lines() if args.demo else Path(args.file).read_text(encoding="utf-8").splitlines()
    records = parse_stream(lines)
    engine = RuleEngine(default_rules())
    alerts = engine.feed_all(records)

    if args.prom:
        Path(args.prom).parent.mkdir(parents=True, exist_ok=True)
        Path(args.prom).write_text(render(alerts, engine.rules, engine.counters), encoding="utf-8")
        print(f"метрики: {args.prom}")

    if args.json:
        print(json.dumps([alert.as_dict() for alert in alerts], ensure_ascii=False, indent=2))
    else:
        print(f"строк разобрано: {engine.counters['events']}")
        print(f"алертов: {engine.counters['alerts']}, подавлено повторов: {engine.counters['suppressed']}")
        for alert in alerts:
            print(f"  [{alert.severity}] {alert.rule_id}: {alert.fingerprint} x{alert.count} — {alert.message}")
    return 1 if any(alert.severity == "P1" for alert in alerts) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="logwatch", description="Разбор логов и алертинг")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="разобрать лог")
    analyze.add_argument("--file", help="путь к файлу лога")
    analyze.add_argument("--demo", action="store_true", help="сгенерировать демо-лог")
    analyze.add_argument("--prom", help="куда выгрузить Prometheus-метрики")
    analyze.add_argument("--json", action="store_true")
    analyze.set_defaults(func=cmd_analyze)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code = 2
    if not args.demo and not args.file:
        print("ошибка: нужен --file или --demo", file=sys.stderr)
    else:
        try:
            code = int(args.func(args))
        except OSError as exc:
            print(f"ошибка: {exc}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
