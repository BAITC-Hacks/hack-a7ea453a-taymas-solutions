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
./scripts/docker-compose.sh up --build
```

После успешного завершения `pipeline` Compose запускает `ui` и `copilot`. Откройте
<http://127.0.0.1:8501>. Остановка:

```bash
./scripts/docker-compose.sh down
```

Помощник доступен во вкладке справа от графа. Его API не публикуется отдельным
портом: `/api/copilot/*` проксирует nginx. Остановка Copilot не останавливает UI.
[Контракт панели и демо на 90 секунд](copilot_panel.md).

Если нужен только пересчёт CSV:

```bash
./scripts/docker-compose.sh run --rm pipeline
```

Результаты будут в `out/nodes_roles.csv`, `out/clusters.csv`,
`out/top_nodes.csv` и `out/edge_table.csv`. Последний файл нужен интерфейсу
для направленных связей и сумм потоков. Команда pipeline запускается с
`--check-repro` и завершится с ошибкой, если второй прогон даст другие sha256.

## Права каталогов и порты

`scripts/docker-compose.sh` создаёт `out/` от имени текущего пользователя и
передаёт его UID/GID в `pipeline` и `copilot` через `LOCAL_UID` / `LOCAL_GID`.
На обычном Linux-хосте пользователь UID 1000 сможет записать результаты в
свой каталог с mode 0755. `chown` в Dockerfile действует только на слой image
и не меняет владельца примонтированного каталога хоста. Запускайте скрипт от
обычного пользователя, имеющего доступ к Docker; `chmod 777` не требуется.

Для прямого использования Compose сначала подготовьте каталог и окружение:

```bash
mkdir -p out
export LOCAL_UID=$(id -u) LOCAL_GID=$(id -g)
docker compose up --build -d
```

Без этих переменных прямой Compose использует UID/GID 10001, поэтому владелец
существующего `out/` должен дать этому пользователю право записи. Скрипт не
меняет права чужих файлов; при старых root-owned результатах используйте новый
каталог через `OUT_DIR`. `DATA_DIR` задаёт каталог входа (read-only).

```bash
UI_PORT=8508 OUT_DIR=./out_review ./scripts/docker-compose.sh up --build -d
```

Copilot имеет собственный healthcheck `/api/copilot/status`. UI запускается
после pipeline независимо от состояния помощника. Порт API остаётся внутренним.

## Smoke-проверка

Скрипт собирает отдельный Compose-проект, использует временный каталог вывода
и свободный локальный порт. Он проверяет четыре CSV и воспроизводимость,
права записи pipeline, `/healthz`, статус Copilot и реальный вопрос с
`use_nvidia=true` при пустом ключе: ответ должен быть `fallback/no_api_key`,
с проверенными claims и источниками. Затем останавливает только свой Copilot
и проверяет доступность UI и побайтное совпадение всех CSV через HTTP.
После проверки временные контейнеры/каталог удаляются; работающие демо других
Compose-проектов и обычный `out/` не затрагиваются.

```bash
./scripts/docker-smoke.sh
# Явно проверить нестандартный порт и ждать готовности до 90 секунд:
UI_PORT=18566 SMOKE_TIMEOUT=90 ./scripts/docker-smoke.sh
```

Нужны Docker Compose, curl и стандартные Unix-утилиты; JSON проверяется Python
внутри образа. `NVIDIA_API_KEY` и `NVIDIA_MODEL` принудительно пусты только в
процессе smoke. Фактический адрес берётся из `docker compose port`, ожидание
готовности и отдельные HTTP-запросы ограничены по времени.

Для проверки интерфейса настоящим Chromium дополнительно установите frontend
dev-зависимости и браузер (`cd frontend && npm ci && npx playwright install chromium`).
Затем из корня:

```bash
SMOKE_BROWSER=1 UI_PORT=18566 ./scripts/docker-smoke.sh
```

Он проверит показ локального ответа, сообщение «Нет связи с помощником» после
остановки API, ошибку повторного вопроса и работу графа/фильтра в этом состоянии.

Проверка PAN-66 от 23.09.2026: macOS arm64, Docker Desktop 4.88.1,
Engine 29.7.2 (Linux arm64), Compose 5.4.0. Команда
`SMOKE_BROWSER=1 UI_PORT=18566 ./scripts/docker-smoke.sh` прошла полностью,
включая Chromium online/offline. На отдельном Linux-volume с владельцем
1000:1000 и mode 0755 UID 10001 не имел права записи; запуск pipeline от
1000:1000 создал четыре CSV, прошёл 27 проверок и повторяемость (2.82 с
первый расчёт). Это проверка Linux-прав внутри VM Docker Desktop;
чистый физический Linux-хост и Windows отдельно не проверялись.

Внутри образа зафиксированы Python 3.11 и `networkx==3.6.1` из
`requirements.txt`. Это важно для одинаковой кластеризации Louvain и
детерминированных выгрузок. Для ручной проверки хешей:

```bash
sha256sum out/*.csv
./scripts/docker-compose.sh run --rm pipeline
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
