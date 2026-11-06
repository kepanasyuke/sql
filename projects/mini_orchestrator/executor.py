"""Исполнитель DAG: уровни параллелизма, retry, таймауты, журнал в sqlite."""

from __future__ import annotations

import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dag import DAG, Task

SUCCESS = "SUCCESS"
FAILED = "FAILED"
UPSTREAM_FAILED = "UPSTREAM_FAILED"

SCHEMA = """
CREATE TABLE IF NOT EXISTS dag_run (
    run_id     TEXT PRIMARY KEY,
    dag        TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status     TEXT NOT NULL,
    duration_ms INTEGER
);
CREATE TABLE IF NOT EXISTS task_run (
    run_id     TEXT NOT NULL,
    task       TEXT NOT NULL,
    status     TEXT NOT NULL,
    attempts   INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    error      TEXT,
    PRIMARY KEY (run_id, task)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Context:
    """То, что задача получает на вход: run_id и результаты предыдущих задач."""

    run_id: str
    xcom: dict[str, Any] = field(default_factory=dict)

    def output(self, task: str, default: Any = None) -> Any:
        return self.xcom.get(task, default)


@dataclass
class RunResult:
    run_id: str
    dag: str
    status: str
    duration_ms: int
    tasks: dict[str, dict]

    @property
    def failed(self) -> list[str]:
        return [name for name, state in self.tasks.items() if state["status"] != SUCCESS]

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "dag": self.dag,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "tasks": self.tasks,
        }


class Executor:
    def __init__(self, dag: DAG, dsn: str = ":memory:", max_workers: int = 4):
        self.dag = dag
        self.max_workers = max(1, max_workers)
        self.dsn = dsn
        if dsn != ":memory:":
            Path(dsn).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(dsn, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    # -- запуск ------------------------------------------------------------
    def run(self, run_id: str | None = None) -> RunResult:
        run_id = run_id or f"{self.dag.name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
        started = time.perf_counter()
        self._conn.execute(
            "INSERT INTO dag_run (run_id, dag, started_at, status) VALUES (?, ?, ?, 'RUNNING')",
            (run_id, self.dag.name, _now()),
        )
        self._conn.commit()

        context = Context(run_id=run_id)
        states: dict[str, dict] = {
            task.name: {"status": "PENDING", "attempts": 0, "error": None} for task in self.dag.tasks
        }

        for wave in self.dag.waves():
            runnable = [name for name in wave if self._upstream_ok(name, states)]
            for name in wave:
                if name not in runnable:
                    states[name]["status"] = UPSTREAM_FAILED
                    states[name]["error"] = "зависимость завершилась с ошибкой"
                    self._record(run_id, name, states[name], started_at=_now())
            if not runnable:
                continue
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(runnable))) as pool:
                futures = {pool.submit(self._execute_task, self.dag.by_name(name), context): name for name in runnable}
                for future, name in futures.items():
                    states[name] = future.result()

        status = SUCCESS if all(state["status"] == SUCCESS for state in states.values()) else FAILED
        duration = int((time.perf_counter() - started) * 1000)
        self._conn.execute(
            "UPDATE dag_run SET status = ?, finished_at = ?, duration_ms = ? WHERE run_id = ?",
            (status, _now(), duration, run_id),
        )
        self._conn.commit()
        return RunResult(run_id=run_id, dag=self.dag.name, status=status, duration_ms=duration, tasks=states)

    def _upstream_ok(self, name: str, states: dict[str, dict]) -> bool:
        return all(states[parent]["status"] == SUCCESS for parent in self.dag.parents(name))

    # -- одна задача -------------------------------------------------------
    def _execute_task(self, task: Task, context: Context) -> dict:
        started_at = _now()
        clock = time.perf_counter()
        attempts = 0
        error: str | None = None
        while attempts <= task.retries:
            attempts += 1
            try:
                output = task.func(context) if task.func else None
                elapsed = time.perf_counter() - clock
                if task.timeout_seconds is not None and elapsed > task.timeout_seconds:
                    raise TimeoutError(
                        f"задача превысила таймаут {task.timeout_seconds} c (фактически {elapsed:.2f} c)"
                    )
                with self._lock:
                    context.xcom[task.name] = output
                state = {
                    "status": SUCCESS,
                    "attempts": attempts,
                    "error": None,
                    "output": output,
                    "duration_ms": int(elapsed * 1000),
                }
                self._record(context.run_id, task.name, state, started_at)
                return state
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if attempts > task.retries:
                    break
                if task.retry_delay:
                    time.sleep(task.retry_delay)
        state = {
            "status": FAILED,
            "attempts": attempts,
            "error": error,
            "trace": traceback.format_exc(limit=2),
            "duration_ms": int((time.perf_counter() - clock) * 1000),
        }
        self._record(context.run_id, task.name, state, started_at)
        return state

    def _record(self, run_id: str, task: str, state: dict, started_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO task_run"
                " (run_id, task, status, attempts, started_at, finished_at, duration_ms, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    task,
                    state["status"],
                    state.get("attempts", 0),
                    started_at,
                    _now(),
                    state.get("duration_ms", 0),
                    state.get("error"),
                ),
            )
            self._conn.commit()

    # -- история -----------------------------------------------------------
    def history(self, limit: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM dag_run WHERE dag = ? ORDER BY started_at DESC LIMIT ?",
            (self.dag.name, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Executor":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
