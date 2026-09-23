import { existsSync, readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadData } from '../src/data'
import { buildDataRequestPlan } from '../src/investigation/dataRequestPlan'

const output = new URL('../../out/', import.meta.url)
afterEach(() => vi.unstubAllGlobals())

describe.skipIf(!existsSync(new URL('nodes_roles.csv', output)))('Plan on local real outputs', () => {
  it('uses the CSV adapter and preserves every source fact, incoming flag and input row', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(readFileSync(new URL(url.split('/').pop()!, output), 'utf8'))))
    const data = await loadData()
    const before = JSON.stringify(data)
    const edges = new Map(data.edges.map(e => [`${e.src}:${e.dst}`, e]))
    for (const node of data.nodes) {
      const plan = buildDataRequestPlan(data, node.gid)
      const expectedIncoming = node.is_seed || String(node.external_inflow_suspected).toLowerCase() === 'true'
        || String(node.in_underestimated).toLowerCase() === 'true'
      expect(plan.items.some(i => i.kind === 'missing_incoming')).toBe(expectedIncoming)
      expect(plan.items.some(i => i.kind === 'missing_outgoing')).toBe(node.depth === 4 || node.boundary_depth4)
      expect(new Set(plan.items.map(i => i.id)).size).toBe(plan.items.length)
      for (const item of plan.items) for (const source of item.sources) {
        const row = source.file === 'nodes_roles.csv' ? node : edges.get(`${source.src}:${source.dst}`)
        expect(row?.[source.column]).toEqual(source.value)
      }
    }
    expect(JSON.stringify(data)).toBe(before)
  })
})
