import type { EdgeRecord, NodeRecord } from './types'

/** Directional positions use observed edges only. Coordinates carry no risk meaning. */
export function flowPositions(nodes: NodeRecord[], edges: EdgeRecord[], centerId: string) {
  const incoming = new Map<string, number>()
  const outgoing = new Map<string, number>()
  for (const edge of edges) {
    if (edge.dst === centerId && edge.src !== centerId)
      incoming.set(edge.src, (incoming.get(edge.src) ?? 0) + edge.sum_kzt)
    if (edge.src === centerId && edge.dst !== centerId)
      outgoing.set(edge.dst, (outgoing.get(edge.dst) ?? 0) + edge.sum_kzt)
  }
  const left: NodeRecord[] = [], right: NodeRecord[] = [], other: NodeRecord[] = []
  for (const node of nodes) {
    if (node.gid === centerId) continue
    if (!incoming.has(node.gid) && !outgoing.has(node.gid)) other.push(node)
    else if ((incoming.get(node.gid) ?? 0) >= (outgoing.get(node.gid) ?? 0)) left.push(node)
    else right.push(node)
  }
  const positions: Record<string, { x: number; y: number }> = { [centerId]: { x: 0, y: 0 } }
  const arrange = (group: NodeRecord[], side: number, amounts: Map<string, number>) => {
    group.sort((a, b) => (amounts.get(b.gid) ?? 0) - (amounts.get(a.gid) ?? 0) || a.gid.localeCompare(b.gid))
    const columns = Math.max(1, Math.ceil(group.length / 8))
    const rows = Math.ceil(group.length / columns)
    group.forEach((node, index) => {
      const column = Math.floor(index / rows)
      const rowCount = Math.min(rows, group.length - column * rows)
      positions[node.gid] = {
        x: side * (190 + column * 126),
        y: (index % rows - (rowCount - 1) / 2) * 54 + (column % 2) * 8,
      }
    })
  }
  arrange(left, -1, incoming)
  arrange(right, 1, outgoing)
  const bottom = Math.max(0, ...Object.values(positions).map(p => p.y)) + 90
  other.sort((a, b) => a.gid.localeCompare(b.gid)).forEach((node, index) => {
    const rowCount = Math.min(8, other.length - Math.floor(index / 8) * 8)
    positions[node.gid] = { x: (index % 8 - (rowCount - 1) / 2) * 105, y: bottom + Math.floor(index / 8) * 54 }
  })
  return positions
}
