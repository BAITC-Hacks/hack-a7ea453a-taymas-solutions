import { describe, expect, it } from 'vitest'
import { buildGraphView } from '../src/filters'
import { clusterColor, evidenceParts, money } from '../src/presentation'
import type { EdgeRecord, FilterState, NodeRecord } from '../src/types'

const filters: FilterState = {
  search: '',
  role: 'all',
  cluster: 'all',
  depth: 'all',
  seed: 'all',
  topOnly: false,
  limit: 30,
}
const node = (gid: string, priority = 0.4): NodeRecord => ({
  gid,
  role: 'peripheral',
  role_score: 0.5,
  cluster_id: 1,
  priority_score: priority,
  evidence: '',
  priority_why: '',
  depth: 1,
  is_seed: false,
  boundary_depth4: false,
  in_deg: 1,
  out_deg: 1,
  n_payers: 1,
  n_receivers: 1,
  in_kzt: 10,
  out_kzt: 10,
})
const a = '100000008165763100',
  b = '100000003037476100',
  c = '100000008165763101'
const nodes = [node(a, 0.9), node(b, 0.01), node(c, 0.5), node('isolated', 0)]
const edges: EdgeRecord[] = [
  { src: a, dst: b, sum_kzt: 100, n_tx: 2 },
  { src: b, dst: c, sum_kzt: 90, n_tx: 1 },
  { src: c, dst: b, sum_kzt: 20, n_tx: 1 },
]

describe('Investigation graph navigation', () => {
  it('exact GID opens both directions despite stale facets and top-only mode', () => {
    const view = buildGraphView(
      nodes,
      edges,
      { ...filters, search: b, role: 'terminal', depth: '0', cluster: '99', topOnly: true },
      new Set([a]),
    )
    expect(view.nodes.map((n) => n.gid).sort()).toEqual([a, b, c].sort())
    expect(view.edges).toHaveLength(3)
    expect(view.focused).toBe(true)
    expect(view.outsideFilterCount).toBe(3)
    expect(view.outsideLimitCount).toBe(0)
  })
  it('keeps every observed neighbor and directed edge beyond the cap', () => {
    const view = buildGraphView(nodes, edges, { ...filters, limit: 2 }, new Set(), b, true)
    expect(view.nodes.map((n) => n.gid)).toContain(b)
    expect(view.nodes).toHaveLength(3)
    expect(view.edges).toEqual(edges)
    expect(view.outsideLimitCount).toBe(1)
    expect(view.total).toBe(3)
    expect(
      view.edges.every(
        (e) => view.nodes.some((n) => n.gid === e.src) && view.nodes.some((n) => n.gid === e.dst),
      ),
    ).toBe(true)
  })
  it('preserves an isolated seed and a real zero-flow graph', () => {
    const view = buildGraphView(nodes, edges, { ...filters, search: 'isolated' }, new Set())
    expect(view.nodes).toEqual([nodes[3]])
    expect(view.edges).toEqual([])
  })
  it('unknown and partial searches do not reintroduce a previous selection', () => {
    expect(buildGraphView(nodes, edges, { ...filters, search: '123' }, new Set(), a).nodes).toEqual(
      [],
    )
    expect(
      buildGraphView(nodes, edges, { ...filters, search: '10000000816576310' }, new Set()).nodes,
    ).toHaveLength(2)
  })
  it('applies normal filters and respects a 500 node cap', () => {
    const many = Array.from({ length: 700 }, (_, i) =>
      node(`100000000000${String(i).padStart(6, '0')}`, i / 1000),
    )
    expect(buildGraphView(many, [], { ...filters, limit: 500 }, new Set()).nodes).toHaveLength(500)
    expect(buildGraphView(nodes, edges, { ...filters, topOnly: true }, new Set([a])).nodes).toEqual(
      [nodes[0]],
    )
  })
})

describe('Evidence and display values', () => {
  it('preserves complete evidence as text, including untrusted markup and decimals', () => {
    const text = '<script>alert(1)</script> 8.6 млн, 109% (+0.21); 15 плательщиков'
    const parts = evidenceParts(text)
    expect(parts.map((p) => p.text).join('')).toBe(text)
    expect(parts.filter((p) => p.numeric).map((p) => p.text)).toEqual([
      '1',
      '8.6',
      '109%',
      '0.21',
      '15',
    ])
  })
  it('formats zero, small amounts, thousands and millions without inventing money', () => {
    expect(money(0)).toBe('0')
    expect(money(450)).toBe('450')
    expect(money(1500)).toBe('1,5 тыс.')
    expect(money(1_200_000)).toBe('1.2 млн')
  })
  it('uses stable, valid cluster colors', () => {
    expect(clusterColor(5)).toBe(clusterColor(5))
    expect(clusterColor(5)).not.toBe(clusterColor(6))
    expect(clusterColor(-1)).toMatch(/^hsl\([\d.]+, 64%, 70%\)$/)
  })
})
