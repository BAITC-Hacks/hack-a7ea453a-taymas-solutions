import { describe, expect, it } from 'vitest'
import { flowPositions } from '../src/graphLayout'
import type { NodeRecord, EdgeRecord } from '../src/types'

const nodes = (...gids: string[]) => gids.map(gid => ({ gid }) as NodeRecord)
const edge = (src: string, dst: string, sum_kzt = 10): EdgeRecord => ({ src, dst, sum_kzt, n_tx: 1 })
describe('directional view', () => {
  it('separates payers, receivers and evidence without direct links; preserves string ids', () => {
    const center = '100000008165763100'
    const positions = flowPositions(nodes(center, 'payer', 'receiver', 'extra'), [edge('payer', center), edge(center, 'receiver')], center)
    expect(positions[center]).toEqual({ x: 0, y: 0 })
    expect(positions.payer.x).toBeLessThan(0)
    expect(positions.receiver.x).toBeGreaterThan(0)
    expect(positions.extra.y).toBeGreaterThan(positions.payer.y)
    expect(Object.keys(positions)).toHaveLength(4)
  })
  it('places bidirectional counterparties once by dominant flow and never overwrites the center for self loops', () => {
    const positions = flowPositions(nodes('center', 'both'), [edge('both', 'center', 80), edge('center', 'both', 30), edge('center', 'center')], 'center')
    expect(positions.both.x).toBeLessThan(0)
    expect(positions.center).toEqual({ x: 0, y: 0 })
    expect(Object.keys(positions)).toHaveLength(2)
  })
  it('has deterministic distinct positions independent of CSV row order', () => {
    const group = nodes('center', ...Array.from({ length: 64 }, (_, i) => `gid-${i}`))
    const edges = group.slice(1).map(n => edge(n.gid, 'center'))
    const positions = flowPositions(group, edges, 'center')
    expect(flowPositions([...group].reverse(), [...edges].reverse(), 'center')).toEqual(positions)
    expect(new Set(Object.values(positions).map(p => `${p.x},${p.y}`)).size).toBe(65)
  })
})
