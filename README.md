# HackAlem AI — «Граф денег» (TayMas Solutions)

Пайплайн для AML-аналитика: по графу внутрибанковских переводов от 81 seed-клиента он присваивает каждому из 2 248 узлов объяснимую роль, кластер и приоритет проверки. Выводы в выгрузках — это гипотезы для проверки, а не утверждения о виновности.

## Первый запуск

Нужен Python 3.11 (3.9+ тоже подойдёт). Команды выполняются из корня репозитория:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m money_graph --data data --out out
```

Полный прогон занимает несколько секунд. В `out/` появятся `nodes_roles.csv`, `clusters.csv` и `top_nodes.csv`. Перед записью пайплайн механически проверяет схему, а повторный запуск даёт побайтно одинаковые файлы.

Оригинальный стартовый код организаторов сохранён без изменений в `starter/` (`python starter/starter.py --data ./data --out ./out`). Роли, кластеры и приоритеты он оставляет пустыми.

## Данные

В репозиторий данные не коммитятся: архив организаторов нужно распаковать в `./data`. Там должны лежать:

- `nodes.parquet` — 2 248 уникальных узлов;
- `edges.parquet` — 3 119 агрегированных направленных рёбер;
- `transactions.parquet` — 4 840 отдельных транзакций.

Схема полей описана в [docs/DATA_README.md](docs/DATA_README.md) и `starter/README.md`.

## Устройство

```
money_graph/          основной пайплайн: python -m money_graph
  config.py           пороги признаков и ролей с обоснованием
  io.py               загрузка parquet и sanity-check (из starter)
  graph.py            направленный взвешенный nx.DiGraph, включая узлы без рёбер
  features.py         признаки узлов
  roles.py            правила ролей, role_score, evidence
  ranking.py          кластеры (через analytics.clustering) и приоритет
  outputs.py          схема выгрузок и её проверка
  cli.py              точка входа
analytics/
  clustering.py       кластеризация и clusters.csv (PAN-35), см. docs/clustering.md
tests/                python -m pytest tests
starter/              исходный стартовый код организаторов
```

## Признаки узла

| Группа | Колонки | Зачем |
|---|---|---|
| Базовые (starter) | `in_deg, out_deg, in_kzt, out_kzt, in_tx, out_tx, pagerank, pass_through` | потоки: сумма и число переводов считаются отдельно |
| Контрагенты | `n_payers, n_receivers, n_seed_payers, n_seed_receivers, top_payer_share, top_receiver_share, avg_in_tx_kzt, avg_out_tx_kzt` | сколько уникальных сторон, сколько из них seed, насколько поток сосредоточен на одном контрагенте, средний чек |
| Центральность (направленный граф) | `betweenness, hub_score, authority_score, downstream_reach, n_seed_upstream, wcc_id, wcc_size` | через кого идут деньги, кто рассылает, кто собирает, сколько узлов ниже по потоку |
| Граница обхода | `boundary, out_observable, in_underestimated, truncated_by_depth, external_inflow_suspected, pass_through_reliable` | какие потоки узла вообще видны в выгрузке |
| Время | `fast_out_share, median_lag_days, sync_payers_max, max_tx_per_day, active_days` | сквозной транзит за ≤2 дня, синхронные поступления, всплески |
| Возвратные потоки | `n_cycles, min_cycle_len` | простые циклы длиной до 5 (всего 468) |

### Как учтены ловушки данных

1. **Обрыв на 4-м колене.** Рёбра 4-го колена выходят из узлов depth=3. Поэтому у узлов depth≤3 исходящие выгружены полностью и `out_deg = 0` для них — наблюдаемый факт. У узлов depth=4 исходящие не выгружались вовсе (`boundary = out_hidden`). Правило `terminal` требует `out_observable`, так что ни один из 444 узлов depth=4 не получает роль `terminal`. Такой узел получает роль по входящему профилю: 3 узла стали `consolidator`, 441 — `peripheral` с пометкой в evidence «исходящие не наблюдаемы … нужен запрос выписки».
2. **Занижен вход у seed.** `pass_through_reliable` считается только для не-seed с наблюдаемым входом и выходом. Правила `transit` T1/T2 и `terminal` по доле пропуска на seed не применяются. Seed с заметным оттоком получает `transit` по правилу T3 с пониженной уверенностью, и evidence прямо говорит: «вход извне не виден».
3. **Сумма и число переводов.** В правилах и evidence присутствуют обе величины: `in_kzt` и `in_tx`, средний чек, число переводов в день.
4. **Направленность.** Все центральности и циклы считаются на направленном графе.
5. **Узлы без рёбер.** 19 seed без переводов попадают в выгрузку как `peripheral` (правило `isolated`).

## Роли: правила и пороги

Правила проверяются сверху вниз, срабатывает первое подходящее. Код правила записывается в колонку `role_rule`. Пороги лежат в [money_graph/config.py](money_graph/config.py) и выбраны по распределению: `in_deg` p95 = 3, p99 = 6; `out_deg` p97 = 9, p98 = 14.

| Правило | Роль | Условие | Узлов |
|---|---|---|---|
| `isolated` | peripheral | нет ни одного ребра | 19 |
| `C1` | coordinator | ≥5 плательщиков **и** ≥10 получателей: сбор и веерная рассылка | 15 |
| `C2` | coordinator | ≥3 плательщика, ≥10 получателей и цикл возврата длиной ≥3 (деньги возвращаются через посредников) | 1 |
| `D1` | distributor | ≥10 получателей | 48 |
| `K1` | consolidator | ≥3 плательщика **или** ≥2 плательщика-seed | 172 |
| `T1` | transit | не seed, `pass_through` 0.8–1.2 | 56 |
| `T2` | transit | не seed, `pass_through` 0.5–2.0 и ≥80% оттока ушло не позже 2 дней после поступления | 28 |
| `T3` | transit | seed с оттоком ≥50 тыс. KZT (вход не наблюдаем, роль определяется по оттоку) | 26 |
| `E1` | terminal | depth<4 (отток наблюдаем), удержано ≥80%, и при этом ≥100 тыс. KZT, или ≥2 плательщика, или ≥3 перевода | 403 |
| `P-trunc` | peripheral | depth=4, признаков сбора нет, отток не наблюдаем | 441 |
| `P-ext` | peripheral | отдаёт больше чем в 1.2 раза от полученного в графе: вероятен источник вне выгрузки | 207 |
| `P` | peripheral | ниже порогов всех ролей | 832 |

**`role_score`** (0–1) — уверенность в роли. Базовая часть начисляется за срабатывание правила, добавки — за силу сигнала относительно порога: число контрагентов, суммы, доля быстрого оттока, циклы. Формулы находятся в `roles.role_scores`. У обрезанного узла уверенность в `peripheral` тем ниже, чем больше в него пришло.

**`evidence`** — до 200 символов, всегда с числами. Пример: `получает 919 тыс. KZT от 9 плательщ. (3 seed) за 18 перев., до 2 плательщ. в один день; отдаёт дальше 8% — признаки консолидации`.

## Кластеры

`cluster_id` и `clusters.csv` строит [analytics/clustering.py](analytics/clustering.py): слабосвязные компоненты, внутри крупных — Louvain на неориентированной проекции с оговоркой про направление, плюс оценка устойчивости. Метод, колонки и правила гипотез описаны в [docs/clustering.md](docs/clustering.md). `networkx` закреплён на 3.6.1, потому что от версии зависит разбиение Louvain.

## Выход: `nodes_roles.csv`

Обязательные колонки по ТЗ: `gid, role, role_score, cluster_id, priority_score, evidence`. За ними идут `role_rule` и все признаки из таблицы выше, чтобы роль любого gid можно было проверить прямо по строке.

> `priority_score` (прозрачная взвешенная сумма в [money_graph/ranking.py](money_graph/ranking.py)) и `top_nodes.csv` — временные. Их заменит этап «Приоритет и топ-лист», интерфейс `ranking.py` при этом сохранится.

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

Тесты: `python -m pytest tests`. Если `./data` не распакована, тесты пропускаются.
