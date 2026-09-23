# Money Graph UI

React + TypeScript + Vite + Cytoscape.js интерфейс для локальных выгрузок
Python-пайплайна. Браузер читает `../out/nodes_roles.csv`,
`clusters.csv`, `top_nodes.csv` и `edge_table.csv`; для просмотра графа API не
требуется. Панель помощника использует локальный Python API; NVIDIA опциональна.
`gid`, `src` и `dst` читаются как строки, чтобы не потерять
18-значные идентификаторы в JavaScript.

## Первый запуск

Из корня репозитория:

```bash
python -m money_graph --data data --out out
cd frontend
npm ci
npm run dev
```

Откройте URL, который напечатает Vite. Во время разработки Vite проксирует
`/out/*` из соседней папки `out/`. Если данных ещё нет, нажмите «Открыть
демо-набор» на экране ошибки, чтобы увидеть небольшой синтетический fixture.

## Проверки

```bash
npm test
npm run build
npm run preview
```

Production build включает доступные на момент сборки файлы `out/*.csv` в
`dist/out/`. Папка `out/` с обезличенными данными остаётся локальной и не
коммитится в Git.

## Помощник по расследованию

В отдельном терминале из корня проекта запустите `python -m agent_orchestrator.server --out out`.
Vite и preview проксируют `/api/copilot` в этот процесс. Откройте вкладку «Помощник»
рядом с графом: три подсказки, контекст до пяти gid, проверяемые факты и следующие шаги.
Ключ для локальных ответов не нужен. Docker Compose запускает сервис автоматически.

[Контракт, проверки и демо на 90 секунд](../docs/copilot_panel.md).
