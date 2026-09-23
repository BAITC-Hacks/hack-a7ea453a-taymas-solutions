# CI для параллельной интеграции (PAN-69)

[Workflow](../.github/workflows/ci.yml) запускается на PR в `main`, push в `main`
и вручную через Actions → CI → Run workflow. На PR проверяется merge commit с
актуальным `main`. Новая версия той же ветки отменяет устаревший прогон.

Два независимых задания:

| Check | Проверки |
|---|---|
| Python tests | Python 3.11, networkx 3.6.1, весь `pytest tests`, включая ошибки провайдера и локальный fallback |
| Frontend + local Copilot | Node.js 22, `npm ci`, синтетический Parquet → настоящий pipeline с `--check-repro`, 5 evaluation-кейсов, frontend unit tests, production build и все существующие Chromium E2E |
| CI gate | Успех только если оба задания завершились `success`; ошибка, отмена или пропуск не считаются успехом |

Workflow публикует результат проверки, но сам не включает защиту ветки и не
мержит PR. Чтобы GitHub запрещал слияние красного PR, владелец репозитория может
выбрать **CI gate** как required status check в ruleset для `main`.

## Данные и границы проверки

Архив организаторов не отправляется в GitHub Actions. `scripts/ci_fixture.py`
использует существующий синтетический граф из `tests/_mini.py` и дополняет его:

- 25 узлов, 24 направленных ребра, 26 транзакций, включая один полный дубликат;
- 18-значные GID выше `Number.MAX_SAFE_INTEGER` для проверки строкового контракта;
- coordinator с пятью плательщиками, transit, terminal, общий сборщик;
- depth=4, неполный вход не-seed и изолированный seed.

Роли, score, кластеры и обязательные CSV считает обычный pipeline. Готовые
ответы и CSV для браузера не подставляются. Copilot API запускается локально;
`NVIDIA_API_KEY` и `NVIDIA_MODEL` удаляются из окружения процесса. Readiness
проверяется через Vite proxy, включая `nvidia_available=false`. Браузерные тесты
проходят вопросы о приоритете, общем сборщике, следующем шаге, отказ API,
план запроса данных, сохранение дела, импорт и экспорт.

Синтетика лежит в `.ci-data/`, а не `data/`: **23 Python-теста, которым нужен
архив организаторов, явно пропускаются**. Frontend-проверка полного реального
CSV-набора также пропускается без `MONEY_GRAPH_TEST_OUT`. Проверки адаптера и
плана по имеющемуся `out/` работают на синтетике. Это не замер на 2 248 узлах,
не проверка реального NVIDIA и не Docker smoke. Пакет на реальном архиве и
полная приёмка остаются в PAN-68/PAN-49.

## Локальное воспроизведение

Используйте отдельный чистый checkout/worktree с Python 3.11 и Node.js 22.
Команды не должны выполняться поверх рабочего `out/` с реальными данными.
Генератор отказывается перезаписывать существующий каталог входа.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci --prefix frontend
(cd frontend && npx --no-install playwright install chromium)

test ! -e out
python -m scripts.ci_fixture --data .ci-data
python -m money_graph --data .ci-data --out out --check-repro
python -m agent_tools.evaluation --out out
python -m pytest tests -ra
npm test --prefix frontend
npm run build --prefix frontend
PLAYWRIGHT_HTML_OPEN=never bash scripts/ci-browser.sh
```

На Linux для Chromium может понадобиться `playwright install --with-deps chromium`
(это делает workflow). `PYTHON=/absolute/path/to/python` позволяет выбрать Python
для browser harness без активации окружения. По умолчанию используются loopback
порты `18769` (API) и `15179` (Vite); переопределение — `CI_API_PORT` и
`CI_UI_PORT`. Занятый порт приводит к ошибке, чужой сервер не переиспользуется.
При выходе harness останавливает только запущенные им процессы.

## Диагностика и доступ

При ошибке команда возвращает ненулевой статус; `pipefail` сохраняет ошибку
команды перед `tee`. Повторные попытки тестов отключены: нестабильный тест
не становится зелёным благодаря повторному запуску. Каждый job ограничен
по времени. Артефакты хранятся 7 дней:

- `python-diagnostics`: JUnit, журнал pytest со списком пропусков, версии зависимостей;
- `browser-diagnostics`: журналы pipeline/evaluation/unit/build/API/Vite, HTML-отчёт
  Playwright, screenshots, trace при ошибке и отчёт воспроизводимости pipeline.

Скачать их можно внизу страницы конкретного run. Trace открывается командой
`npx playwright show-trace /path/to/trace.zip` из `frontend/`.
В браузерных артефактах CI присутствует только синтетика. При локальном запуске
с реальным `out/` screenshots/trace могут содержать данные: они игнорируются Git.

Workflow использует `contents: read`, не получает secrets, не сохраняет Git
credentials и фиксирует actions полным SHA. Это соответствует
[рекомендациям GitHub](https://docs.github.com/en/actions/reference/security/secure-use).
Артефакты ограничены указанными каталогами диагностики; `.env` и архив входа
не входят в пути загрузки.

## Исправленные препятствия для CI

Проверка отсутствия лишнего GID раньше искала `12` во всём запросе, включая
таймаут вроде `7.999794125`, и случайно падала. Теперь проверяются именно
`messages` и `tools`, отправляемые модели.

E2E устаревшего дела раньше менял роль только в `nodes_roles.csv`. После PAN-64
это нарушает согласованность с `top_nodes.csv` и проверяет недоступный источник,
а не смену версии. Теперь меняется валидный evidence; fingerprint изменяется,
все три сохранённых материала помечаются снимками другой выгрузки.
