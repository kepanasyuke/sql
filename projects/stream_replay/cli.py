"""CLI: воспроизвести поток событий и показать окна."""

from __future__ import annotations

import argparse
import json
import sys

from .pipeline import StreamPipeline, generate_events


def cmd_demo(args: argparse.Namespace) -> int:
    events = generate_events(
        count=args.events,
        duplicate_ratio=args.duplicates,
        late_ratio=args.late,
        seed=args.seed,
    )
    pipeline = StreamPipeline(
        window_seconds=args.window,
        allowed_lateness=args.lateness,
    )
    pipeline.process(events)
    result = pipeline.finish()

    print(f"событий получено: {result['metrics']['received']}")
    print(f"дубликатов отброшено: {result['metrics']['duplicates']}")
    print(f"опоздавших сверх допуска: {result['metrics']['late_dropped']}")
    print(f"окон закрыто: {result['metrics']['windows_closed']}")
    print("окно | событий | клиентов | сумма | топ-клиент")
    for window in result["windows"]:
        print(
            f"{window['window_start']}..{window['window_end']} | {window['events']:>3} | "
            f"{window['clients']:>3} | {window['total_amount']:>14,.2f} | {window['top_client']}"
        )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stream-replay", description="Потоковые окна с дедупликацией")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="прогнать демо-поток")
    demo.add_argument("--events", type=int, default=300)
    demo.add_argument("--window", type=int, default=60)
    demo.add_argument("--lateness", type=int, default=30)
    demo.add_argument("--duplicates", type=float, default=0.15)
    demo.add_argument("--late", type=float, default=0.03)
    demo.add_argument("--seed", type=int, default=11)
    demo.add_argument("--json", action="store_true")
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
