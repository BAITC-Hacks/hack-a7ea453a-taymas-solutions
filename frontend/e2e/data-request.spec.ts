import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { parseCsv } from '../src/csv'

const nodes = parseCsv(readFileSync(new URL('../../out/nodes_roles.csv', import.meta.url), 'utf8'))
const boundary = nodes.find(n => n.depth === '4')!.gid
const external = nodes.find(n => n.external_inflow_suspected.toLowerCase() === 'true' && n.depth !== '4')!.gid

test('local request plan survives API outage, changes with selection and keeps the graph usable', async ({ page }) => {
  await page.route('**/api/copilot/**', route => route.fulfill({ status: 503, body: '{}' }))
  await page.goto('/')
  await expect(page.getByText('Нет связи с помощником')).toBeVisible()
  await page.getByLabel('Поиск по GID').fill(boundary)
  await page.locator('.data-request-plan > summary').click()
  await expect(page.getByText('Проверить продолжение потока', { exact: true })).toBeVisible()
  await page.getByText('Поля, проверка и источники', { exact: true }).click()
  await expect(page.getByText('nodes_roles.csv · depth = 4', { exact: true })).toBeVisible()
  expect((await page.locator('.workspace').boundingBox())!.height).toBeLessThan(900)
  await page.locator('.data-request-plan').screenshot({ path: 'test-results/data-request-boundary.png' })
  await page.getByLabel('Поиск по GID').fill(external)
  await expect(page.getByText('Уточнить источник средств', { exact: true })).toBeVisible()
  await expect(page.getByText('Проверить продолжение потока', { exact: true })).toHaveCount(0)
  await expect(page.locator('.data-request-target')).toContainText(external)
  await page.locator('.data-request-target button').click()
  await expect(page.getByRole('region', { name: `Карточка узла ${external}`, exact: true })).toBeVisible()
  await expect(page.getByRole('img', { name: 'Направленный граф транзакционной сети' })).toBeVisible()
})
