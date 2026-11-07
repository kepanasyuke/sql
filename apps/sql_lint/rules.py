"""Правила линтера: один выход на функцию, тело в 1-2 строки, без continue.

Выходом считается любой из узлов ``return``, ``break`` и ``while`` — по правилам
проекта цикл ``while`` сам является точкой выхода, поэтому функция не может
совмещать его с ``return``. Анализ идёт по AST, поэтому вложенные функции,
лямбды и строки не влияют на подсчёт.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

DEFAULT_MAX_LINES = 2
EXIT_TYPES = (ast.Return, ast.Break, ast.While)
EXIT_NAMES = {ast.Return: "return", ast.Break: "break", ast.While: "while"}

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


class LintError(ValueError):
    """Исходный код не разбирается как Python."""

    def __init__(self, message: str, line: int = 0, column: int = 0):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str
    line: int
    column: int
    message: str
    function: str = ""

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "line": self.line,
            "column": self.column,
            "message": self.message,
            "function": self.function,
        }


def parse(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise LintError(exc.msg or "синтаксическая ошибка", exc.lineno or 0, exc.offset or 0) from exc


def iter_functions(tree: ast.AST) -> Iterator[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _nested_ids(function: ast.AST) -> set[int]:
    nested: set[int] = set()
    for child in ast.walk(function):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and child is not function:
            nested.update(id(node) for node in ast.walk(child))
    return nested


def own_nodes(function: ast.AST, types: tuple[type, ...]) -> list[ast.AST]:
    nested = _nested_ids(function)
    return [node for node in ast.walk(function) if isinstance(node, types) and id(node) not in nested]


def find_exits(function: ast.AST) -> list[ast.AST]:
    return own_nodes(function, EXIT_TYPES)


def exit_kinds(exits: Iterable[ast.AST]) -> list[str]:
    return sorted({EXIT_NAMES[type(node)] for node in exits})


def function_lines(function: ast.AST) -> int:
    end = getattr(function, "end_lineno", None) or function.lineno
    return end - function.lineno + 1


def function_name(function: ast.AST) -> str:
    return getattr(function, "name", "<lambda>")


def _exit_message(name: str, exits: list[ast.AST]) -> str:
    kinds = ", ".join(exit_kinds(exits)) or "нет"
    found = f"должно быть ровно одно место выхода, найдено {len(exits)} ({kinds})"
    missing = (
        "нет места выхода: нужен ровно один return, break или while "
        "(или флаг --allow-implicit-exit)"
    )
    return f"у функции «{name}» {found if exits else missing}"


def check_single_exit(function: ast.AST, allow_implicit: bool = False) -> list[Violation]:
    """Ровно один выход; неявный return — отдельный режим для служебного кода."""
    exits = find_exits(function)
    name = function_name(function)
    valid = len(exits) == 1 or (allow_implicit and not exits)
    violation = Violation(
        rule="single-exit",
        severity=SEVERITY_ERROR,
        line=function.lineno,
        column=function.col_offset + 1,
        message=_exit_message(name, exits),
        function=name,
    )
    return [] if valid else [violation]


def check_size(function: ast.AST, max_lines: int) -> list[Violation]:
    span = function_lines(function)
    name = function_name(function)
    violation = Violation(
        rule="small-function",
        severity=SEVERITY_ERROR,
        line=function.lineno,
        column=function.col_offset + 1,
        message=f"функция «{name}» занимает {span} строк, допустимо {max_lines}",
        function=name,
    )
    return [] if span <= max_lines else [violation]


def check_continue(function: ast.AST) -> list[Violation]:
    name = function_name(function)
    return [
        Violation(
            rule="no-continue",
            severity=SEVERITY_WARNING,
            line=node.lineno,
            column=node.col_offset + 1,
            message=f"функция «{name}»: continue — дополнительный выход, перепишите через if/else",
            function=name,
        )
        for node in own_nodes(function, (ast.Continue,))
    ]


def lint_code(
    source: str,
    max_lines: int = DEFAULT_MAX_LINES,
    allow_implicit_exit: bool = False,
) -> list[Violation]:
    tree = parse(source)
    violations: list[Violation] = []
    for function in iter_functions(tree):
        violations.extend(check_single_exit(function, allow_implicit_exit))
        violations.extend(check_size(function, max_lines))
        violations.extend(check_continue(function))
    return sorted(violations, key=lambda item: (item.line, item.column, item.rule))


def lint_files(
    paths: Sequence[str],
    max_lines: int = DEFAULT_MAX_LINES,
    allow_implicit_exit: bool = False,
) -> dict[str, list[Violation]]:
    from pathlib import Path

    report: dict[str, list[Violation]] = {}
    for raw in paths:
        path = Path(raw)
        report[str(path)] = lint_code(path.read_text(encoding="utf-8"), max_lines, allow_implicit_exit)
    return report


def summary(violations: Iterable[Violation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for violation in violations:
        counts[violation.rule] = counts.get(violation.rule, 0) + 1
    return counts
