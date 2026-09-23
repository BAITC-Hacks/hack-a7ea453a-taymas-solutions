import { test, expect, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync } from 'node:fs'
import { parseCsv } from '../src/csv'

const dataDir = process.env.UPLOAD_DATA_DIR ?? fileURLToPath(new URL('../../data', import.meta.url))
const secondDir = process.env.UPLOAD_SECOND_DATA_DIR
const names = ['nodes', 'edges', 'transactions'] as const

async function upload(page: Page, directory: string) {
  const open = page.getByRole('button', { name: 'Загрузить другой набор', exact: true })
  if (await open.isVisible()) await open.click()
  for (const name of names) await page.getByLabel(`${name}.parquet`, { exact: true }).setInputFiles(path.join(directory, `${name}.parquet`))
  const accepted = page.waitForResponse(r => r.url().endsWith('/api/datasets/jobs') && r.request().method() === 'POST')
  await page.getByRole('button', { name: /Построить граф/ }).click()
  expect((await accepted).status()).toBe(202)
  await expect(page.getByRole('img', { name: 'Направленный граф транзакционной сети' })).toBeVisible({ timeout: 300_000 })
  await expect(page.getByText(/^Анализ завершён/)).toBeVisible({ timeout: 300_000 })
  await expect(page.getByText('Локальный анализ готов', { exact: true })).toBeVisible()
}

test('upload Parquet, download CSV, inspect node and ask Copilot; a failed replacement preserves graph', async ({ page, request }) => {
  test.setTimeout(330_000)
  test.skip(!names.every(name => existsSync(path.join(dataDir, `${name}.parquet`))), 'Provide UPLOAD_DATA_DIR with the three Parquet files')
  await page.goto('/')
  await upload(page, dataDir)
  const active = await (await request.get('/api/datasets/active')).json()
  const versions = await page.getByRole('navigation', { name: 'Скачать результаты' }).getByRole('link').evaluateAll(links => links.map(link => link.getAttribute('href')))
  expect(versions).toHaveLength(3)
  for (const href of versions) expect(href).toContain(`/api/datasets/${active.dataset_id}/files/`)
  const nodes = parseCsv(await (await request.get(`${active.files_base}/nodes_roles.csv`)).text())
  const top = parseCsv(await (await request.get(`${active.files_base}/top_nodes.csv`)).text())
  expect(top.length).toBeGreaterThanOrEqual(Math.min(20, nodes.length))
  expect(nodes.every(node => node.role && node.evidence && node.cluster_id)).toBe(true)
  const gid = top[0].gid
  await page.getByLabel('Поиск по GID').fill(gid)
  await page.getByRole('tab', { name: 'Обзор клиента', exact: true }).click()
  await expect(page.getByRole('region', { name: `Карточка узла ${gid}`, exact: true })).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('link', { name: /nodes_roles.csv/ }).click()
  expect((await download).suggestedFilename()).toBe('nodes_roles.csv')
  await page.getByRole('tab', { name: /AI Copilot/ }).click()
  await page.getByRole('button', { name: /Почему этот узел в топе/ }).click()
  const answerPromise = page.waitForResponse('**/api/copilot/answer')
  await page.getByRole('button', { name: /Разобрать вопрос/ }).click()
  expect((await answerPromise).headers()['x-dataset-version']).toBe(active.dataset_id)
  await expect(page.getByText('Локальный ответ', { exact: true })).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: 'test-results/upload-ready.png', fullPage: true })

  await page.getByRole('button', { name: 'Загрузить другой набор', exact: true }).click()
  for (const name of names) await page.getByLabel(`${name}.parquet`, { exact: true }).setInputFiles(path.join(dataDir, `${name}.parquet`))
  await page.getByLabel('nodes.parquet', { exact: true }).setInputFiles({ name: 'nodes.parquet', mimeType: 'application/octet-stream', buffer: Buffer.from('broken parquet') })
  await page.getByRole('button', { name: /Построить граф/ }).click()
  await expect(page.getByRole('alert')).toContainText('nodes.parquet', { timeout: 30_000 })
  const preserved = await (await request.get('/api/datasets/active')).json()
  expect(preserved.dataset_id).toBe(active.dataset_id)
  await expect(page.getByRole('img', { name: 'Направленный граф транзакционной сети' })).toBeVisible()
})

test('a different dataset replaces the graph, clears prior context and rejects stale Copilot version', async ({ page, request }) => {
  test.setTimeout(330_000)
  test.skip(!secondDir, 'Set UPLOAD_SECOND_DATA_DIR to a second valid dataset')
  await page.goto('/')
  const before = await (await request.get('/api/datasets/active')).json()
  test.skip(!before.dataset_id, 'Run the upload scenario first')
  await expect(page.getByLabel('Поиск по GID')).toBeVisible()
  const oldTop = parseCsv(await (await request.get(`${before.files_base}/top_nodes.csv`)).text())
  const oldCsv = await (await request.get(`${before.files_base}/nodes_roles.csv`)).text()
  await page.getByLabel('Поиск по GID').fill(oldTop[0].gid)
  await upload(page, secondDir!)
  const after = await (await request.get('/api/datasets/active')).json()
  expect(after.dataset_id).not.toBe(before.dataset_id)
  expect(await (await request.get(`${after.files_base}/nodes_roles.csv`)).text()).not.toBe(oldCsv)
  await expect(page.getByLabel('Поиск по GID')).toHaveValue('')
  await expect(page.locator('.copilot-summary')).toHaveCount(0)
  await expect(page.locator('.graph-evidence-note')).toHaveCount(0)
  const stale = await request.post('/api/copilot/answer', { headers: { 'X-Dataset-Version': before.dataset_id }, data: { question: 'Кого проверить первым и почему?', use_nvidia: false } })
  expect(stale.status()).toBe(409)
  await page.getByRole('button', { name: /Почему этот узел в топе/ }).click()
  const answer = page.waitForResponse('**/api/copilot/answer')
  await page.getByRole('button', { name: /Разобрать вопрос/ }).click()
  expect((await answer).headers()['x-dataset-version']).toBe(after.dataset_id)
  await expect(page.getByText('Локальный ответ', { exact: true })).toBeVisible()
})
