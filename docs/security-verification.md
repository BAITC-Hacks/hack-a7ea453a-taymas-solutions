# PAN-67: проверка зависимостей и локальных секретов

Проверено 23 сентября 2026 года на Node.js 24.14.1 / npm 11.11.0,
повторно после слияния `origin/main` (`708a61b`).

## Изменения

- `.env` и `.env.*` игнорируются Git в корне и вложенных папках. Исключение —
  `.env.example` с пустыми `NVIDIA_API_KEY` и `NVIDIA_MODEL`. Для локального
  Copilot заполнение ключа не требуется; Docker Compose читает локальный `.env`.
- Vitest зафиксирован на `4.1.11`; его зависимость `@vitest/mocker` также
  разрешается в `4.1.11`. Принудительные overrides и `npm audit fix --force`
  не использовались. Версии React, Vite и Cytoscape не изменены.

[Advisory проекта Vitest](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9)
описывает чтение файлов через redirect mock в доступном извне dev-сервере.
Первое исправление поддерживаемой ветки — `4.1.11`; для `3.x` backport не
планируется. Это зависимость инструментов разработки. Согласно
[npm metadata Vitest 4.1.11](https://registry.npmjs.org/vitest/4.1.11), версия
совместима с Vite 7 и Node.js 20, 22, 24; Docker использует Node.js 22.

## Результаты

| Проверка | Результат |
|---|---|
| `npm audit` до изменения | 2 moderate: `vitest` и `@vitest/mocker`, один advisory |
| `npm ci --ignore-scripts` | Успешная чистая установка lockfile |
| `npm ls vitest @vitest/mocker vite --depth=1` | Vitest/mocker 4.1.11, Vite 7.3.6 |
| `npm test` | 70 passed, 1 skipped: отсутствует локальный `out/nodes_roles.csv` |
| `npm run build` | Успешно; существующее предупреждение о JS chunk >500 kB |
| `npm audit --json` после изменения | 0 уязвимостей, включая devDependencies |

Без вывода значений проверены 137 отслеживаемых текстовых файлов рабочей копии:
шаблоны приватных ключей, известных provider tokens и строковых присваиваний
секретов. Совпадений нет; отслеживаемых приватных `.env` также нет. Это проверка
текущих файлов по ограниченным шаблонам, не аудит истории Git.

## Повторная проверка

Из корня репозитория:

```bash
git check-ignore --no-index -v .env .env.local frontend/.env .env.example
git ls-files '.env' '.env.*' '**/.env' '**/.env.*'
cd frontend
npm ci --ignore-scripts
npm test
npm run build
npm audit
```

Для `.env.example` первая команда показывает правило-исключение
`!.env.example`; во второй допускаются только такие шаблоны. Команды проверки
Git показывают пути и правила, не содержимое файлов. Результат `npm audit`
относится к дате проверки и может измениться при появлении новых advisory.
