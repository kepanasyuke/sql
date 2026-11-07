"""Покрытие тестами без внешних зависимостей: ``sys.monitoring`` + HTML-сайт.

Запускает pytest программно, ловит события LINE и раскладывает результат в
статический сайт: индекс со сводкой и страницу на каждый файл с подсветкой
покрытых и пропущенных строк.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

TOOL_ID = 4
TOOL_NAME = "sql-lint-coverage"


@dataclass
class Collector:
    """Собирает исполненные строки только для файлов приложения.

    Для чужих файлов обработчик возвращает ``sys.monitoring.DISABLE``: событие
    для этой строки больше не приходит, иначе pytest с его миллионами строк
    замедляет прогон в десятки раз.
    """

    root: Path
    hits: dict[str, set[int]] = field(default_factory=dict)
    active: bool = False
    exclude: tuple[str, ...] = ("tests",)

    def _own(self, filename: Path) -> bool:
        try:
            relative: Path | None = filename.relative_to(self.root.resolve())
        except ValueError:
            relative = None
        return relative is not None and not any(part in self.exclude for part in relative.parts)

    def _callback(self, code: Any, line: int) -> Any:
        filename = Path(code.co_filename).resolve()
        if self._own(filename):
            self.hits.setdefault(str(filename), set()).add(line)
        return sys.monitoring.DISABLE

    def start(self) -> None:
        monitor = sys.monitoring
        monitor.use_tool_id(TOOL_ID, TOOL_NAME)
        monitor.register_callback(TOOL_ID, monitor.events.LINE, self._callback)
        monitor.set_events(TOOL_ID, monitor.events.LINE)
        self.active = True

    def stop(self) -> None:
        if not self.active:
            return
        monitor = sys.monitoring
        monitor.set_events(TOOL_ID, 0)
        monitor.register_callback(TOOL_ID, monitor.events.LINE, None)
        monitor.free_tool_id(TOOL_ID)
        self.active = False


def executable_lines(source: str) -> set[int]:
    try:
        tree: ast.AST | None = ast.parse(source)
    except SyntaxError:
        tree = None
    nodes = ast.walk(tree) if tree is not None else []
    return {node.lineno for node in nodes if isinstance(node, ast.stmt)}


def percent(covered: int, total: int) -> float:
    return round(100.0 * covered / total, 1) if total else 100.0


def file_report(path: Path, hits: Iterable[int]) -> dict:
    source = path.read_text(encoding="utf-8")
    statements = executable_lines(source)
    covered = statements & set(hits)
    return {
        "file": str(path),
        "source": source,
        "statements": sorted(statements),
        "covered": sorted(covered),
        "missing": sorted(statements - covered),
        "total": len(statements),
        "hit": len(covered),
        "percent": percent(len(covered), len(statements)),
    }


def collect_files(root: Path, hits: dict[str, set[int]]) -> list[dict]:
    reports: list[dict] = []
    for raw, lines in sorted(hits.items()):
        path = Path(raw)
        if path.suffix == ".py" and path.exists():
            reports.append(file_report(path, lines))
    return reports


def _bar(value: float) -> str:
    width = max(0, min(100, int(round(value))))
    color = "#6ee7b7" if value >= 85 else "#fbbf24" if value >= 60 else "#f87171"
    return (
        f'<span class="bar"><span style="width:{width}%;background:{color}"></span></span>'
        f'<span class="pct">{value:.1f}%</span>'
    )


PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin:0; background:#0b1220; color:#e6edf7; font-family:"Segoe UI",Roboto,Arial,sans-serif; line-height:1.55; }}
  .wrap {{ width:min(1080px,94vw); margin:0 auto; padding:26px 0 48px; }}
  h1 {{ font-size:24px; margin:0 0 6px; }}
  .muted {{ color:#93a4bd; font-size:13.5px; }}
  table {{ width:100%; border-collapse:collapse; margin-top:18px; font-size:13.5px; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #24344c; }}
  th {{ color:#93a4bd; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.05em; }}
  a {{ color:#6ee7b7; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .bar {{ display:inline-block; width:150px; height:8px; background:#1b2b41; border-radius:4px; overflow:hidden; vertical-align:middle; }}
  .bar span {{ display:block; height:100%; }}
  .pct {{ font-family:ui-monospace,Consolas,monospace; font-size:12px; margin-left:8px; }}
  pre {{ margin:0; padding:14px 16px; background:#0e1626; border:1px solid #24344c; border-radius:12px;
         font-family:ui-monospace,Consolas,monospace; font-size:12.5px; overflow:auto; }}
  .ln {{ display:block; }}
  .ln.hit {{ background:rgba(110,231,183,.10); }}
  .ln.miss {{ background:rgba(248,113,113,.16); }}
  .num {{ display:inline-block; width:42px; color:#5b6c85; user-select:none; }}
  .stat {{ display:flex; gap:18px; flex-wrap:wrap; margin-top:10px; font-family:ui-monospace,Consolas,monospace; font-size:12.5px; color:#93a4bd; }}
  .stat b {{ color:#e6edf7; }}
</style></head><body><div class="wrap">
{body}
</div></body></html>
"""


def render_file_page(report: dict) -> str:
    covered = set(report["covered"])
    missing = set(report["missing"])
    lines: list[str] = []
    for number, text in enumerate(report["source"].splitlines(), start=1):
        css = "hit" if number in covered else "miss" if number in missing else ""
        lines.append(f'<span class="ln {css}"><span class="num">{number}</span>{_escape(text)}</span>')
    body = (
        f'<h1>{_escape(Path(report["file"]).name)}</h1>'
        f'<div class="stat"><span>покрыто: <b>{report["hit"]}</b> из <b>{report["total"]}</b></span>'
        f'<span>{_bar(report["percent"])}</span></div>'
        f'<pre>{"".join(lines)}</pre>'
        '<p class="muted"><a href="index.html">← ко всем файлам</a></p>'
    )
    return PAGE.format(title=f'Покрытие — {Path(report["file"]).name}', body=body)


def render_index(reports: Sequence[dict]) -> str:
    total = sum(item["total"] for item in reports)
    covered = sum(item["hit"] for item in reports)
    rows = "".join(
        f'<tr><td><a href="{page_name(item["file"])}">{_escape(item["file"])}</a></td>'
        f'<td>{item["total"]}</td><td>{item["hit"]}</td><td>{_bar(item["percent"])}</td></tr>'
        for item in reports
    )
    body = (
        "<h1>Покрытие тестами · sql-lint</h1>"
        f'<div class="muted">Собрано собственным сборщиком на <code>sys.monitoring</code>, без внешних зависимостей.</div>'
        f'<div class="stat"><span>файлов: <b>{len(reports)}</b></span>'
        f'<span>операторов: <b>{total}</b></span><span>покрыто: <b>{covered}</b></span>'
        f'<span>итого: <b>{percent(covered, total):.1f}%</b></span></div>'
        '<table><thead><tr><th>файл</th><th>операторов</th><th>покрыто</th><th>покрытие</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )
    return PAGE.format(title="Покрытие тестами — sql-lint", body=body)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def page_name(file: str) -> str:
    return Path(file).name.replace(".py", "") + ".html"


def build_site(reports: Sequence[dict], target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for report in reports:
        (target / page_name(report["file"])).write_text(render_file_page(report), encoding="utf-8")
    (target / "index.html").write_text(render_index(reports), encoding="utf-8")
    summary = [
        {key: value for key, value in report.items() if key != "source"}
        for report in reports
    ]
    (target / "coverage.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def run_tests(root: Path, pytest_args: Sequence[str], target: Path) -> dict:
    import pytest

    collector = Collector(root=root)
    collector.start()
    try:
        exit_code = int(pytest.main(list(pytest_args)))
    finally:
        collector.stop()
    reports = collect_files(root, collector.hits)
    build_site(reports, target)
    total = sum(item["total"] for item in reports)
    covered = sum(item["hit"] for item in reports)
    return {
        "exit_code": exit_code,
        "files": len(reports),
        "statements": total,
        "covered": covered,
        "percent": percent(covered, total),
        "site": str(target),
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="sql-lint-coverage", description="Покрытие тестами и HTML-сайт")
    parser.add_argument("--root", default="apps/sql_lint")
    parser.add_argument("--target", default="docs/coverage")
    parser.add_argument("tests", nargs="?", default="apps/sql_lint/tests")
    args = parser.parse_args(list(argv) if argv is not None else None)

    summary = run_tests(Path(args.root), ["-q", args.tests], Path(args.target))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["exit_code"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
