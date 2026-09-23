# Money Graph UI

React + TypeScript + Vite + Cytoscape.js интерфейс для локальных выгрузок
Python-пайплайна. Браузер читает `../out/nodes_roles.csv`,
`clusters.csv`, `top_nodes.csv` и `edge_table.csv`; API или внешний сервис не
требуются. `gid`, `src` и `dst` читаются как строки, чтобы не потерять
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
