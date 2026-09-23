import { booleanValue, numberValue, parseCsv } from './csv'
import type { ClusterRecord, EdgeRecord, GraphData, NodeRecord, TopRecord } from './types'

export class DataLoadError extends Error {
  constructor(message: string) { super(message); this.name = 'DataLoadError' }
}

function node(row: Record<string, string>): NodeRecord {
  return {
    ...row,
    // gid is deliberately kept as a string: these identifiers are longer than JS's safe integer range.
    gid: row.gid.trim(), role: row.role || 'peripheral',
    role_score: numberValue(row.role_score), cluster_id: numberValue(row.cluster_id),
    priority_score: numberValue(row.priority_score), evidence: row.evidence || 'Нет объяснения',
    priority_why: row.priority_why || row.why || 'Приоритет рассчитан структурными признаками графа',
    depth: numberValue(row.depth), is_seed: booleanValue(row.is_seed),
    boundary_depth4: booleanValue(row.boundary_depth4) || numberValue(row.depth) >= 4,
    in_deg: numberValue(row.in_deg || row.n_payers), out_deg: numberValue(row.out_deg || row.n_receivers),
    n_payers: numberValue(row.n_payers || row.in_deg), n_receivers: numberValue(row.n_receivers || row.out_deg),
    in_kzt: numberValue(row.in_kzt), out_kzt: numberValue(row.out_kzt),
  }
}

function edge(row: Record<string, string>): EdgeRecord {
  return { ...row, src: row.src.trim(), dst: row.dst.trim(), sum_kzt: numberValue(row.sum_kzt), n_tx: numberValue(row.n_tx) }
}

async function getCsv(base: string, file: string): Promise<Record<string, string>[]> {
  const response = await fetch(`${base.replace(/\/$/, '')}/${file}`)
  if (!response.ok) throw new DataLoadError(`${file}: сервер вернул ${response.status}`)
  return parseCsv(await response.text())
}

export async function loadData(base = '/out'): Promise<GraphData> {
  const [nodesRows, clusterRows, topRows, edgeRows] = await Promise.all([
    getCsv(base, 'nodes_roles.csv'), getCsv(base, 'clusters.csv'),
    getCsv(base, 'top_nodes.csv'), getCsv(base, 'edge_table.csv'),
  ])
  if (!nodesRows.length) throw new DataLoadError('nodes_roles.csv пустой')
  const nodes = nodesRows.map(node)
  const nodeIds = new Set(nodes.map((item) => item.gid))
  const edges = edgeRows.map(edge).filter((item) => nodeIds.has(item.src) && nodeIds.has(item.dst))
  const clusters: ClusterRecord[] = clusterRows.map((row) => ({
    ...row, cluster_id: numberValue(row.cluster_id), n_nodes: numberValue(row.n_nodes),
    n_seed: numberValue(row.n_seed), sum_kzt_internal: numberValue(row.sum_kzt_internal),
    top_gids: row.top_gids ?? '', hypothesis: row.hypothesis ?? '',
  }))
  const topNodes: TopRecord[] = topRows.map((row) => ({
    ...row, rank: numberValue(row.rank), gid: row.gid.trim(), role: row.role || 'peripheral',
    priority_score: numberValue(row.priority_score), why: row.why || '',
    priority_why: row.priority_why || row.why || '',
  }))
  return { nodes, edges, clusters, topNodes, source: base }
}
