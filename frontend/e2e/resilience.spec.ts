import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'

// Реальная выгрузка: python -m money_graph ... && python -m analytics.resilience_export --out out
const payload = JSON.parse(readFileSync(new URL('../../out/resilience.json', import.meta.url), 'utf8'))
const pct = (v: number | null) => (v === null ? 'не определено' : `${(v * 100).toFixed(1)}%`)

test('изъятие top-N: числа из PAN-44, N=0, контроли, переход к узлу', async ({ page }) => {
  await page.route('**/api/copilot/**', route => route.fulfill({ status: 503, body: '{}' }))
  await page.goto('/')
  const panel = page.locator('#resilience')
  await panel.scrollIntoViewIfNeeded()
  await expect(panel.getByRole('heading', { name: 'Устойчивость сети' })).toBeVisible()

  const cell = (metric: string) => panel.getByTestId(`priority-${metric}`)
  await expect(cell('weak_pair_loss')).toHaveText(pct(payload.deterministic.priority['20'].weak_pair_loss))
  await expect(cell('seed_reach_loss')).toHaveText(pct(payload.deterministic.priority['20'].seed_reach_loss))
  await expect(panel.getByTestId('fragment-check')).toContainText('Фрагменты сверены с файлом')
  await expect(panel.getByTestId('display-limit')).toContainText(`${payload.sources.graph.n_nodes} узлов`)
  await expect(panel.getByText(`${payload.parameters.random_runs} прогонов`).first()).toBeVisible()
  await panel.screenshot({ path: 'test-results/resilience-n20.png' })

  await panel.getByRole('radio', { name: '0', exact: true }).click()
  await expect(cell('removed_kzt_share')).toHaveText(pct(payload.baseline.removed_kzt_share))
  await expect(panel.getByRole('heading', { name: 'Ничего не удалено' })).toBeVisible()

  await panel.getByRole('radio', { name: '5', exact: true }).click()
  await panel.getByRole('radio', { name: /degree/ }).click()
  await expect(panel.getByRole('heading', { name: /Удалены: по числу связей/ })).toBeVisible()
  const first = payload.removals.degree[0]
  await panel.locator('.resilience-removed button', { hasText: first }).click()
  await expect(page.getByRole('region', { name: `Карточка узла ${first}`, exact: true })).toBeVisible()
})
