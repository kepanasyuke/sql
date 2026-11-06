"""Граф lineage: таблицы, колонки, анализ влияния."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from .parser import Query, SourceRef, Statement


@dataclass(frozen=True)
class ColumnEdge:
    source_table: str
    source_column: str
    target_table: str
    target_column: str

    def as_dict(self) -> dict:
        return {
            "from": f"{self.source_table}.{self.source_column}",
            "to": f"{self.target_table}.{self.target_column}",
        }


@dataclass
class LineageModel:
    tables: set[str] = field(default_factory=set)
    table_edges: set[tuple[str, str]] = field(default_factory=set)
    column_edges: list[ColumnEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # -- построение --------------------------------------------------------
    def add_statement(self, statement: Statement) -> None:
        target = statement.name
        self.tables.add(target)
        for source in _base_sources(statement.query, statement.query, set()):
            if source != target:
                self.table_edges.add((source, target))
        for output in statement.query.outputs:
            traced = _trace_column(statement.query, output.source_alias, output.source_column)
            if not traced:
                if output.source_column:
                    self.warnings.append(
                        f"{target}.{output.name}: не удалось проследить {output.expression or output.source_column}"
                    )
                continue
            for base_table, base_column in traced:
                edge = ColumnEdge(base_table, base_column, target, output.name)
                if edge not in self.column_edges:
                    self.column_edges.append(edge)

    # -- навигация ---------------------------------------------------------
    def upstream(self, table: str) -> list[str]:
        return sorted({source for source, target in self.table_edges if _same(target, table)})

    def downstream(self, table: str) -> list[str]:
        return sorted({target for source, target in self.table_edges if _same(source, table)})

    def all_downstream(self, table: str) -> list[str]:
        seen: set[str] = set()
        queue = deque(self.downstream(table))
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            queue.extend(self.downstream(node))
        return sorted(seen)

    def all_upstream(self, table: str) -> list[str]:
        seen: set[str] = set()
        queue = deque(self.upstream(table))
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            queue.extend(self.upstream(node))
        return sorted(seen)

    def column_upstream(self, table: str, column: str) -> list[str]:
        seen: list[str] = []
        stack = [(edge.source_table, edge.source_column) for edge in self.column_edges
                 if _same(edge.target_table, table) and edge.target_column == column]
        while stack:
            source_table, source_column = stack.pop()
            node = f"{source_table}.{source_column}"
            if node in seen:
                continue
            seen.append(node)
            stack.extend(
                (edge.source_table, edge.source_column)
                for edge in self.column_edges
                if _same(edge.target_table, source_table) and edge.target_column == source_column
            )
        return sorted(seen)

    def impact(self, table: str) -> dict:
        direct = self.downstream(table)
        transitive = self.all_downstream(table)
        leaves = [node for node in transitive if not self.downstream(node)]
        return {
            "table": table,
            "direct_downstream": direct,
            "all_downstream": transitive,
            "leaf_marts": leaves,
            "upstream": self.all_upstream(table),
            "affected_columns": sorted(
                {
                    f"{edge.target_table}.{edge.target_column}"
                    for edge in self.column_edges
                    if _same(edge.source_table, table)
                }
            ),
        }

    def topological_order(self) -> list[str]:
        nodes = set(self.tables) | {source for source, _ in self.table_edges}
        incoming = {node: 0 for node in nodes}
        for _, target in self.table_edges:
            incoming[target] = incoming.get(target, 0) + 1
        queue = deque(sorted(node for node, count in incoming.items() if count == 0))
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for child in self.downstream(node):
                incoming[child] -= 1
                if incoming[child] == 0:
                    queue.append(child)
        if len(order) != len(nodes):
            cyclic = sorted(nodes - set(order))
            raise ValueError(f"цикл в графе lineage: {cyclic}")
        return order

    # -- экспорт -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "tables": sorted(self.tables),
            "table_edges": [{"from": source, "to": target} for source, target in sorted(self.table_edges)],
            "column_edges": [edge.as_dict() for edge in self.column_edges],
            "warnings": self.warnings,
        }

    def to_dot(self) -> str:
        lines = ["digraph lineage {", '    rankdir="LR";', '    node [shape=box, fontname="Helvetica"];']
        for table in sorted(self.tables):
            shape = "note" if not self.downstream(table) and self.upstream(table) else "box"
            lines.append(f'    "{table}" [shape={shape}];')
        for source, target in sorted(self.table_edges):
            lines.append(f'    "{source}" -> "{target}";')
        lines.append("}")
        return "\n".join(lines)


def _same(left: str, right: str) -> bool:
    return left.lower() == right.lower()


def _query_aliases(query: Query) -> dict[str, SourceRef]:
    mapping: dict[str, SourceRef] = {}
    for source in query.sources:
        mapping[source.alias.lower()] = source
        mapping[source.name.lower()] = source
        mapping[source.table.lower()] = source
    return mapping


def _base_sources(query: Query, root: Query, visited: set[str]) -> set[str]:
    """Разворачивает CTE в реальные таблицы-источники."""
    result: set[str] = set()
    for source in query.sources:
        key = source.table.lower()
        if key in query.ctes and key not in visited:
            visited.add(key)
            result |= _base_sources(query.ctes[key], root, visited)
        elif key in root.ctes and key not in visited:
            visited.add(key)
            result |= _base_sources(root.ctes[key], root, visited)
        else:
            result.add(source.name)
    return result


def _trace_column(
    query: Query,
    alias: str | None,
    column: str | None,
    depth: int = 0,
    root: Query | None = None,
) -> list[tuple[str, str]]:
    """Идёт от выходной колонки к базовой таблице через CTE.

    CTE в аналитических скриптах почти всегда плоские (все объявлены в одном
    WITH), поэтому определения ищутся и в текущем запросе, и в корневом.
    """
    root = root or query
    ctes = query.ctes or root.ctes
    if not column or depth > 8:
        return []
    aliases = _query_aliases(query)
    source: SourceRef | None = None
    if alias:
        source = aliases.get(alias.lower())
    elif len({s.name.lower() for s in query.sources}) == 1:
        source = next(iter(query.sources))

    if source is None:
        for cte in ctes.values():
            output = next((item for item in cte.outputs if item.name == column), None)
            if output is not None:
                return _trace_column(cte, output.source_alias, output.source_column, depth + 1, root)
        return []

    key = source.table.lower()
    if key in ctes:
        cte = ctes[key]
        output = next((item for item in cte.outputs if item.name == column), None)
        if output is None or (output.source_alias is None and output.source_column is None):
            return []
        return _trace_column(cte, output.source_alias, output.source_column, depth + 1, root)
    return [(source.name, column)]


def build_model(statements: Iterable[Statement]) -> LineageModel:
    model = LineageModel()
    for statement in statements:
        model.add_statement(statement)
    return model
