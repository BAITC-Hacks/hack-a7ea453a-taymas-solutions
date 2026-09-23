import { describe, expect, it } from 'vitest'
import { buildDataRequestPlan } from '../src/investigation/dataRequestPlan'
import type { GraphData } from '../src/types'
import { A, B, data } from './copilot-fixture'

const C = '9007199254741099'
function graph(): GraphData {
  const copy = structuredClone(data)
  copy.nodes.push({ ...copy.nodes[0], gid: C })
  copy.edges = [
    { src: B, dst: A, sum_kzt: 5000, n_tx: 2, first_date: '2026-07-02', last_date: '2026-07-10' },
    { src: A, dst: C, sum_kzt: 5000, n_tx: 1, first_date: '2026-07-10', last_date: '2026-07-10' },
  ]
  return copy
}

describe('Data request plan', () => {
  it('handles no grounds and unknown or imprecise gids without inventing a recommendation', () => {
    expect(buildDataRequestPlan(data, A)).toMatchObject({ status: 'insufficient_data', items: [] })
    expect(buildDataRequestPlan(data, 'missing')).toMatchObject({ status: 'unknown_gid', items: [] })
    expect(buildDataRequestPlan(data, Number(A) as unknown as string).status).toBe('unknown_gid')
  })

  it('keeps simultaneous limitations, consolidates incoming reasons and cites facts exactly', () => {
    const input = graph()
    Object.assign(input.nodes[0], { depth: 4, is_seed: true, in_underestimated: 'True', external_inflow_suspected: 'True' })
    const before = structuredClone(input)
    const result = buildDataRequestPlan(input, A)
    expect(result.items.map(i => i.kind)).toEqual(['missing_outgoing', 'missing_incoming', 'same_day_order'])
    expect(new Set(result.items.map(i => i.id)).size).toBe(3)
    expect(result.items[1].sources.map(s => s.column)).toEqual(['is_seed', 'external_inflow_suspected', 'in_kzt', 'out_kzt'])
    for (const item of result.items) {
      expect(item.gid).toBe(A)
      expect(item.hypothesis).toContain('Гипотеза для проверки')
      expect(item.request.fields.length).toBeGreaterThan(0)
      expect(item.supports).toBeTruthy()
      expect(item.weakens).toBeTruthy()
      for (const fact of item.sources) {
        const row = fact.file === 'nodes_roles.csv' ? input.nodes.find(n => n.gid === fact.gid)
          : input.edges.find(e => e.src === fact.src && e.dst === fact.dst)
        expect(row?.[fact.column]).toEqual(fact.value)
      }
    }
    expect(input).toEqual(before)
    expect(result.items[0].request.period).toEqual({ scope: 'source_period' })
  })

  it.each([false, 'False', 'false', '0', '', 0])('does not treat a false CSV flag %s as true', flag => {
    const input = structuredClone(data)
    Object.assign(input.nodes[0], { external_inflow_suspected: flag, in_underestimated: flag })
    expect(buildDataRequestPlan(input, A).items).toHaveLength(0)
  })

  it('uses the pipeline flag rather than an independent inflow threshold', () => {
    const input = structuredClone(data)
    Object.assign(input.nodes[0], { in_kzt: 10000, out_kzt: 100000, external_inflow_suspected: false })
    expect(buildDataRequestPlan(input, A).items).toHaveLength(0)
    input.nodes[0].external_inflow_suspected = true
    const [item] = buildDataRequestPlan(input, A).items
    expect(item.kind).toBe('missing_incoming')
    expect(item.limitation).toContain('остатком до начала периода')
    delete input.nodes[0].external_inflow_suspected
    expect(buildDataRequestPlan(input, A).items).toHaveLength(0)
  })

  it('supports the explicit incomplete-inflow flag without a seed flag', () => {
    const input = structuredClone(data)
    input.nodes[0].in_underestimated = 'True'
    expect(buildDataRequestPlan(input, A).items[0].sources[0].column).toBe('in_underestimated')
  })

  it('does not equate no visible outflow with retained funds', () => {
    const input = structuredClone(data)
    input.nodes[0].out_deg = 0
    expect(buildDataRequestPlan(input, A).items).toHaveLength(0)
    input.nodes[0].depth = 4
    expect(buildDataRequestPlan(input, A).items[0].limitation).toContain('не доказывает оседание')
  })

  it('proves the same-day witness through endpoint dates; requires timestamps, not assumed order', () => {
    const [item] = buildDataRequestPlan(graph(), A).items
    expect(item.kind).toBe('same_day_order')
    expect(item.request.period).toEqual({ scope: 'day', date: '2026-07-10' })
    expect(item.sources.map(s => s.column)).toEqual(['last_date', 'first_date'])
    expect(item.supports).toContain('не докажет передачу тех же денег')
  })

  it('does not infer an event from overlapping ranges, a lag aggregate or a self-loop', () => {
    const input = graph()
    input.edges[1].first_date = '2026-07-03'
    input.edges[1].last_date = '2026-07-11'
    input.nodes[0].median_lag_days = 0
    input.nodes[0].fast_out_share = 1
    expect(buildDataRequestPlan(input, A).items).toHaveLength(0)
    input.edges = [{ src: A, dst: A, sum_kzt: 5000, n_tx: 1, first_date: '2026-07-10', last_date: '2026-07-10' }]
    expect(buildDataRequestPlan(input, A).items).toHaveLength(0)
  })

  it.each(['', 'bad date', '2026-02-30', '2026-7-10', '2026-07-10T00:00:00'])('ignores missing or invalid endpoint %s', value => {
    const input = graph()
    input.edges[0].last_date = value
    expect(buildDataRequestPlan(input, A).items).toHaveLength(0)
  })

  it('rejects reversed date ranges and same-direction pairs', () => {
    const input = graph()
    input.edges[0].first_date = '2026-07-20'
    expect(buildDataRequestPlan(input, A).items).toHaveLength(0)
    input.edges[0].first_date = '2026-07-10'
    input.edges[1].src = C; input.edges[1].dst = A
    expect(buildDataRequestPlan(input, A).items).toHaveLength(0)
  })

  it('is deterministic when rows change order or duplicate grounds appear', () => {
    const input = graph()
    input.edges.push({ ...input.edges[0], src: C })
    const expected = buildDataRequestPlan(input, A)
    input.nodes.reverse(); input.edges.reverse()
    expect(buildDataRequestPlan(input, A)).toEqual(expected)
    expect(expected.items).toHaveLength(1)
  })
})
