"""DAG-планировщик: задачи, зависимости, топологический порядок."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class DagError(ValueError):
    """Некорректный граф: цикл, неизвестная зависимость, дубликат."""


@dataclass
class Task:
    name: str
    func: Callable[[Any], Any] | None = None
    depends_on: tuple[str, ...] = ()
    retries: int = 0
    retry_delay: float = 0.0
    timeout_seconds: float | None = None
    description: str = ""


@dataclass
class DAG:
    name: str
    tasks: list[Task] = field(default_factory=list)
    description: str = ""

    def __post_init__(self) -> None:
        names = [task.name for task in self.tasks]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise DagError(f"дубликаты задач: {sorted(duplicates)}")
        known = set(names)
        for task in self.tasks:
            unknown = set(task.depends_on) - known
            if unknown:
                raise DagError(f"{task.name}: неизвестные зависимости {sorted(unknown)}")
            if task.name in task.depends_on:
                raise DagError(f"{task.name}: задача не может зависеть от себя")
        self.topological_order()

    # -- граф --------------------------------------------------------------
    def by_name(self, name: str) -> Task:
        for task in self.tasks:
            if task.name == name:
                return task
        raise DagError(f"нет задачи {name}")

    def children(self, name: str) -> list[str]:
        return [task.name for task in self.tasks if name in task.depends_on]

    def parents(self, name: str) -> list[str]:
        return list(self.by_name(name).depends_on)

    def topological_order(self) -> list[str]:
        incoming = {task.name: len(task.depends_on) for task in self.tasks}
        waves = self.waves()
        order = [name for wave in waves for name in wave]
        if len(order) != len(self.tasks):
            cyclic = sorted(set(incoming) - set(order))
            raise DagError(f"цикл в графе: {cyclic}")
        return order

    def waves(self) -> list[list[str]]:
        """Уровни параллелизма: задачи одного уровня не зависят друг от друга."""
        done: set[str] = set()
        result: list[list[str]] = []
        remaining = [task.name for task in self.tasks]
        while remaining:
            wave = [name for name in remaining if set(self.parents(name)) <= done]
            if not wave:
                raise DagError(f"цикл в графе: {sorted(remaining)}")
            result.append(sorted(wave))
            done |= set(wave)
            remaining = [name for name in remaining if name not in done]
        return result
