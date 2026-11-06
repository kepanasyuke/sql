# mini-orchestrator — DAG-планировщик на потоках

Мини-замена Airflow для учебных и небольших конвейеров: граф задач с
зависимостями, параллелизм по уровням, retry с задержкой, таймауты, передача
результатов между задачами и журнал прогонов в SQLite.

## Что умеет

- **валидация графа**: цикл, неизвестная зависимость, дубликат имени — ошибка
  на этапе создания DAG, а не в момент запуска;
- **уровни параллелизма** (`waves`): задачи без общих зависимостей идут
  одновременно в `ThreadPoolExecutor`, что даёт ускорение на I/O;
- **retry**: `Task(retries=1)` перезапускает транзиентно упавшую задачу,
  счётчик попыток попадает в журнал;
- **timeout**: задача, превысившая лимит, помечается `FAILED` (честное
  ограничение: поток в Python нельзя прервать, поэтому таймаут проверяется по
  факту завершения — для HTTP/БД-вызовов это работает, для CPU-loop нет);
- **каскад ошибок**: падение задачи переводит только её потомков в
  `UPSTREAM_FAILED`, независимые ветки продолжают работу;
- **xcom**: `ctx.output("extract")` — результат предыдущей задачи;
- **журнал**: `dag_run` и `task_run` в SQLite, `history()` для разбора.

## Запуск

```bash
python -m projects.mini_orchestrator.cli demo
python -m projects.mini_orchestrator.cli demo --fail-load      # каскад ошибок
python -m projects.mini_orchestrator.cli demo --json
python -m projects.mini_orchestrator.cli history --limit 5
```

```
граф mart_clients_daily: extract -> transform_a -> transform_b -> load -> notify
уровни параллелизма: extract | transform_a,transform_b | load | notify
  SUCCESS         extract      attempts=1
  SUCCESS         transform_a  attempts=1
  SUCCESS         transform_b  attempts=2      (retry после ConnectionError)
  SUCCESS         load         attempts=1
  SUCCESS         notify       attempts=1
итог: SUCCESS, 12 мс
```

## Чем отличается от «просто запустить функции по порядку»

Порядок в списке ничего не гарантирует: при добавлении задачи легко получить
скрытую зависимость от порядка объявления. Здесь порядок вычисляется из графа,
параллелизм — из уровней, а состояние каждой задачи фиксируется в БД, поэтому
упавший прогон можно разобрать по `task_run` без чтения логов.

## Тесты

```bash
python -m pytest projects/mini_orchestrator -q
```

Проверяется: отказ от циклов/неизвестных зависимостей/дубликатов, разбиение на
уровни, передача данных через xcom, реальная параллельность (две задачи
одновременно), retry транзиентной ошибки, каскад `UPSTREAM_FAILED`, таймаут,
сохранение истории между перезапусками и exit-код CLI.

**В резюме:** «mini-orchestrator — DAG-планировщик на потоках: топологический
порядок, параллелизм по уровням, retry/таймауты, xcom и журнал прогонов в SQLite».
