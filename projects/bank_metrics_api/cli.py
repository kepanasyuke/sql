"""CLI: поднять API-сервер."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

from .api import make_server


def cmd_serve(args: argparse.Namespace) -> int:
    server = make_server(args.db, port=args.port, seed_data=not args.no_seed)
    host, port = server.server_address
    print(f"API слушает http://{host}:{port}")
    print("маршруты: /health /clients /clients/{id}/turnover /portfolio /dq")
    if args.self_test:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=5) as response:
            print(json.dumps(json.load(response), ensure_ascii=False))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлено")
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bank-metrics-api", description="HTTP JSON API по метрикам клиентов")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="запустить сервер")
    serve.add_argument("--db", default="artifacts/metrics_api.sqlite3")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-seed", action="store_true", help="не наполнять демо-данными")
    serve.add_argument("--self-test", action="store_true", help="проверить /health после старта")
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OSError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
