# Инструменты графа для AML-агента (PAN-45)

> Проверка ответов агента и контрольные вопросы: [docs/agent_verifier.md](agent_verifier.md).

`agent_tools/` — read-only слой запросов к результатам пайплайна. Агент (PAN-46) вызывает эти функции вместо того, чтобы считать сам. LLM никогда не вычисляет роли, скоры и суммы: она только выбирает инструмент и пересказывает его ответ.

Гарантии:
- **без LLM и сети** — работает только по `out/nodes_roles.csv`, `edge_table.csv`, `clusters.csv`, `top_nodes.csv`;
- **ничего не пишет** — файлы только читаются (это проверяет тест по sha256);
- **детерминированно** — списки отсортированы по score или сумме, при равенстве — по gid;
- **числа из CSV без пересчёта** — итоги `get_incoming` / `get_outgoing` совпадают с `in_kzt` / `out_kzt` в `nodes_roles.csv`;
- **каждый факт со ссылкой** — поле `sources`: файл, gid или ребро, колонки (по ним сверяет verifier PAN-47);
- **границы выгрузки явно** — поле `warnings` (4-е колено, заниженный вход seed, источник вне выгрузки);
- **ограниченный размер** — у списков `limit` (по умолчанию 20–100, максимум 200) и флаг `truncated`.

## Вызов из Python

```python
from agent_tools import GraphStore, GraphTools, call_tool

tools = GraphTools(GraphStore.from_dir("out"))        # после python -m money_graph --data data --out out

tools.get_node(100000003684369100)                   # прямой вызов: исключение при ошибке
tools.find_common_collectors(["100000003684369100", "100000008603629100", "100000006866783100"])

call_tool(tools, "get_incoming", {"gid": "100000003684369100", "limit": 5})
# → {"ok": True, "tool": "get_incoming", "result": {...}}
call_tool(tools, "get_node", {"gid": "123"})
# → {"ok": False, "tool": "get_node", "error": {"type": "UnknownGidError", "code": "unknown_gid",
#                                               "message": "gid 123 нет в nodes_roles.csv: ...", "gid": 123}}
```

`call_tool` — единая точка входа для оркестратора. Она проверяет allow-list инструментов, сверяет аргументы со схемой (лишние поля, типы, диапазоны) и возвращает типизированную ошибку вместо исключения.

`tool_specs()` отдаёт описания инструментов в формате OpenAI-совместимого tool calling (`{"type": "function", "function": {name, description, parameters}}`), который принимает NVIDIA API.

Из консоли:

```bash
python -m agent_tools --list                                              # инструменты и схемы параметров
python -m agent_tools get_node '{"gid": "100000003684369100"}'
python -m agent_tools find_common_collectors '{"gids": ["100000003684369100", "100000008603629100"]}'
```

## Инструменты

| Инструмент | Параметры | Что возвращает | Сортировка |
|---|---|---|---|
| `get_node` | `gid` | карточка: роль, role_score, priority_score, кластер, потоки, evidence, `priority_why`, вклад компонент, место в топе | — |
| `get_incoming` | `gid, limit=50` | рёбра «кто платил» с карточкой контрагента, итоги `n_edges / sum_kzt / n_tx` | сумма ↓, gid ↑ |
| `get_outgoing` | `gid, limit=50` | рёбра «кому платил», итоги | сумма ↓, gid ↑ |
| `trace_upstream` | `gids, depth=4, limit=100` | предки с расстоянием, рёбра пути, достигнутые seed | шаг ↑, priority ↓, gid ↑ |
| `trace_downstream` | `gid, depth=4, limit=100` | потомки с расстоянием, рёбра пути, узлы на границе обхода | шаг ↑, priority ↓, gid ↑ |
| `find_common_collectors` | `gids (≥2), depth=2, min_sources=2, limit=20` | узлы, до которых доходят деньги ≥ min_sources заданных gid; по каждому источнику — число шагов и прямая сумма | число источников ↓, прямая сумма ↓, priority ↓, gid ↑ |
| `rank_candidates` | `filters, limit=20` | узлы по priority_score с evidence и `priority_why` | priority ↓, gid ↑ |
| `get_cluster` | `cluster_id, limit=10` | строка clusters.csv, распределение ролей, главные участники | priority ↓, gid ↑ |
| `compare_nodes` | `gids (2–10)` | метрики узлов, прямые переводы между ними, общие плательщики и получатели | priority ↓, gid ↑ |

Фильтры `rank_candidates`: `role`, `cluster_id`, `is_seed`, `depth`, `boundary`, `min_priority`, `min_in_kzt`, `min_out_kzt`, `min_payers`, `min_receivers`, `exclude_gids`. Неизвестный фильтр — ошибка `invalid_argument`, а не молчаливое игнорирование.

**gid передаётся целым числом или строкой цифр.** float отклоняется: 18-значный gid в float64 теряет последние цифры (`1.0000000368436911e17` — уже другой клиент). Для LLM надёжнее строка.

**Суммы через посредников не складываются.** В `find_common_collectors` для каждого источника показана только прямая сумма перевода сборщику (`direct_sum_kzt`). Если деньги дошли за 2 шага, `direct_sum_kzt = null`: по пути они смешались с чужими, и утверждать «от A к K дошло X тенге» данные не позволяют.

## Формат ответа

Пример `get_incoming` (укорочен: 1 ребро, 2 источника):

```json
{
  "ok": true,
  "tool": "get_incoming",
  "result": {
    "gid": 100000003684369100,
    "direction": "in",
    "node": {"gid": 100000003684369100, "role": "coordinator", "role_score": 0.961, "priority_score": 0.697,
             "cluster_id": 5, "depth": 0, "is_seed": true, "boundary": "in_hidden",
             "in_kzt": 3848436.0, "out_kzt": 8588655.0, "in_tx": 58, "out_tx": 67, "n_payers": 24, "n_receivers": 62},
    "totals": {"n_edges": 24, "sum_kzt": 3848436.0, "n_tx": 58},
    "edges": [{"src": 100000008748914100, "dst": 100000003684369100, "sum_kzt": 1582700.0, "n_tx": 9,
               "first_date": "2026-07-23", "last_date": "2026-07-28", "share_of_dst_in": 0.411258,
               "counterparty": {"gid": 100000008748914100, "role": "distributor", "priority_score": 0.4013, "cluster_id": 5}}],
    "total_count": 24,
    "truncated": true,
    "warnings": [
      {"code": "seed_inflow_underestimated", "gid": 100000003684369100, "message": "seed: входящие извне выборки не видны, in_kzt занижен"},
      {"code": "inflow_sample_only", "gid": 100000003684369100, "message": "видны только переводы от клиентов выборки: реальных плательщиков может быть больше"}
    ],
    "sources": [
      {"file": "nodes_roles.csv", "gid": 100000003684369100, "columns": ["in_deg", "in_kzt", "in_tx"]},
      {"file": "edge_table.csv", "src": 100000008748914100, "dst": 100000003684369100, "columns": ["sum_kzt", "n_tx"]}
    ]
  }
}
```

JSON-схемы параметров и результатов всех инструментов лежат в [agent_tools/schemas.py](../agent_tools/schemas.py): `PARAMETERS` (вход, отдаётся модели) и `RESULTS` (выход). `agent_tools.validate(value, schema)` проверяет их без внешних зависимостей, и тесты прогоняют через неё ответ каждого инструмента.

### Предупреждения (`warnings[].code`)

| Код | Когда | Что обязан сказать агент |
|---|---|---|
| `depth4_outflow_unobserved` | узел на 4-м колене | исходящие не выгружались; это не terminal |
| `depth4_frontier` | обход вниз дошёл до 4-го колена | цепочка может продолжаться за пределами данных |
| `seed_inflow_underestimated` | узел — seed | входящие извне не видны, `in_kzt` занижен |
| `external_inflow` | отдал больше 1.2 × видимого входа | вероятен источник денег вне выгрузки |
| `inflow_sample_only` | `get_incoming` | видны только плательщики из выборки |
| `upstream_partial` | `trace_upstream` | цепочка вверх может продолжаться за пределами данных |
| `depth4_members` | в кластере есть узлы 4-го колена | их исходящие не выгружались |
| `no_transfers` | у узла нет рёбер | связей в выгрузке нет |

### Ошибки (`error.code`)

| Код | Тип | Когда |
|---|---|---|
| `unknown_gid` | `UnknownGidError` | gid нет в `nodes_roles.csv` |
| `unknown_cluster` | `UnknownClusterError` | `cluster_id` нет в `clusters.csv` |
| `invalid_argument` | `InvalidArgumentError` | лишний или неверный аргумент, float-gid, depth вне 1–4, неизвестный фильтр |
| `tool_not_allowed` | `ToolNotAllowedError` | инструмента нет в allow-list |
| `outputs_not_found` | `OutputsNotFoundError` | в папке нет выгрузок (сначала запустите пайплайн) |

## Проверка

```bash
python -m pytest -q tests/test_agent_tools.py
```

19 тестов на синтетическом графе с заранее известными ответами (выгрузки строит настоящий пайплайн):
- прямые связи: карточка и итоги совпадают с CSV, сортировка по сумме, агрегирование переводов в ребро;
- общий сборщик: напрямую, через посредников, пустой результат, минимум два gid;
- обходы: вниз до границы 4-го колена, ограничение глубины, вверх до seed, изолированный seed;
- рейтинг, кластер, сравнение узлов;
- контракт: ответы всех инструментов проходят JSON-схемы и сериализуются, allow-list, проверка аргументов;
- детерминизм и read-only (sha256 CSV до и после).

На реальном датасете тест сверяет с CSV карточку, входящие и исходящие итоги для 5 первых узлов `top_nodes.csv`.
