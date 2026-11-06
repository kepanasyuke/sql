# sql — 10 pet-проектов по данным, SQL и инфраструктуре

## Простыми словами

Десять небольших инструментов под типовые задачи при работе с данными: понять,
откуда в отчёте взялась цифра; сверить, что после загрузки в приёмнике лежат те
же строки; загрузить данные так, чтобы повторный запуск ничего не сломал;
разобрать логи и поднять алерт; быстро посмотреть, что вообще в файле и нет ли
там персональных данных; сравнить два варианта SQL по скорости.

**Где применяется** — в командах, которые сопровождают хранилище и отчётность:
аналитика и DWH, дежурство по данным, подготовка релизов витрин, разбор
инцидентов «цифры не сходятся».

**Как это используется** — каждый проект запускается отдельной командой, ставить
ничего не нужно:

```bash
python -m projects.recon_kit.cli run --source a.csv --target b.csv --key id
python -m projects.logwatch.cli analyze --demo
python -m projects.perf_lab.cli bench --rows 20000 --repeats 5
```

## Что внутри

Каждый проект в этом репозитории — рабочий инструмент, а не заготовка: есть
CLI, тесты и README с разбором решений. Внешних зависимостей нет вообще (только
стандартная библиотека Python 3.10+), поэтому любой проект запускается одной
командой на чистой машине.

```bash
python -m pytest -q          # 94 теста по всем проектам
```

## Проекты

| # | Проект | Что делает | Ключевая фича | Стек |
| --- | --- | --- | --- | --- |
| 1 | [sql-lineage](projects/sql_lineage/README.md) | строит lineage по SQL-скриптам и отвечает «что сломается, если таблица не пришла» | свой токенизатор + колоночный lineage через CTE; экспорт JSON/DOT | Python, SQL |
| 2 | [recon-kit](projects/recon_kit/README.md) | сверка источника и приёмника после загрузки | checksum-бакеты для быстрого ответа «идентично» + потоковое сравнение | Python, CSV, SQL |
| 3 | [etl-watermark](projects/etl_watermark/README.md) | идемпотентная инкрементальная загрузка | upsert по бизнес-ключу, водяной знак, журнал прогонов, retry, backfill | Python, SQLite |
| 4 | [bank-metrics-api](projects/bank_metrics_api/README.md) | JSON API по оборотам, портфелю и статусу DQ | пагинация, фильтры, ETag/304, валидация параметров, JSON access-log | Python (stdlib HTTP), SQLite |
| 5 | [logwatch](projects/logwatch/README.md) | разбор логов и алертинг | правила с окном и дедупликацией (alert fatigue), Prometheus-экспорт | Python, nginx/JSON-логи |
| 6 | [csv-profiler](projects/csv_profiler/README.md) | профилирование данных и поиск PII | типы, NULL, квантили, ИНН/Луна/телефон, автогенерация DQ-проверок | Python, CSV |
| 7 | [mini-orchestrator](projects/mini_orchestrator/README.md) | DAG-планировщик на потоках | уровни параллелизма, retry/таймауты, xcom, каскад `UPSTREAM_FAILED` | Python, SQLite |
| 8 | [stream-replay](projects/stream_replay/README.md) | потоковая обработка событий | tumbling-окна по event time, водяной знак, дедупликация at-least-once | Python |
| 9 | [sql-migrations](projects/sql_migrations/README.md) | миграции схемы | атомарное применение, контроль дрейфа по SHA-256, up/down, блокировка | Python, SQL |
| 10 | [perf-lab](projects/perf_lab/README.md) | бенчмарк SQL-вариантов | эквивалентность результатов как гейт, p50/p95, вклад индексов | Python, SQL |

## Быстрый прогон

```bash
python -m projects.sql_lineage.cli dot sql/demo.sql          # нужен файл со скриптами
python -m projects.recon_kit.cli run --source a.csv --target b.csv --key id
python -m projects.etl_watermark.cli demo
python -m projects.bank_metrics_api.cli serve --port 8765 --self-test
python -m projects.logwatch.cli analyze --demo --prom artifacts/metrics/logwatch.prom
python -m projects.csv_profiler.cli profile --demo
python -m projects.mini_orchestrator.cli demo
python -m projects.stream_replay.cli demo
python -m projects.sql_migrations.cli demo
python -m projects.perf_lab.cli bench --rows 20000 --repeats 5
```

## Общие принципы

1. **Никаких фреймворков ради галочки.** Всё, что обычно приходит с зависимостью
   (HTTP-сервер, планировщик, мигратор, парсер), написано на stdlib — зато видно,
   как это устроено внутри.
2. **Тест проверяет поведение, а не строки.** Проверяются инварианты:
   идемпотентность, эквивалентность результатов, каскад ошибок, откат транзакции.
3. **Отчёт вместо «оно работает».** Каждый инструмент оставляет артефакт:
   markdown-отчёт, JSON, Prometheus-метрики, журнал в БД.
4. **Ошибки видны.** Расхождение результатов, дрейф миграции, потерянные события
   и опоздавшие данные не проглатываются, а попадают в отчёт и exit-код.

## Структура

```
projects/<project>/
├── <модуль>.py        логика
├── cli.py             командная строка
├── README.md          что умеет, как запустить, какие решения приняты
└── tests/             pytest: поведение и граничные случаи
```

## Лицензия

MIT.
