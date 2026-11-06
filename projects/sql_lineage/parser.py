"""Парсер SQL для построения lineage.

Задача: по набору SQL-скриптов ответить на вопросы «откуда взялась колонка»
и «что сломается, если таблица не пришла». Полноценный SQL-парсер здесь не
нужен и был бы неподъёмным, поэтому взят прагматичный подход: токенизация +
разбор структуры запроса (CTE, источники, SELECT-список). Такой парсер
переживает 95% реальных аналитических скриптов и не требует зависимостей.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<comment>--[^\n]*|/\*.*?\*/)
    | (?P<string>'(?:[^']|'')*')
    | (?P<number>\d+(?:\.\d+)?)
    | (?P<word>[A-Za-z_][A-Za-z0-9_$]*)
    | (?P<punct><=|>=|<>|!=|[(),.;*=<>+\-/%:])
    """,
    re.S | re.X,
)

KEYWORDS = {
    "select", "from", "join", "left", "right", "inner", "outer", "full", "cross",
    "on", "where", "group", "by", "order", "having", "limit", "offset", "union",
    "all", "as", "with", "and", "or", "using", "distinct", "case", "when", "then",
    "else", "end", "create", "table", "view", "insert", "into", "replace", "delete",
    "update", "set", "values", "asc", "desc", "null", "is", "not", "in", "exists",
    "between", "like", "over", "partition", "rows", "range", "preceding", "following",
    "current", "row", "unbounded", "cast", "date", "timestamp", "interval", "true",
    "false", "sum", "count", "avg", "min", "max", "coalesce", "round",
}


class SqlParseError(ValueError):
    """Скрипт не удалось разобрать."""


@dataclass(frozen=True)
class Token:
    kind: str
    value: str

    @property
    def low(self) -> str:
        return self.value.lower()


@dataclass
class SourceRef:
    name: str
    alias: str

    @property
    def table(self) -> str:
        return self.name.split(".")[-1]


@dataclass
class OutputColumn:
    name: str
    source_alias: str | None = None
    source_column: str | None = None
    expression: str = ""


@dataclass
class Query:
    sources: list[SourceRef] = field(default_factory=list)
    outputs: list[OutputColumn] = field(default_factory=list)
    ctes: dict[str, "Query"] = field(default_factory=dict)

    @property
    def aliases(self) -> dict[str, str]:
        return {source.alias: source.name for source in self.sources}


@dataclass
class Statement:
    name: str
    query: Query
    kind: str = "query"


def tokenize(sql: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    while pos < len(sql):
        match = TOKEN_RE.match(sql, pos)
        if not match:
            raise SqlParseError(f"неожиданный символ {sql[pos]!r} в позиции {pos}")
        pos = match.end()
        kind = match.lastgroup or ""
        if kind not in ("ws", "comment"):
            tokens.append(Token(kind, match.group()))
    return tokens


def _find_matching(tokens: list[Token], start: int) -> int:
    """Индекс закрывающей скобки для tokens[start] == '('."""
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index].value == "(":
            depth += 1
        elif tokens[index].value == ")":
            depth -= 1
            if depth == 0:
                return index
    raise SqlParseError("несбалансированные скобки")


def _split_top_level(tokens: list[Token]) -> list[list[Token]]:
    parts: list[list[Token]] = [[]]
    depth = 0
    for token in tokens:
        if token.value == "," and depth == 0:
            parts.append([])
        else:
            parts[-1].append(token)
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
    return [part for part in parts if part]


def _read_identifier(tokens: list[Token], index: int) -> tuple[str, int]:
    """Читает a.b.c начиная с index, возвращает имя и новую позицию."""
    if index >= len(tokens) or tokens[index].kind != "word":
        raise SqlParseError("ожидалось имя таблицы или колонки")
    parts = [tokens[index].value]
    index += 1
    while index + 1 < len(tokens) and tokens[index].value == "." and tokens[index + 1].kind == "word":
        parts.append(tokens[index + 1].value)
        index += 2
    return ".".join(parts), index


def _parse_sources(body: list[Token]) -> list[SourceRef]:
    sources: list[SourceRef] = []
    depth = 0
    index = 0
    while index < len(body):
        token = body[index]
        step = index + 1
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif depth == 0 and token.kind == "word" and token.low in ("from", "join"):
            try:
                name, cursor = _read_identifier(body, index + 1)
            except SqlParseError:
                step = index + 1
            else:
                alias = name.split(".")[-1]
                if cursor < len(body) and body[cursor].kind == "word" and body[cursor].low == "as":
                    cursor += 1
                if (
                    cursor < len(body)
                    and body[cursor].kind == "word"
                    and body[cursor].low not in KEYWORDS
                ):
                    alias = body[cursor].value
                    cursor += 1
                sources.append(SourceRef(name=name, alias=alias))
                step = cursor
        index = step
    return sources


def _parse_select_item(item: list[Token]) -> OutputColumn | None:
    """Один элемент SELECT-списка: колонка или None, если разбирать нечего."""
    column: OutputColumn | None = None
    if item and item[0].low == "distinct":
        item = item[1:]
    if item and not (len(item) == 1 and item[0].value == "*"):
        alias: str | None = None
        expression = item
        explicit = next(
            (
                (position, item[position + 1].value)
                for position, token in enumerate(item)
                if token.kind == "word" and token.low == "as" and position + 1 < len(item)
            ),
            None,
        )
        if explicit is not None:
            alias = explicit[1]
            expression = item[: explicit[0]]
        if alias is None and len(item) >= 2 and item[-1].kind == "word" and item[-2].kind == "word":
            # SELECT sum(x) total  -- неявный алиас
            if item[-2].value != ".":
                alias = item[-1].value
                expression = item[:-1]

        source_alias: str | None = None
        source_column: str | None = None
        qualified = next(
            (
                (token.value, expression[position + 2].value)
                for position, token in enumerate(expression)
                if (
                    token.kind == "word"
                    and token.low not in KEYWORDS
                    and position + 2 < len(expression)
                    and expression[position + 1].value == "."
                    and expression[position + 2].kind == "word"
                )
            ),
            None,
        )
        if qualified is not None:
            source_alias, source_column = qualified
        if source_column is None:
            words = [t for t in expression if t.kind == "word" and t.low not in KEYWORDS]
            if len(words) == 1 and (len(expression) == 1 or expression[0].value != "("):
                source_column = words[0].value

        column = OutputColumn(
            name=alias or source_column or "expr",
            source_alias=source_alias,
            source_column=source_column,
            expression=" ".join(token.value for token in expression)[:120],
        )
    return column


def _parse_select_list(tokens: list[Token]) -> list[OutputColumn]:
    columns = (_parse_select_item(item) for item in _split_top_level(tokens))
    return [column for column in columns if column is not None]


def parse_query(tokens: list[Token]) -> Query:
    """Разбирает запрос: CTE, источники, SELECT-список. Один выход — один Query."""
    query = Query()
    start = _query_start(tokens)
    if start is not None:
        tokens = tokens[start:]
        index = 0
        if tokens[0].low == "with":
            index = _parse_ctes(tokens, query)

        body = tokens[index:]
        select_at = _first_at_depth_zero(body, ("select",))
        if select_at is not None:
            from_at = _first_at_depth_zero(body, ("from", "where", "group"), select_at + 1)
            select_tokens = body[select_at + 1 : from_at if from_at is not None else len(body)]
            query.outputs = _parse_select_list(select_tokens)
            if from_at is not None:
                query.sources = _parse_sources(body[from_at:])
    return query


def _query_start(tokens: list[Token]) -> int | None:
    return _first_at_depth_zero(tokens, ("with", "select"))


def _first_at_depth_zero(tokens: list[Token], words: tuple[str, ...], start: int = 0) -> int | None:
    found: int | None = None
    depth = 0
    position = start
    while position < len(tokens) and found is None:
        token = tokens[position]
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif depth == 0 and token.kind == "word" and token.low in words:
            found = position
        position += 1
    return found


def _parse_ctes(tokens: list[Token], query: Query) -> int:
    """Читает все CTE и возвращает позицию начала основного SELECT."""
    index = 1
    more = index < len(tokens)
    while more:
        cte_name = tokens[index].value
        index += 1
        if index < len(tokens) and tokens[index].value == "(":
            index = _find_matching(tokens, index) + 1
        if index >= len(tokens) or tokens[index].low != "as":
            raise SqlParseError(f"ожидалось AS после имени CTE {cte_name}")
        index += 1
        if index >= len(tokens) or tokens[index].value != "(":
            raise SqlParseError(f"ожидалось '(' в определении CTE {cte_name}")
        end = _find_matching(tokens, index)
        query.ctes[cte_name.lower()] = parse_query(tokens[index + 1 : end])
        index = end + 1
        more = index < len(tokens) and tokens[index].value == ","
        if more:
            index += 1
    return index


def _statement_name(tokens: list[Token], index: int) -> tuple[str, str, int]:
    """Тип и имя целевой сущности: CREATE TABLE/VIEW ... AS, INSERT INTO ...."""
    name, kind, cursor = "", "query", index
    head = tokens[index].low
    if head == "create":
        kind, cursor = "table", index + 1
        if cursor < len(tokens) and tokens[cursor].low in ("table", "view"):
            kind = tokens[cursor].low
            cursor += 1
        if cursor < len(tokens) and tokens[cursor].low in ("if", "not", "exists"):
            while cursor < len(tokens) and tokens[cursor].low != "exists":
                cursor += 1
            cursor += 1
        name, cursor = _read_identifier(tokens, cursor)
    elif head == "insert":
        kind, cursor = "insert", index + 1
        if cursor < len(tokens) and tokens[cursor].low == "into":
            cursor += 1
        name, cursor = _read_identifier(tokens, cursor)
    return name, kind, cursor


def parse_script(sql: str) -> list[Statement]:
    """Разбирает скрипт на утверждения. Имя витрины берётся из CREATE/INSERT
    либо из комментария вида `-- name: mart.some_mart` над утверждением."""
    statements: list[Statement] = []
    chunks: list[tuple[str, str]] = []
    current: list[str] = []
    depth = 0
    pending_name = ""
    for raw_line in sql.splitlines():
        stripped = raw_line.strip()
        if stripped.lower().startswith("-- name:") and depth == 0:
            pending_name = stripped.split(":", 1)[1].strip()
        else:
            for char in raw_line:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
            current.append(raw_line)
            if depth == 0 and raw_line.rstrip().endswith(";"):
                chunks.append(("\n".join(current), pending_name))
                current, pending_name = [], ""
    if "".join(current).strip():
        chunks.append(("\n".join(current), pending_name))

    for chunk, name in chunks:
        tokens = tokenize(chunk)
        if tokens:
            target, kind, _ = _statement_name(tokens, 0)
            statements.append(
                Statement(
                    name=target or name or f"query_{len(statements) + 1}",
                    query=parse_query(tokens),
                    kind=kind,
                )
            )
    return statements
