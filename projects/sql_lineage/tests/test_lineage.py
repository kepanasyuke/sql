"""Тесты парсера и графа lineage."""

from __future__ import annotations

import pytest

from projects.sql_lineage.graph import build_model
from projects.sql_lineage.parser import parse_query, parse_script, tokenize

DEMO_SQL = """
-- name: mart.client_turnover
CREATE TABLE mart.client_turnover AS
WITH daily AS (
    SELECT client_id, dt, debit_turnover, credit_turnover
    FROM mart.fct_account_turnover_daily
    WHERE dt >= DATE '2026-01-01'
),
agg AS (
    SELECT d.client_id,
           SUM(d.debit_turnover) AS debit,
           SUM(d.credit_turnover) AS credit
    FROM daily d
    JOIN mart.dim_client c ON c.client_id = d.client_id
    GROUP BY d.client_id
)
SELECT client_id, debit, credit, credit - debit AS net_flow
FROM agg;

-- name: mart.client_score
CREATE TABLE mart.client_score AS
SELECT t.client_id, t.net_flow, c.segment
FROM mart.client_turnover t
JOIN mart.dim_client c ON c.client_id = t.client_id;
"""


def test_tokenizer_skips_comments_and_strings() -> None:
    tokens = tokenize("select 1 -- комментарий\n, 'строка с ; ' /* блок */ from t")
    values = [token.value for token in tokens]
    assert "--" not in " ".join(values)
    assert "'строка с ; '" in values
    assert values[0] == "select"


def test_parse_script_names_and_columns() -> None:
    statements = parse_script(DEMO_SQL)
    assert [statement.name for statement in statements] == [
        "mart.client_turnover",
        "mart.client_score",
    ]
    first = statements[0].query
    assert {output.name for output in first.outputs} == {"client_id", "debit", "credit", "net_flow"}
    assert set(first.ctes) == {"daily", "agg"}
    assert [source.name for source in first.sources] == ["agg"]


def test_parse_query_handles_cte_sources() -> None:
    tokens = tokenize("SELECT a, b FROM x JOIN y ON x.id = y.id")
    query = parse_query(tokens)
    assert [source.name for source in query.sources] == ["x", "y"]
    assert [output.name for output in query.outputs] == ["a", "b"]


def test_build_model_table_edges() -> None:
    model = build_model(parse_script(DEMO_SQL))
    assert ("mart.fct_account_turnover_daily", "mart.client_turnover") in model.table_edges
    assert ("mart.dim_client", "mart.client_turnover") in model.table_edges
    assert ("mart.client_turnover", "mart.client_score") in model.table_edges
    assert ("mart.dim_client", "mart.client_score") in model.table_edges


def test_column_lineage_through_two_ctes() -> None:
    model = build_model(parse_script(DEMO_SQL))
    edges = {(edge.target_table, edge.target_column): (edge.source_table, edge.source_column)
             for edge in model.column_edges}
    assert edges[("mart.client_turnover", "debit")] == ("mart.fct_account_turnover_daily", "debit_turnover")
    assert edges[("mart.client_turnover", "credit")] == ("mart.fct_account_turnover_daily", "credit_turnover")
    assert edges[("mart.client_score", "segment")] == ("mart.dim_client", "segment")


def test_impact_analysis() -> None:
    model = build_model(parse_script(DEMO_SQL))
    impact = model.impact("mart.fct_account_turnover_daily")
    assert impact["direct_downstream"] == ["mart.client_turnover"]
    assert impact["all_downstream"] == ["mart.client_score", "mart.client_turnover"]
    assert impact["leaf_marts"] == ["mart.client_score"]
    assert "mart.client_turnover.debit" in impact["affected_columns"]


def test_upstream_and_topological_order() -> None:
    model = build_model(parse_script(DEMO_SQL))
    assert model.upstream("mart.client_score") == ["mart.client_turnover", "mart.dim_client"]
    order = model.topological_order()
    assert order.index("mart.fct_account_turnover_daily") < order.index("mart.client_turnover")
    assert order.index("mart.client_turnover") < order.index("mart.client_score")


def test_unparseable_statement_is_reported() -> None:
    statements = parse_script("SELECT count(*) AS cnt FROM (SELECT some_func(x) FROM t) sub;")
    model = build_model(statements)
    assert model.to_dict()["tables"] == ["query_1"]


def test_dot_export_contains_edges() -> None:
    model = build_model(parse_script(DEMO_SQL))
    dot = model.to_dot()
    assert dot.startswith("digraph lineage")
    assert '"mart.fct_account_turnover_daily" -> "mart.client_turnover"' in dot


@pytest.mark.parametrize("sql", ["SELECT * FROM t", "select a from b where c = 1"])
def test_minimal_queries(sql: str) -> None:
    model = build_model(parse_script(sql))
    assert len(model.tables) == 1
