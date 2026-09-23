# Локальный Docker-запуск

Docker-режим воспроизводит локальный запуск ТЗ без облака и без API-ключей.
Python-пайплайн выполняется в одноразовом контейнере, а React/Vite build
отдаётся nginx на `http://127.0.0.1:8501`.

## Что нужно подготовить

Docker Desktop с Compose v2 или Docker Engine с `docker compose`, а также
распакованный датасет организаторов в `data/`:

```text
data/
  nodes.parquet
  edges.parquet
  transactions.parquet
```

`data/` и `out/` намеренно не входят в Docker build context и не попадают в
образы. `data/` монтируется только для чтения; `out/` монтируется для записи
пайплайном и только для чтения nginx и Copilot. Для локальных ответов NVIDIA
не нужна. При явной настройке `NVIDIA_API_KEY` / `NVIDIA_MODEL` они передаются
только сервису Copilot во время запуска, не включаются в image или frontend.

## Запуск одной командой

Из корня репозитория:

```bash
mkdir -p out
docker compose up --build
```

После успешного завершения `pipeline` Compose запускает `ui` и `copilot`. Откройте
<http://127.0.0.1:8501>. Остановка:

```bash
docker compose down
```

Помощник доступен во вкладке справа от графа. Его API не публикуется отдельным
портом: `/api/copilot/*` проксирует nginx. Остановка Copilot не останавливает UI.
[Контракт панели и демо на 90 секунд](copilot_panel.md).

Если нужен только пересчёт CSV:

```bash
docker compose run --rm pipeline
```

Результаты будут в `out/nodes_roles.csv`, `out/clusters.csv`,
`out/top_nodes.csv` и `out/edge_table.csv`. Последний файл нужен интерфейсу
для направленных связей и сумм потоков. Команда pipeline запускается с
`--check-repro` и завершится с ошибкой, если второй прогон даст другие sha256.

## Smoke-проверка

Скрипт проверяет входные parquet, наличие всех четырёх CSV, health endpoint
nginx и доступность `edge_table.csv` из браузерного контейнера:

```bash
./scripts/docker-smoke.sh
```

Внутри образа зафиксированы Python 3.11 и `networkx==3.6.1` из
`requirements.txt`. Это важно для одинаковой кластеризации Louvain и
детерминированных выгрузок. Для ручной проверки хешей:

```bash
sha256sum out/*.csv
docker compose run --rm pipeline
sha256sum out/*.csv
```

После второго запуска хеши должны совпасть. `run_report.md` и
`run_report.json` также сохраняются в `out/`.

## Состав образов и ограничения

`Dockerfile` содержит отдельные targets:

- `pipeline`: `python:3.11-slim`, код аналитики и зависимости;
- `ui`: nginx с собранным React frontend из node build stage.

В Docker image не копируются обезличенный датасет, generated CSV, `.env`,
node_modules и git-метаданные. UI использует только локальные файлы в `out/`;
внешний интернет и NVIDIA API не требуются.

Для production deployment нужно заменить bind mounts на управляемые volumes,
ограничить сетевой доступ к nginx и добавить TLS перед reverse proxy. Текущий
режим рассчитан на локальную демонстрацию и проверку жюри.
