"""CLI мини-оркестратора: демо-DAG, запуск, история."""

from __future__ import annotations

import argparse
import json
import random
import sys

from .dag import DAG, Task
from .executor import Executor


def build_demo_dag(fail_load: bool = False, flaky: bool = True) -> DAG:
    """Демо: extract -> две независимые трансформации -> load -> notify."""
    attempts = {"transform_b": 0}

    def extract(ctx):
        return {"rows": 100, "source": "abs_core"}

    def transform_a(ctx):
        rows = ctx.output("extract")["rows"]
        return {"clients": rows // 2, "kind": "a"}

    def transform_b(ctx):
        attempts["transform_b"] += 1
        if flaky and attempts["transform_b"] == 1:
            raise ConnectionError("витрина временно недоступна")
        return {"clients": ctx.output("extract")["rows"] // 2, "kind": "b"}

    def load(ctx):
        if fail_load:
            raise ValueError("целевая таблица заблокирована")
        a = ctx.output("transform_a")["clients"]
        b = ctx.output("transform_b")["clients"]
        return {"loaded": a + b}

    def notify(ctx):
        return {"message": f"загружено строк: {ctx.output('load')['loaded']}"}

    return DAG(
        name="mart_clients_daily",
        description="демонстрационный конвейер загрузки витрины",
        tasks=[
            Task("extract", extract),
            Task("transform_a", transform_a, depends_on=("extract",)),
            Task("transform_b", transform_b, depends_on=("extract",), retries=1, retry_delay=0.0),
            Task("load", load, depends_on=("transform_a", "transform_b"), retries=1),
            Task("notify", notify, depends_on=("load",)),
        ],
    )


def cmd_demo(args: argparse.Namespace) -> int:
    dag = build_demo_dag(fail_load=args.fail_load, flaky=not args.stable)
    print(f"граф {dag.name}: {' -> '.join(dag.topological_order())}")
    print("уровни параллелизма: " + " | ".join(",".join(wave) for wave in dag.waves()))

    with Executor(dag, dsn=args.dsn, max_workers=args.workers) as executor:
        result = executor.run()
        for name in dag.topological_order():
            state = result.tasks[name]
            error = f" — {state['error']}" if state.get("error") else ""
            print(f"  {state['status']:<15} {name:<12} attempts={state['attempts']}{error}")
        print(f"итог: {result.status}, {result.duration_ms} мс, run_id={result.run_id}")
        history = executor.history(limit=3)
    if args.json:
        print(json.dumps({"result": result.as_dict(), "history": history}, ensure_ascii=False, indent=2))
    return 0 if result.status == "SUCCESS" else 1


def cmd_history(args: argparse.Namespace) -> int:
    with Executor(build_demo_dag(), dsn=args.dsn) as executor:
        rows = executor.history(limit=args.limit)
    for row in rows:
        print(f"{row['started_at']}  {row['status']:<7} {row['duration_ms']:>6} мс  {row['run_id']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mini-orchestrator", description="DAG-планировщик на потоках")
    parser.add_argument("--dsn", default="artifacts/orchestrator.sqlite3")
    parser.add_argument("--workers", type=int, default=4)
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="прогнать демо-DAG")
    demo.add_argument("--fail-load", action="store_true", help="сломать загрузку (проверить каскад)")
    demo.add_argument("--stable", action="store_true", help="без транзиентной ошибки источника")
    demo.add_argument("--json", action="store_true")
    demo.set_defaults(func=cmd_demo)

    history = sub.add_parser("history", help="история прогонов")
    history.add_argument("--limit", type=int, default=5)
    history.set_defaults(func=cmd_history)
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
