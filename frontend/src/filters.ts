import type { EdgeRecord, FilterState, NodeRecord } from './types'

export function filterNodes(
  nodes: NodeRecord[],
  filters: FilterState,
  topIds = new Set<string>(),
): NodeRecord[] {
  const needle = filters.search.trim().toLowerCase()
  return nodes.filter((node) => {
    if (needle && !node.gid.toLowerCase().includes(needle)) return false
    if (filters.role !== 'all' && node.role !== filters.role) return false
    if (filters.cluster !== 'all' && String(node.cluster_id) !== filters.cluster) return false
    if (filters.depth !== 'all' && String(node.depth) !== filters.depth) return false
    if (filters.seed === 'seed' && !node.is_seed) return false
    if (filters.seed === 'non-seed' && node.is_seed) return false
    if (filters.topOnly && !topIds.has(node.gid)) return false
    return true
  })
}

export function findNodeByGid(nodes: NodeRecord[], query: string): NodeRecord | undefined {
  const gid = query.trim()
  return gid ? nodes.find((node) => node.gid === gid) : undefined
}

export function capGraph(nodes: NodeRecord[], edges: EdgeRecord[], limit: number) {
  const visible = [...nodes]
    .sort((a, b) => b.priority_score - a.priority_score || a.gid.localeCompare(b.gid))
    .slice(0, limit)
  const ids = new Set(visible.map((node) => node.gid))
  return { nodes: visible, edges: edges.filter((edge) => ids.has(edge.src) && ids.has(edge.dst)) }
}

export function nodeNeighbors(gid: string, edges: EdgeRecord[]) {
  return {
    incoming: edges.filter((edge) => edge.dst === gid).sort((a, b) => b.sum_kzt - a.sum_kzt),
    outgoing: edges.filter((edge) => edge.src === gid).sort((a, b) => b.sum_kzt - a.sum_kzt),
  }
}

export interface FlowSummary {
  sum_kzt: number
  n_tx: number
  counterpart_count: number
}

export interface NodeFlowSummary {
  incoming: FlowSummary
  outgoing: FlowSummary
}

/** Aggregate all visible transaction edges for a node, including tx count. */
export function summarizeNodeFlows(gid: string, edges: EdgeRecord[]): NodeFlowSummary {
  const incoming = edges.filter((edge) => edge.dst === gid)
  const outgoing = edges.filter((edge) => edge.src === gid)
  const summarize = (rows: EdgeRecord[]): FlowSummary => ({
    sum_kzt: rows.reduce((total, edge) => total + edge.sum_kzt, 0),
    n_tx: rows.reduce((total, edge) => total + edge.n_tx, 0),
    counterpart_count: rows.length,
  })
  return { incoming: summarize(incoming), outgoing: summarize(outgoing) }
}

export function isBoundaryNode(node: NodeRecord): boolean {
  return node.boundary_depth4 || node.depth >= 4
}

/** A focused client must remain visible, even when it is below the node cap. */
export function buildGraphView(
  nodes: NodeRecord[],
  edges: EdgeRecord[],
  filters: FilterState,
  topIds: Set<string>,
  selectedId?: string,
  neighborsOnly = false,
) {
  const selected = nodes.find((node) => node.gid === selectedId)
  const exact = findNodeByGid(nodes, filters.search)
  // Exact GID lookup intentionally opens its context independently of facet filters.
  const focus = exact ?? (neighborsOnly ? selected : undefined)
  let candidates = filterNodes(nodes, filters, topIds)
  if (focus) {
    const ids = new Set([focus.gid])
    edges.forEach((edge) => {
      if (edge.src === focus.gid) ids.add(edge.dst)
      if (edge.dst === focus.gid) ids.add(edge.src)
    })
    candidates = nodes.filter((node) => ids.has(node.gid))
  }
  const graph = capGraph(candidates, edges, filters.limit)
  const pinned = focus ?? selected
  // Do not reintroduce a selection excluded by the current filters.
  if (
    pinned &&
    candidates.some((node) => node.gid === pinned.gid) &&
    !graph.nodes.some((node) => node.gid === pinned.gid)
  ) {
    graph.nodes = [pinned, ...graph.nodes.slice(0, Math.max(0, filters.limit - 1))]
    const ids = new Set(graph.nodes.map((node) => node.gid))
    graph.edges = edges.filter((edge) => ids.has(edge.src) && ids.has(edge.dst))
  }
  return { ...graph, total: candidates.length, focused: Boolean(focus), contextId: focus?.gid }
}
