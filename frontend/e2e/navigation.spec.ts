import { test, expect, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { parseCsv } from '../src/csv'

// Use the pipeline's real output, including GIDs larger than JS safe integers.
const files = new Map(['nodes_roles.csv', 'edge_table.csv', 'clusters.csv', 'top_nodes.csv']
  .map(name => [name, readFileSync(new URL(`../../out/${name}`, import.meta.url), 'utf8')]))
const nodes = parseCsv(files.get('nodes_roles.csv')!)
const edges = parseCsv(files.get('edge_table.csv')!)
const topIds = new Set(parseCsv(files.get('top_nodes.csv')!).map(node => node.gid))
const linked = nodes.find(node => !topIds.has(node.gid)
  && edges.some(edge => edge.dst === node.gid)
  && edges.some(edge => edge.src === node.gid))!
const isolated = nodes.find(node => /^(true|1)$/i.test(node.is_seed)
  && !edges.some(edge => edge.src === node.gid || edge.dst === node.gid))!

async function expectContext(page: Page, gid: string) {
  const neighbors = new Set([gid])
  for (const edge of edges) {
    if (edge.src === gid) neighbors.add(edge.dst)
    if (edge.dst === gid) neighbors.add(edge.src)
  }
  const contextEdges = edges.filter(edge => neighbors.has(edge.src) && neighbors.has(edge.dst))
  await expect(page.getByRole('region', { name: `Карточка узла ${gid}`, exact: true })).toBeVisible()
  await expect(page.getByRole('img', {
    name: new RegExp(`^Направленный граф: ${neighbors.size} узлов, ${contextEdges.length} связей\\.`),
  })).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  await page.route('**/out/*.csv', route => {
    const filename = new URL(route.request().url()).pathname.split('/').pop()!
    return route.fulfill({ contentType: 'text/csv', body: files.get(filename)! })
  })
  await page.route('**/api/copilot/status', route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({ ready: true, nvidia_available: false }),
  }))
  await page.goto('/')
  await expect(page.getByLabel('Поиск по GID')).toBeVisible()
})

test('arbitrary GID opens directed links and a neighbor without resetting facets', async ({ page }) => {
  expect(linked).toBeDefined()
  await page.getByRole('combobox', { name: 'Роль клиента', exact: true }).selectOption('terminal')
  await page.getByRole('button', { name: /Верхние приоритеты/ }).click()
  await page.getByLabel('Поиск по GID').fill(linked.gid)
  await expectContext(page, linked.gid)
  await expect(page.locator('.navigation-context')).toContainText('вне фильтров')
  const neighborLink = page.locator('#profile-panel .flow-row').first()
  const neighbor = (await neighborLink.getAttribute('aria-label'))!.replace('Открыть узел ', '')
  await neighborLink.click()
  await expectContext(page, neighbor)
  await expect(page.getByRole('combobox', { name: 'Роль клиента', exact: true })).toHaveValue('terminal')
  await expect(page.getByRole('button', { name: /Верхние приоритеты/ })).toHaveAttribute('aria-pressed', 'true')
  await page.screenshot({ path: 'test-results/navigation-neighbor.png', fullPage: true })
  await page.getByRole('button', { name: 'Снять временное окружение', exact: true }).click()
  await expect(page.locator('.navigation-context')).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Роль клиента', exact: true })).toHaveValue('terminal')
})

test('isolated seed has no invented arrows and an absent GID shows a clear error', async ({ page }) => {
  expect(isolated).toBeDefined()
  await page.getByLabel('Поиск по GID').fill(isolated.gid)
  await expectContext(page, isolated.gid)
  await expect(page.locator('#profile-panel .flow-row')).toHaveCount(0)
  let unknown = '999999999999999999'
  while (nodes.some(node => node.gid === unknown)) unknown += '9'
  await page.getByLabel('Поиск по GID').fill(unknown)
  await expect(page.getByText('Точный GID не найден', { exact: true })).toBeVisible()
  await expect(page.locator('.node-inspector')).toHaveCount(0)
})
