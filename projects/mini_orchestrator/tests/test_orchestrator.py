"""Тесты мини-оркестратора: граф, параллелизм, retry, каскад ошибок."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from projects.mini_orchestrator.cli import build_demo_dag, main
from projects.mini_orchestrator.dag import DAG, DagError, Task
from projects.mini_orchestrator.executor import FAILED, SUCCESS, UPSTREAM_FAILED, Executor


def test_cycle_is_rejected() -> None:
    with pytest.raises(DagError, match="цикл"):
        DAG("bad", [Task("a", depends_on=("b",)), Task("b", depends_on=("a",))])


def test_unknown_dependency_is_rejected() -> None:
    with pytest.raises(DagError, match="неизвестные зависимости"):
        DAG("bad", [Task("a", depends_on=("nope",))])


def test_duplicate_task_is_rejected() -> None:
    with pytest.raises(DagError, match="дубликаты"):
        DAG("bad", [Task("a"), Task("a")])


def test_waves_group_independent_tasks() -> None:
    dag = build_demo_dag(flaky=False)
    assert dag.waves()[0] == ["extract"]
    assert dag.waves()[1] == ["transform_a", "transform_b"]
    assert dag.waves()[2] == ["load"]
    assert dag.waves()[3] == ["notify"]


def test_successful_run_passes_data_through_xcom() -> None:
    dag = build_demo_dag(flaky=False)
    with Executor(dag, max_workers=4) as executor:
        result = executor.run()
        assert result.status == SUCCESS
        assert all(state["status"] == SUCCESS for state in result.tasks.values())
        assert result.tasks["notify"]["output"]["message"] == "загружено строк: 100"


def test_parallel_tasks_run_concurrently() -> None:
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def make(name: str):
        def run(ctx):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1
            return name

        return run

    dag = DAG("parallel", [Task("a", make("a")), Task("b", make("b")), Task("c", make("c"))])
    with Executor(dag, max_workers=3) as executor:
        result = executor.run()
    assert result.status == SUCCESS
    assert active["max"] >= 2


def test_retry_recovers_transient_failure() -> None:
    dag = build_demo_dag(flaky=True)
    with Executor(dag, max_workers=2) as executor:
        result = executor.run()
    assert result.status == SUCCESS
    assert result.tasks["transform_b"]["attempts"] == 2


def test_failure_skips_downstream_only() -> None:
    dag = build_demo_dag(fail_load=True, flaky=False)
    with Executor(dag, max_workers=2) as executor:
        result = executor.run()
    assert result.tasks["load"]["status"] == FAILED
    assert result.tasks["notify"]["status"] == UPSTREAM_FAILED
    assert result.tasks["transform_a"]["status"] == SUCCESS
    assert result.status == FAILED


def test_timeout_is_recorded() -> None:
    dag = DAG(
        "slow",
        [
            Task("slow", lambda ctx: time.sleep(0.2) or "ok", timeout_seconds=0.05),
            Task("after", lambda ctx: "ok", depends_on=("slow",)),
        ],
    )
    with Executor(dag) as executor:
        result = executor.run()
    assert result.tasks["slow"]["status"] == FAILED
    assert "таймаут" in result.tasks["slow"]["error"]
    assert result.tasks["after"]["status"] == UPSTREAM_FAILED


def test_history_is_persisted(tmp_path: Path) -> None:
    dsn = str(tmp_path / "runs.sqlite3")
    dag = build_demo_dag(flaky=False)
    with Executor(dag, dsn=dsn) as executor:
        executor.run()
        executor.run()
    with Executor(dag, dsn=dsn) as executor:
        history = executor.history(limit=5)
    assert len(history) == 2
    assert history[0]["status"] == SUCCESS


def test_cli_demo_reports_failure(tmp_path: Path, capsys) -> None:
    code = main(["--dsn", str(tmp_path / "cli.sqlite3"), "demo", "--fail-load", "--stable"])
    output = capsys.readouterr().out
    assert code == 1
    assert "UPSTREAM_FAILED" in output
