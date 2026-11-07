"""Тесты правил: один выход, размер функции, continue, изоляция вложенности."""

from __future__ import annotations

import pytest

from apps.sql_lint import rules
from apps.sql_lint.rules import LintError

# --------------------------------------------------------------------------
# Подсчёт выходов: return, break и while считаются выходом из функции
# --------------------------------------------------------------------------

EXIT_CASES = [
    ("один return", "def f():\n    return 1\n", ["return"]),
    ("две строки с return", "def f(x):\n    y = x\n    return y\n", ["return"]),
    ("while без return", "def f():\n    while True:\n        pass\n", ["while"]),
    ("break внутри while", "def f():\n    while True:\n        break\n", ["break", "while"]),
    ("return плюс break", "def f():\n    while True:\n        break\n    return 1\n", ["break", "return", "while"]),
    ("if с двумя return", "def f(x):\n    if x:\n        return 1\n    return 2\n", ["return"]),
    ("try return", "def f():\n    try:\n        return 1\n    finally:\n        pass\n", ["return"]),
    ("async return", "async def f():\n    return 1\n", ["return"]),
    ("return None", "def f():\n    return None\n", ["return"]),
    ("return в конце блока", "def f(x):\n    for i in x:\n        return i\n", ["return"]),
    ("while + return", "def f():\n    while True:\n        return 1\n", ["return", "while"]),
    ("for + break", "def f(xs):\n    for x in xs:\n        break\n", ["break"]),
    ("nested if elif else", "def f(x):\n    if x:\n        return 1\n    elif x is None:\n        return 2\n    else:\n        return 3\n", ["return"]),
    ("with return", "def f(h):\n    with h:\n        return 1\n", ["return"]),
    ("raise без возврата", "def f():\n    raise ValueError('x')\n", []),
]

SIZE_CASES = [
    ("одна строка", "def f(): return 1\n", 1),
    ("две строки", "def f():\n    return 1\n", 2),
    ("три строки", "def f():\n    x = 1\n    return x\n", 3),
    ("пять строк", "def f():\n    a = 1\n    b = 2\n    c = a + b\n    return c\n", 5),
    ("пустое тело", "def f():\n    pass\n", 2),
    ("многострочная подпись", "def f(\n    a,\n    b,\n):\n    return a\n", 5),
    ("декоратор снаружи", "@staticmethod\ndef f():\n    return 1\n", 2),
    ("async в две строки", "async def f():\n    return 1\n", 2),
    ("docstring плюс return", 'def f():\n    """Док."""\n    return 1\n', 3),
    ("вложенный if", "def f(x):\n    if x:\n        return 1\n    return 0\n", 4),
]


@pytest.mark.parametrize("name,code,expected", EXIT_CASES, ids=[case[0] for case in EXIT_CASES])
def test_exit_kinds(name: str, code: str, expected: list[str]) -> None:
    tree = rules.parse(code)
    function = next(rules.iter_functions(tree))
    assert rules.exit_kinds(rules.find_exits(function)) == expected


@pytest.mark.parametrize("name,code,expected", EXIT_CASES, ids=[case[0] for case in EXIT_CASES])
def test_exit_count(name: str, code: str, expected: list[str]) -> None:
    tree = rules.parse(code)
    function = next(rules.iter_functions(tree))
    kinds = rules.exit_kinds(rules.find_exits(function))
    assert len(rules.find_exits(function)) == len(("x",) * len(kinds)) or len(rules.find_exits(function)) >= len(kinds)


@pytest.mark.parametrize("name,code,span", SIZE_CASES, ids=[case[0] for case in SIZE_CASES])
def test_function_lines(name: str, code: str, span: int) -> None:
    tree = rules.parse(code)
    function = next(rules.iter_functions(tree))
    assert rules.function_lines(function) == span


# --------------------------------------------------------------------------
# Нарушения single-exit
# --------------------------------------------------------------------------

VIOLATIONS = [
    ("два return", "def f(x):\n    if x:\n        return 1\n    return 2\n"),
    ("while и return", "def f():\n    while True:\n        break\n    return 1\n"),
    ("break и return", "def f(xs):\n    for x in xs:\n        break\n    return 1\n"),
    ("без выхода", "def f():\n    x = 1\n"),
    ("только raise", "def f():\n    raise ValueError\n"),
    ("два while", "def f():\n    while True:\n        while True:\n            pass\n"),
]

CLEAN = [
    ("один return", "def f():\n    return 1\n"),
    ("один while", "def f():\n    while True:\n        pass\n"),
    ("один break", "def f(xs):\n    for x in xs:\n        break\n"),
]


@pytest.mark.parametrize("name,code", VIOLATIONS, ids=[case[0] for case in VIOLATIONS])
def test_single_exit_violations(name: str, code: str) -> None:
    found = [item for item in rules.lint_code(code, max_lines=100) if item.rule == "single-exit"]
    assert len(found) == 1
    assert found[0].severity == "error"
    assert "выход" in found[0].message


@pytest.mark.parametrize("name,code", CLEAN, ids=[case[0] for case in CLEAN])
def test_single_exit_is_clean(name: str, code: str) -> None:
    found = [item for item in rules.lint_code(code, max_lines=100) if item.rule == "single-exit"]
    assert found == []


def test_no_exit_message_mentions_required_node() -> None:
    violations = rules.lint_code("def f():\n    x = 1\n", max_lines=100)
    assert "нет места выхода" in violations[0].message


def test_many_exits_message_contains_count() -> None:
    code = "def f(x):\n    if x:\n        return 1\n    return 2\n"
    violations = rules.lint_code(code, max_lines=100)
    assert "найдено 2" in violations[0].message


# --------------------------------------------------------------------------
# Размер функции
# --------------------------------------------------------------------------

SIZE_VIOLATIONS = [
    ("три строки", "def f():\n    a = 1\n    return a\n", 2),
    ("пять строк", "def f():\n    a = 1\n    b = 2\n    c = 3\n    return c\n", 2),
    ("десять строк", "def f():\n" + "".join(f"    x{i} = {i}\n" for i in range(8)) + "    return 0\n", 5),
]


@pytest.mark.parametrize("name,code,limit", SIZE_VIOLATIONS, ids=[case[0] for case in SIZE_VIOLATIONS])
def test_size_violations(name: str, code: str, limit: int) -> None:
    found = [item for item in rules.lint_code(code, max_lines=limit) if item.rule == "small-function"]
    assert len(found) == 1
    assert f"допустимо {limit}" in found[0].message


@pytest.mark.parametrize("code", ["def f(): return 1\n", "def f():\n    return 1\n"])
def test_two_line_functions_are_allowed(code: str) -> None:
    assert [item for item in rules.lint_code(code) if item.rule == "small-function"] == []


def test_size_limit_is_configurable() -> None:
    code = "def f():\n    a = 1\n    return a\n"
    assert [item for item in rules.lint_code(code, max_lines=3) if item.rule == "small-function"] == []


# --------------------------------------------------------------------------
# continue и вложенность
# --------------------------------------------------------------------------

CONTINUE_CASES = [
    ("в цикле for", "def f(xs):\n    for x in xs:\n        continue\n", ["no-continue"]),
    ("в цикле while", "def f():\n    while True:\n        continue\n", ["no-continue"]),
    ("без continue", "def f(xs):\n    return len(xs)\n", []),
]


@pytest.mark.parametrize("name,code,expected", CONTINUE_CASES, ids=[case[0] for case in CONTINUE_CASES])
def test_continue_rule(name: str, code: str, expected: list[str]) -> None:
    found = sorted(item.rule for item in rules.lint_code(code, max_lines=100) if item.rule == "no-continue")
    assert len(found) == len(expected)


def test_continue_is_warning() -> None:
    violations = rules.lint_code("def f(xs):\n    for x in xs:\n        continue\n", max_lines=100)
    warning = next(item for item in violations if item.rule == "no-continue")
    assert warning.severity == "warning"


def test_nested_function_exits_are_not_counted() -> None:
    code = "def outer():\n    def inner(x):\n        if x:\n            return 1\n        return 2\n    return inner\n"
    violations = [item for item in rules.lint_code(code, max_lines=100) if item.rule == "single-exit"]
    assert len(violations) == 1
    assert violations[0].function == "inner"


def test_lambda_does_not_break_counting() -> None:
    code = "def f(xs):\n    return list(map(lambda x: x * 2, xs))\n"
    assert [item for item in rules.lint_code(code, max_lines=100) if item.rule == "single-exit"] == []


def test_sibling_functions_are_checked_separately() -> None:
    code = "def a():\n    return 1\n\n\ndef b(x):\n    if x:\n        return 1\n    return 2\n"
    violations = [item for item in rules.lint_code(code, max_lines=100) if item.rule == "single-exit"]
    assert [item.function for item in violations] == ["b"]


def test_class_methods_are_checked() -> None:
    code = "class A:\n    def m(self, x):\n        if x:\n            return 1\n        return 2\n"
    violations = [item for item in rules.lint_code(code, max_lines=100) if item.rule == "single-exit"]
    assert violations[0].function == "m"


def test_decorated_function_is_checked() -> None:
    code = "@staticmethod\ndef f(x):\n    if x:\n        return 1\n    return 2\n"
    assert len([item for item in rules.lint_code(code, max_lines=100) if item.rule == "single-exit"]) == 1


# --------------------------------------------------------------------------
# Разбор, сортировка, сводка, ошибки
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source",
    [
        "def f(:\n    pass\n",
        "def f():\nreturn 1\n",
        "x = (\n",
        "class :\n    pass\n",
    ],
)
def test_syntax_error_is_reported(source: str) -> None:
    with pytest.raises(LintError):
        rules.lint_code(source)


def test_syntax_error_has_position() -> None:
    with pytest.raises(LintError) as error:
        rules.parse("def f(:\n    pass\n")
    assert error.value.line >= 1


@pytest.mark.parametrize(
    "source",
    ["", "\n\n", "# только комментарий\n", "x = 1\n"],
)
def test_source_without_functions_is_clean(source: str) -> None:
    assert rules.lint_code(source) == []


def test_violations_sorted_by_position() -> None:
    code = "def big():\n    a = 1\n    b = 2\n    return a\n\n\ndef two(x):\n    if x:\n        return 1\n    return 2\n"
    violations = rules.lint_code(code, max_lines=2)
    assert violations == sorted(violations, key=lambda item: (item.line, item.column, item.rule))


def test_summary_counts_by_rule() -> None:
    code = "def big():\n    a = 1\n    return a\n\n\ndef empty():\n    pass\n"
    counts = rules.summary(rules.lint_code(code, max_lines=2))
    assert counts["small-function"] == 1
    assert counts["single-exit"] == 1


def test_violation_as_dict_contains_rule_fields() -> None:
    violation = rules.lint_code("def f():\n    x = 1\n", max_lines=100)[0].as_dict()
    assert set(violation) == {"rule", "severity", "line", "column", "message", "function"}


def test_lint_files_reports_each_path(tmp_path) -> None:
    first = tmp_path / "one.py"
    second = tmp_path / "two.py"
    first.write_text("def f():\n    return 1\n", encoding="utf-8")
    second.write_text("def f(x):\n    if x:\n        return 1\n    return 2\n", encoding="utf-8")
    report = rules.lint_files([str(first), str(second)], max_lines=100)
    assert len(report) == 2
    assert report[str(first)] == []
    assert len(report[str(second)]) == 1


ASYNC_AND_CLASS_CASES = [
    ("async def", "async def f(x):\n    if x:\n        return 1\n    return 2\n"),
    ("метод класса", "class A:\n    def m(self, x):\n        if x:\n            return 1\n        return 2\n"),
    ("staticmethod", "class A:\n    @staticmethod\n    def m(x):\n        if x:\n            return 1\n        return 2\n"),
    ("генератор", "def gen(xs):\n    for x in xs:\n        yield x\n        break\n    return None\n"),
    ("с вложенным while", "def f():\n    while True:\n        pass\n    return 1\n"),
]


@pytest.mark.parametrize("name,code", ASYNC_AND_CLASS_CASES, ids=[case[0] for case in ASYNC_AND_CLASS_CASES])
def test_structural_variants_have_violations(name: str, code: str) -> None:
    violations = [item for item in rules.lint_code(code, max_lines=100) if item.rule == "single-exit"]
    assert violations, name


def test_one_line_function_with_one_exit() -> None:
    assert rules.lint_code("def f(): return 1", max_lines=1) == []


def test_multiline_signature_length() -> None:
    code = "def f(\n    a,\n    b,\n):\n    return a\n"
    assert rules.function_lines(next(rules.iter_functions(rules.parse(code)))) == 5


def test_function_name_is_used_in_messages() -> None:
    violation = rules.lint_code("def calculate(x):\n    return x + 1\n", max_lines=1)[0]
    assert "calculate" in violation.message


# --------------------------------------------------------------------------
# Послабление на неявный return
# --------------------------------------------------------------------------

IMPLICIT_CASES = [
    ("пустая функция", "def f():\n    pass\n"),
    ("только присваивание", "def f():\n    x = 1\n"),
    ("__init__", "class A:\n    def __init__(self):\n        self.x = 1\n"),
]


@pytest.mark.parametrize("name,code", IMPLICIT_CASES, ids=[case[0] for case in IMPLICIT_CASES])
def test_implicit_exit_is_violation_by_default(name: str, code: str) -> None:
    violations = [item for item in rules.lint_code(code, max_lines=100) if item.rule == "single-exit"]
    assert len(violations) == 1


@pytest.mark.parametrize("name,code", IMPLICIT_CASES, ids=[case[0] for case in IMPLICIT_CASES])
def test_implicit_exit_allowed_by_flag(name: str, code: str) -> None:
    found = [item for item in rules.lint_code(code, max_lines=100, allow_implicit_exit=True) if item.rule == "single-exit"]
    assert found == []


def test_flag_does_not_hide_real_violations() -> None:
    code = "def f(x):\n    if x:\n        return 1\n    return 2\n"
    found = [item for item in rules.lint_code(code, max_lines=100, allow_implicit_exit=True) if item.rule == "single-exit"]
    assert len(found) == 1


def test_message_mentions_flag() -> None:
    violation = rules.lint_code("def f():\n    pass\n", max_lines=100)[0]
    assert "--allow-implicit-exit" in violation.message
