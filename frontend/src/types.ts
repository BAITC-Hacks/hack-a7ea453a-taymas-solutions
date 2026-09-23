export type Role =
  | 'consolidator'
  | 'transit'
  | 'distributor'
  | 'terminal'
  | 'coordinator'
  | 'peripheral'
  | string

export interface NodeRecord {
  gid: string
  role: Role
  role_score: number
  cluster_id: number
  priority_score: number
  evidence: string
  priority_why: string
  depth: number
  is_seed: boolean
  boundary_depth4: boolean
  in_deg: number
  out_deg: number
  n_payers: number
  n_receivers: number
  in_kzt: number
  out_kzt: number
  [key: string]: string | number | boolean
}

export interface EdgeRecord {
  src: string
  dst: string
  sum_kzt: number
  n_tx: number
  [key: string]: string | number | boolean
}

export interface ClusterRecord {
  cluster_id: number
  n_nodes: number
  n_seed: number
  sum_kzt_internal: number
  top_gids: string
  hypothesis: string
  [key: string]: string | number | boolean
}

export interface TopRecord {
  rank: number
  gid: string
  role: Role
  priority_score: number
  why: string
  priority_why: string
  [key: string]: string | number | boolean
}

export interface GraphData {
  nodes: NodeRecord[]
  edges: EdgeRecord[]
  clusters: ClusterRecord[]
  topNodes: TopRecord[]
  source: string
}

export interface FilterState {
  search: string
  role: string
  cluster: string
  depth: string
  seed: 'all' | 'seed' | 'non-seed'
  topOnly: boolean
  limit: number
}

export const ROLES = [
  'coordinator',
  'consolidator',
  'distributor',
  'transit',
  'terminal',
  'peripheral',
] as const

export const ROLE_COLORS: Record<string, string> = {
  coordinator: 'var(--role-coordinator)',
  consolidator: 'var(--role-consolidator)',
  distributor: 'var(--role-distributor)',
  transit: 'var(--role-transit)',
  terminal: 'var(--role-terminal)',
  peripheral: 'var(--role-peripheral)',
}
