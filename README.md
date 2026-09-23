# HackAlem AI — «Граф денег»

В репозитории находятся обезличенные данные кейса и стартовый код для анализа транзакционной сети.

## Первый запуск

Требуется Python 3.9 или новее.

Из корня репозитория выполните:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r starter/requirements.txt
python starter/starter.py --data ./data --out ./out
```

После запуска в папке `out/` появятся:

```text
nodes_roles.csv
clusters.csv
top_nodes.csv
```

Стартовый код уже загружает parquet-файлы, проверяет их согласованность, строит направленный взвешенный граф и считает базовые метрики. Роли, кластеры и приоритеты в стартовой версии оставлены пустыми — это часть реализации MVP.

## Данные

Папка `data/` содержит:

- `nodes.parquet` — 2 248 уникальных узлов;
- `edges.parquet` — 3 119 агрегированных направленных рёбер;
- `transactions.parquet` — 4 840 отдельных транзакций.

Полное описание схемы находится в `starter/README.md` и в README датасета, предоставленном организаторами.

## Быстрая проверка выходных файлов

```bash
python3 - <<'PY'
import pandas as pd

for name in ["nodes_roles", "clusters", "top_nodes"]:
    df = pd.read_csv(f"out/{name}.csv")
    print(f"{name}: {len(df)} строк")
    print(df.head(3).to_string(index=False))
PY
```

Для выхода из виртуального окружения:

```bash
deactivate
```
