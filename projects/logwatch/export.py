"""Экспорт метрик logwatch в формате Prometheus textfile."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from .rules import Alert, Rule, RuleEngine

HELP = {
    "logwatch_events_total": ("counter", "Сколько строк лога разобрано"),
    "logwatch_alerts_total": ("counter", "Сколько алертов выпущено по правилу"),
    "logwatch_suppressed_total": ("counter", "Сколько повторов подавлено дедупликацией"),
    "logwatch_open_alerts": ("gauge", "Алерты по severity в последнем разборе"),
}


def render(alerts: Iterable[Alert], rules: Iterable[Rule], counters: dict[str, int] | None = None) -> str:
    alerts = list(alerts)
    rules = list(rules)
    counters = counters or {}
    lines: list[str] = []
    for name, (metric_type, description) in HELP.items():
        lines.append(f"# HELP {name} {description}")
        lines.append(f"# TYPE {name} {metric_type}")

    lines.append(f'logwatch_events_total {counters.get("events", 0)}')
    lines.append(f'logwatch_suppressed_total {counters.get("suppressed", 0)}')

    per_rule = Counter((alert.rule_id, alert.severity) for alert in alerts)
    for rule in rules:
        key = (rule.rule_id, rule.severity)
        lines.append(
            f'logwatch_alerts_total{{rule="{rule.rule_id}",severity="{rule.severity}"}} {per_rule.get(key, 0)}'
        )
    per_severity = Counter(alert.severity for alert in alerts)
    for severity in ("P1", "P2", "P3"):
        lines.append(f'logwatch_open_alerts{{severity="{severity}"}} {per_severity.get(severity, 0)}')
    return "\n".join(lines) + "\n"


def counters_snapshot(engine: RuleEngine) -> dict[str, int]:
    return dict(engine.counters)
