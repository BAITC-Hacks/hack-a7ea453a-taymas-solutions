import type { EdgeRecord, GraphData, NodeRecord } from '../types'
import type { CopilotAnswer } from './api'

export const edgeKey = (src: string, dst: string) => `${src}:${dst}`

export function answerHighlights(answer: CopilotAnswer | null) {
  return { gids: answer?.gids ?? [], edges: [...new Set(answer?.claims
    .filter(c => c.kind === 'edge').map(c => edgeKey(c.src!, c.dst!)) ?? [])] }
}

/** Keep the user's filtered graph; add evidence nodes explicitly, never silently drop them. */
export function includeEvidence(graph: { nodes: NodeRecord[]; edges: EdgeRecord[] }, data: GraphData, gids: string[], focused?: string) {
  const pinned = new Set([...gids, ...(focused ? [focused] : [])])
  const visible = new Set(graph.nodes.map(n => n.gid))
  const extra = data.nodes.filter(n => pinned.has(n.gid) && !visible.has(n.gid))
  extra.forEach(n => visible.add(n.gid))
  return { nodes: [...graph.nodes, ...extra], edges: data.edges.filter(e => visible.has(e.src) && visible.has(e.dst)), extraCount: extra.length }
}
