import { test, expect } from '@playwright/test'
import { parseCsv } from '../src/csv'

let edges: ReturnType<typeof parseCsv>
let primary: string

test.beforeAll(async ({ request }) => {
  const response = await request.get('/api/datasets/active')
  const active = response.ok() ? await response.json() : null
  const base = active?.files_base ?? '/out'
  const top = parseCsv(await (await request.get(`${base}/top_nodes.csv`)).text())
  edges = parseCsv(await (await request.get(`${base}/edge_table.csv`)).text())
  expect(top[0]?.gid, 'Upload a dataset before running Copilot smoke').toBeTruthy()
  primary = top[0].gid
})

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Локальный анализ готов')).toBeVisible()
})

test('explanation, exact gid navigation, focus and evidence beyond filters', async ({ page }) => {
  let submissions = 0
  page.on('request', request => { if (request.url().endsWith('/api/copilot/answer')) submissions++ })
  await page.screenshot({ path: 'test-results/copilot-initial.png', fullPage: true })
  await page.getByRole('combobox', { name: 'Роль', exact: true }).selectOption('terminal')
  await page.getByRole('button', { name: /Почему этот узел в топе/ }).click()
  await page.getByRole('button', { name: /Разобрать вопрос/ }).click()
  await expect(page.getByText('Локальный ответ', { exact: true })).toBeVisible()
  await expect(page.locator('.copilot-summary')).toContainText('гипотеза для проверки')
  await expect(page.locator('.graph-evidence-note')).toContainText('вне фильтров')
  expect((await page.locator('.workspace').boundingBox())!.height).toBeLessThan(900)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: 'test-results/copilot-answer.png', fullPage: true })
  await page.getByRole('button', { name: `Открыть узел ${primary}`, exact: true }).first().click()
  await expect(page.getByRole('region', { name: `Карточка узла ${primary}`, exact: true })).toBeVisible()
  expect(await page.getByRole('combobox', { name: 'Роль', exact: true }).inputValue()).toBe('terminal')
  expect(submissions).toBe(1)
})

test('common collector from real incoming edges', async ({ page }) => {
  const byTarget = new Map<string, string[]>()
  for (const edge of edges) byTarget.set(edge.dst, [...(byTarget.get(edge.dst) ?? []), edge.src])
  const payers = byTarget.get(primary) ?? [...byTarget.values()].find(gids => gids.length >= 2)!
  expect(payers.length).toBeGreaterThanOrEqual(2)
  for (const gid of payers.slice(0, 2)) {
    await page.getByLabel('Узлы для проверки').fill(gid)
    await page.getByRole('button', { name: 'Добавить GID', exact: true }).click()
  }
  await page.getByRole('button', { name: /Кто общий сборщик/ }).click()
  await page.getByRole('button', { name: /Разобрать вопрос/ }).click()
  await expect(page.getByText('Локальный ответ', { exact: true })).toBeVisible()
  await expect(page.locator('.copilot-summary')).toContainText('общего сборщика')
  await expect(page.locator('.copilot-candidate')).not.toHaveCount(0)
})

test('next step and API error leave graph and filters usable', async ({ page }) => {
  await page.getByRole('button', { name: /Какой следующий шаг/ }).click()
  await page.getByRole('button', { name: /Разобрать вопрос/ }).click()
  await expect(page.getByText('Локальный ответ', { exact: true })).toBeVisible()
  await expect(page.locator('.copilot-next')).toContainText('Запросить выписки')
  await page.route('**/api/copilot/answer', route => route.fulfill({ status: 503, body: '{}' }))
  await page.getByRole('button', { name: /Разобрать вопрос/ }).click()
  await expect(page.getByRole('alert')).toContainText('Помощник занят')
  await page.getByRole('combobox', { name: 'Роль', exact: true }).selectOption('transit')
  await expect(page.getByRole('img', { name: 'Направленный граф транзакционной сети' })).toBeVisible()
  await expect(page.locator('.graph-evidence-note')).toHaveCount(0)
})
