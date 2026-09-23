import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import type { Core } from 'cytoscape'
import { parseCsv } from '../src/csv'

const files = new Map(['nodes_roles.csv', 'edge_table.csv', 'clusters.csv', 'top_nodes.csv']
  .map(name => [name, readFileSync(new URL(`../../out/${name}`, import.meta.url), 'utf8')]))
const top = parseCsv(files.get('top_nodes.csv')!)
const nodes = parseCsv(files.get('nodes_roles.csv')!)
const edges = parseCsv(files.get('edge_table.csv')!)
test.use({ reducedMotion: 'reduce' })

test.beforeEach(async ({ page }) => {
  await page.route('**/out/*.csv', route => route.fulfill({ contentType: 'text/csv',
    body: files.get(new URL(route.request().url()).pathname.split('/').pop()!)! }))
  await page.route('**/api/datasets/active', route => route.fulfill({ status: 404, body: '{}' }))
  await page.route('**/api/copilot/status', route => route.fulfill({ contentType: 'application/json',
    body: JSON.stringify({ ready: true, nvidia_available: false }) }))
  await page.goto('/')
  await expect(page.getByLabel('Поиск по GID')).toBeVisible()
})

test('all priorities are accessible in CSV order, including long GIDs after the first 20', async ({ page }) => {
  expect(top.length).toBeGreaterThan(20)
  await expect(page.locator('.priority-count').first()).toHaveText(`Показано 5 из ${top.length} клиентов`)
  await page.getByRole('button', { name: `Все ${top.length} клиентов`, exact: true }).click()
  await expect(page.locator('.table-row')).toHaveCount(top.length)
  expect(await page.locator('.table-node > span').allTextContents()).toEqual(top.map(row => row.gid))
  expect(await page.locator('.table-row .rank').allTextContents()).toEqual(top.map(row => row.rank.padStart(2, '0')))
  const target = top[25] ?? top[20]
  await page.getByRole('button', { name: `Открыть клиент ${target.gid}`, exact: true }).click()
  await expect(page.getByRole('region', { name: `Карточка узла ${target.gid}`, exact: true })).toBeVisible()
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    expect(await page.locator('.table-node').evaluateAll(buttons => buttons.every(button => {
      const cell = button.parentElement!.getBoundingClientRect(), bounds = button.getBoundingClientRect()
      return bounds.left >= cell.left - 1 && bounds.right <= cell.right + 1
        && button.scrollWidth <= button.clientWidth + 1
    }))).toBe(true)
  }
})

test('all incoming and outgoing counterparties are readable and open the right profile', async ({ page }) => {
  const target = nodes.find(node => edges.filter(edge => edge.dst === node.gid).length > 5
    && edges.filter(edge => edge.src === node.gid).length > 5)!
  expect(target).toBeDefined()
  await page.getByLabel('Поиск по GID').fill(target.gid)
  const profile = page.getByRole('region', { name: `Карточка узла ${target.gid}`, exact: true })
  await expect(profile).toBeVisible()
  const aggregates = await profile.locator('.flow-summary').textContent()
  const expected = [
    edges.filter(edge => edge.dst === target.gid).sort((a, b) => Number(b.sum_kzt) - Number(a.sum_kzt)).map(edge => edge.src),
    edges.filter(edge => edge.src === target.gid).sort((a, b) => Number(b.sum_kzt) - Number(a.sum_kzt)).map(edge => edge.dst),
  ]
  for (const [index, gids] of expected.entries()) {
    const list = profile.locator('.flow-list').nth(index)
    await list.getByRole('button', { name: `Показать все ${gids.length} связей`, exact: true }).click()
    expect(await list.locator('.flow-row').evaluateAll(buttons => buttons.map(button => button.getAttribute('aria-label'))))
      .toEqual(gids.map(gid => `Открыть узел ${gid}`))
  }
  expect(await profile.locator('.flow-summary').textContent()).toBe(aggregates)
  expect(await profile.locator('.flow-row').evaluateAll(buttons => buttons.every(button => {
    const gid = button.firstElementChild!.getBoundingClientRect(), amount = button.lastElementChild!.getBoundingClientRect()
    return gid.right <= amount.left + 1
  }))).toBe(true)
  const neighbor = expected[1].at(-1)!
  await profile.locator('.flow-list').nth(1).getByRole('button', { name: `Открыть узел ${neighbor}`, exact: true }).click()
  await expect(page.getByRole('region', { name: `Карточка узла ${neighbor}`, exact: true })).toBeVisible()
})

test('cluster legend matches rendered colors and retains arrow direction and seed rings', async ({ page }) => {
  const seed = nodes.find(node => /^(true|1)$/i.test(node.is_seed) && edges.some(edge => edge.src === node.gid || edge.dst === node.gid))!
  await page.getByLabel('Поиск по GID').fill(seed.gid)
  await page.getByRole('button', { name: 'Кластеры', exact: true }).click()
  await expect(page.getByLabel('Кластеры текущего вида')).toBeVisible()
  const rendered = await page.locator('.graph-canvas').evaluate(element => {
    const cy = (element as HTMLElement & { _cyreg: { cy: Core } })._cyreg.cy
    return { clusters: [...new Set(cy.nodes().map(node => node.data('cluster')))].sort((a, b) => a - b),
      colors: cy.nodes().map(node => [String(node.data('cluster')), node.style('background-color')]),
      arrows: cy.edges().map(edge => edge.style('target-arrow-shape')),
      seedStyles: cy.nodes('[seed = 1]').map(node => node.style('border-style')) }
  })
  const entries = await page.locator('.cluster-legend li').evaluateAll(items => items.map(item => ({
    id: item.getAttribute('data-cluster-id')!, color: getComputedStyle(item.querySelector('i')!).backgroundColor,
  })))
  expect(entries.map(entry => Number(entry.id))).toEqual(rendered.clusters)
  for (const [id, color] of rendered.colors) {
    // Chromium and Cytoscape round HSL -> RGB channels differently by at most 1.
    const actual = color.match(/\d+/g)!.map(Number)
    const expected = entries.find(entry => entry.id === id)!.color.match(/\d+/g)!.map(Number)
    expect(actual).toHaveLength(3)
    actual.forEach((channel, index) => expect(Math.abs(channel - expected[index])).toBeLessThanOrEqual(1))
  }
  expect(rendered.arrows.length).toBeGreaterThan(0)
  expect(rendered.arrows.every(arrow => arrow === 'triangle')).toBe(true)
  expect(rendered.seedStyles.length).toBeGreaterThan(0)
  expect(rendered.seedStyles.every(style => style === 'double')).toBe(true)
  await page.screenshot({ path: 'test-results/complete-display-clusters.png', fullPage: true })
  await page.getByRole('button', { name: 'Роли', exact: true }).click()
  await expect(page.getByLabel('Цвета кластеров на графе')).toHaveCount(0)
})
