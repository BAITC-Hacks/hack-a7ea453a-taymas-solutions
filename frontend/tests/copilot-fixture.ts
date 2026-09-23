import type { GraphData, NodeRecord } from '../src/types'
import type { CopilotAnswer } from '../src/copilot/api'

export const A = '9007199254741009', B = '9007199254741011'
const node = (gid: string): NodeRecord => ({ gid, role: 'consolidator', role_score: .8, cluster_id: 1, priority_score: .7,
  evidence: '2 плательщика', priority_why: 'сбор 0.2', depth: 1, is_seed: false, boundary_depth4: false,
  in_deg: 2, out_deg: 1, n_payers: 2, n_receivers: 1, in_kzt: 15000, out_kzt: 5000, in_tx: 3, out_tx: 1 })
export const data: GraphData = { source: '/out', nodes: [node(A), node(B)], edges: [{ src: A, dst: B, sum_kzt: 5000, n_tx: 1 }],
  clusters: [], topNodes: [{ gid: A, rank: 1, role: 'consolidator', priority_score: .7, why: 'сбор 0.2', priority_why: 'сбор 0.2' }] }
export const brief: CopilotAnswer = { status: 'ok', provider: 'fallback', verification: 'passed',
  summary: 'Приоритет узла 0.7. Это гипотеза для проверки.', gids: [A],
  candidates: [{ gid: A, role: 'consolidator', priority_score: .7 }],
  claims: [{ kind: 'node', gid: A, field: 'in_kzt', value: 15000, source: { file: 'nodes_roles.csv', gid: A, column: 'in_kzt' } }],
  evidence: [{ claim_index: 0 }], warnings: [], next_steps: ['Запросить выписку.'], tool_calls: [{ tool: 'get_node' }],
  fallback_reason: null, error: null }
