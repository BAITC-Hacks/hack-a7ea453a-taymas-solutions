import { describe, expect, it, vi } from 'vitest'
import { loadData } from '../src/data'
import { filterNodes, findNodeByGid, nodeNeighbors } from '../src/filters'
import { parseCsv } from '../src/csv'
import type { FilterState, NodeRecord } from '../src/types'

const base: FilterState = { search: '', role: 'all', cluster: 'all', depth: 'all', seed: 'all', topOnly: false, limit: 160 }
const node = (gid: string, role = 'peripheral', cluster_id = 1): NodeRecord => ({ gid, role, role_score: .5, cluster_id, priority_score: .4, evidence: 'x', priority_why: 'y', depth: 1, is_seed: false, boundary_depth4: false, in_deg: 1, out_deg: 1, n_payers: 1, n_receivers: 1, in_kzt: 10, out_kzt: 10 })

describe('CSV and graph data contracts', () => {
  it('keeps quoted commas and large identifiers intact', () => {
    const rows = parseCsv('gid,evidence\n100000003684369100,"gets money, then forwards it"\n')
    expect(rows[0].gid).toBe('100000003684369100')
    expect(rows[0].evidence).toBe('gets money, then forwards it')
  })

  it('finds a gid outside the top list and respects every filter', () => {
    const nodes = [node('100000003684369100', 'coordinator', 7), node('200000000000000001', 'terminal', 8)]
    expect(filterNodes(nodes, { ...base, search: '200000000000000001' }, new Set(['100000003684369100']))).toHaveLength(1)
    expect(filterNodes(nodes, { ...base, role: 'terminal' }, new Set())).toEqual([nodes[1]])
    expect(filterNodes(nodes, { ...base, topOnly: true }, new Set(['100000003684369100']))).toEqual([nodes[0]])
    expect(findNodeByGid(nodes, '200000000000000001')?.gid).toBe('200000000000000001')
  })

  it('separates incoming and outgoing direction for reciprocal edges', () => {
    const edges = [{ src: 'A', dst: 'B', sum_kzt: 20, n_tx: 2 }, { src: 'B', dst: 'A', sum_kzt: 10, n_tx: 1 }]
    expect(nodeNeighbors('A', edges).incoming[0].src).toBe('B')
    expect(nodeNeighbors('A', edges).outgoing[0].dst).toBe('B')
  })

  it('loads all required CSVs from the local output directory', async () => {
    const fixture = new Map([
      ['nodes_roles.csv', 'gid,role,role_score,cluster_id,priority_score,evidence,depth,is_seed\n100000003684369100,coordinator,0.9,1,0.8,"evidence, with comma",1,false\n'],
      ['clusters.csv', 'cluster_id,n_nodes,n_seed,sum_kzt_internal,top_gids,hypothesis\n1,1,0,10,100000003684369100,h\n'],
      ['top_nodes.csv', 'rank,gid,role,priority_score,why\n1,100000003684369100,coordinator,0.8,priority why\n'],
      ['edge_table.csv', 'src,dst,sum_kzt,n_tx\n100000003684369100,100000003684369100,10,1\n'],
    ])
    vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve({ ok: true, text: () => Promise.resolve(fixture.get(url.split('/').pop()!) ?? '') })))
    const data = await loadData('/out')
    expect(data.nodes[0].gid).toBe('100000003684369100')
    expect(data.nodes[0].priority_why).toBe('priority why')
    expect(data.edges[0].src).toBe('100000003684369100')
    vi.unstubAllGlobals()
  })

  it('reports missing required output columns', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve({
      ok: true, text: () => Promise.resolve(url.endsWith('nodes_roles.csv') ? 'gid\n1\n' : 'x\ny\n'),
    })))
    await expect(loadData('/out')).rejects.toThrow('nodes_roles.csv: отсутствуют обязательные колонки')
    vi.unstubAllGlobals()
  })
})
