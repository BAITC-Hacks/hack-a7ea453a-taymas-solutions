import { test, expect, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import type { Core } from 'cytoscape'
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

test.use({ reducedMotion: 'reduce' })

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

// These observed neighborhoods are strongly asymmetric in the flow layout.
// Read Cytoscape's existing container registration in the test only: no product
// debug globals or test-only rendering paths are required.
test('asymmetric neighborhoods remain inside the canvas after navigation and resize', async ({ page }) => {
  async function expectNodesInsideCanvas() {
    await expect.poll(() => page.locator('.graph-canvas').evaluate(element => {
      const cy = (element as HTMLElement & { _cyreg?: { cy: Core } })._cyreg?.cy
      if (!cy) return ['graph not mounted']
      return cy.nodes().filter(node => {
        const box = node.renderedBoundingBox({ includeLabels: false })
        return box.x1 < 0 || box.y1 < 0 || box.x2 > cy.width() || box.y2 > cy.height()
      }).map(node => node.id())
    })).toEqual([])
  }
  for (const gid of ['100000001697501100', '100000008489922100', '100000002957787100']) {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.getByLabel('Поиск по GID').fill(gid)
    await expectContext(page, gid)
    await expectNodesInsideCanvas()
    await page.setViewportSize({ width: 1360, height: 900 })
    await expectNodesInsideCanvas()
  }
})
