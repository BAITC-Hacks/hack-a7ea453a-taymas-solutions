import { booleanValue, CsvParseError, numberValue, parseCsvTable, type CsvTable } from './csv'
import { ROLES, type ClusterRecord, type EdgeRecord, type GraphData, type NodeRecord, type TopRecord } from './types'

export class DataLoadError extends Error {
  constructor(message: string) { super(message); this.name = 'DataLoadError' }
}

const REQUIRED_COLUMNS = {
  'nodes_roles.csv': ['gid', 'role', 'role_score', 'cluster_id', 'priority_score', 'evidence'],
  'clusters.csv': ['cluster_id', 'n_nodes', 'n_seed', 'sum_kzt_internal', 'top_gids', 'hypothesis'],
  'top_nodes.csv': ['rank', 'gid', 'role', 'priority_score', 'why'],
  'edge_table.csv': ['src', 'dst', 'sum_kzt', 'n_tx'],
}
type FileName = keyof typeof REQUIRED_COLUMNS
type Row = Record<string, string>
type Context = { file: FileName; line: number }
const fail = ({ file, line }: Context, field: string, problem: string): never => {
  throw new DataLoadError(`${file}: строка ${line}, поле «${field}»: ${problem}`)
}
function required(row: Row, field: string, context: Context): string {
  const value = row[field]?.trim()
  if (!value) return fail(context, field, 'значение не заполнено')
  return value
}
function numeric(row: Row, field: string, context: Context, min = 0, max = Number.MAX_VALUE, integer = false): number {
  let value: number
  try { value = numberValue(row[field]) }
  catch (error) { return fail(context, field, error instanceof Error ? error.message : 'некорректное число') }
  if (integer && !Number.isSafeInteger(value)) return fail(context, field, 'ожидается целое число в безопасном диапазоне JavaScript')
  if (value < min || value > max) return fail(context, field, `значение вне диапазона ${min}…${max === Number.MAX_VALUE ? 'конечное положительное число' : max}`)
  return value
}
function integer(row: Row, field: string, context: Context, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  return numeric(row, field, context, min, max, true)
}
function identifier(row: Row, field: string, context: Context): string {
  const value = required(row, field, context)
  // Never pass a gid through Number: distinct int64 IDs can collapse to one JS number.
  if (value.length > 20 || !/^-?(?:0|[1-9]\d*)$/.test(value) || value === '-0' || BigInt(value) < -(2n ** 63n) || BigInt(value) > 2n ** 63n - 1n) {
    return fail(context, field, 'ожидается идентификатор int64, записанный целыми десятичными цифрами')
  }
  return value
}
function role(row: Row, context: Context): string {
  const value = required(row, 'role', context)
  if (!(ROLES as readonly string[]).includes(value)) return fail(context, 'role', `неизвестная роль «${value}»`)
  return value
}
function boolean(row: Row, field: string, context: Context): boolean {
  try { return booleanValue(row[field]) }
  catch (error) { return fail(context, field, error instanceof Error ? error.message : 'некорректный флаг') }
}

// Optional analytical columns may be absent. Only documented undefined metrics may be blank.
const optionalCounts = ['in_deg', 'out_deg', 'n_payers', 'n_receivers', 'n_seed_payers', 'n_seed_receivers', 'in_tx', 'out_tx',
  'downstream_reach', 'n_seed_upstream', 'wcc_id', 'wcc_size', 'sync_payers_max', 'max_tx_per_day', 'active_days', 'n_cycles', 'min_cycle_len',
  'component_id', 'n_edges_internal', 'n_tx_internal', 'hub_in_partners', 'hub_out_partners', 'n_repeat_tx']
const optionalAmounts = ['in_kzt', 'out_kzt', 'avg_in_tx_kzt', 'avg_out_tx_kzt', 'sum_kzt_in_external', 'sum_kzt_out_external',
  'hub_in_kzt', 'hub_out_kzt', 'avg_edge_kzt', 'large_edge_threshold', 'avg_tx_kzt', 'max_tx_kzt', 'hub_out_share']
const optionalScores = ['contrib_collect', 'contrib_fanout', 'contrib_flow', 'contrib_seed', 'contrib_bridge', 'contrib_volume',
  'boundary_factor', 'pagerank', 'betweenness', 'hub_score', 'authority_score', 'transit_share', 'truncated_share', 'stability', 'share_of_src_out', 'share_of_dst_in']
const optionalFlags = ['is_seed', 'boundary_depth4', 'truncated_by_depth', 'out_observable', 'in_underestimated', 'external_inflow_suspected',
  'src_is_seed', 'dst_is_seed', 'is_back_edge', 'is_reciprocal']
const nullableScores = ['top_payer_share', 'top_receiver_share', 'fast_out_share']
const nullableMetrics = ['pass_through', 'pass_through_reliable', 'median_lag_days']
function optionalColumns(row: Row, context: Context): Record<string, string | number | boolean> {
  const result: Record<string, string | number | boolean> = { ...row }
  for (const field of optionalCounts) if (field in row) result[field] = integer(row, field, context)
  for (const field of optionalAmounts) if (field in row) result[field] = numeric(row, field, context)
  for (const field of optionalScores) if (field in row) result[field] = numeric(row, field, context, 0, 1)
  for (const field of optionalFlags) if (field in row) result[field] = boolean(row, field, context)
  for (const field of ['depth', 'src_depth', 'dst_depth', 'min_depth', 'max_depth']) if (field in row) result[field] = integer(row, field, context, 0, 4)
  for (const field of nullableScores) if (field in row && row[field].trim()) result[field] = numeric(row, field, context, 0, 1)
  for (const field of nullableMetrics) if (field in row && row[field].trim()) result[field] = numeric(row, field, context)
  for (const field of ['hub_in_gid', 'hub_out_gid']) if (field in row) result[field] = identifier(row, field, context)
  return result
}
function node(row: Row, context: Context): NodeRecord {
  const extra = optionalColumns(row, context)
  const evidence = required(row, 'evidence', context)
  if (Array.from(evidence).length > 200) fail(context, 'evidence', 'обоснование длиннее 200 символов')
  const optionalNumber = (field: string, alias?: string) => (extra[field] ?? (alias ? extra[alias] : undefined) ?? 0) as number
  const depth = optionalNumber('depth')
  return {
    ...extra, gid: identifier(row, 'gid', context), role: role(row, context),
    role_score: numeric(row, 'role_score', context, 0, 1), cluster_id: integer(row, 'cluster_id', context),
    priority_score: numeric(row, 'priority_score', context, 0, 1), evidence,
    priority_why: row.priority_why?.trim() || row.why?.trim() || 'Приоритет рассчитан структурными признаками графа',
    depth, is_seed: (extra.is_seed ?? false) as boolean,
    boundary_depth4: Boolean(extra.boundary_depth4) || depth >= 4,
    in_deg: optionalNumber('in_deg', 'n_payers'), out_deg: optionalNumber('out_deg', 'n_receivers'),
    n_payers: optionalNumber('n_payers', 'in_deg'), n_receivers: optionalNumber('n_receivers', 'out_deg'),
    in_kzt: optionalNumber('in_kzt'), out_kzt: optionalNumber('out_kzt'),
  }
}
function edge(row: Row, context: Context): EdgeRecord {
  const sum = numeric(row, 'sum_kzt', context)
  if (sum === 0) fail(context, 'sum_kzt', 'сумма перевода должна быть больше нуля')
  return { ...optionalColumns(row, context), src: identifier(row, 'src', context), dst: identifier(row, 'dst', context), sum_kzt: sum, n_tx: integer(row, 'n_tx', context, 1) }
}
function cluster(row: Row, context: Context): ClusterRecord {
  return { ...optionalColumns(row, context), cluster_id: integer(row, 'cluster_id', context), n_nodes: integer(row, 'n_nodes', context, 1),
    n_seed: integer(row, 'n_seed', context), sum_kzt_internal: numeric(row, 'sum_kzt_internal', context),
    top_gids: required(row, 'top_gids', context), hypothesis: required(row, 'hypothesis', context) }
}
function top(row: Row, context: Context): TopRecord {
  const why = required(row, 'why', context)
  return { ...optionalColumns(row, context), rank: integer(row, 'rank', context, 1), gid: identifier(row, 'gid', context),
    role: role(row, context), priority_score: numeric(row, 'priority_score', context, 0, 1), why, priority_why: row.priority_why?.trim() || why }
}

async function getCsv(base: string, file: FileName): Promise<CsvTable> {
  const response = await fetch(`${base.replace(/\/$/, '')}/${file}`)
  if (!response.ok) throw new DataLoadError(`${file}: сервер вернул ${response.status}`)
  let table: CsvTable
  try { table = parseCsvTable(await response.text()) }
  catch (error) {
    if (error instanceof CsvParseError) throw new DataLoadError(`${file}: строка ${error.line}${error.field ? `, поле «${error.field}»` : ''}: ${error.message}`)
    throw new DataLoadError(`${file}: не удалось прочитать CSV`)
  }
  if (!table.headers.length) throw new DataLoadError(`${file}: пустой файл`)
  const missing = REQUIRED_COLUMNS[file].filter(column => !table.headers.includes(column))
  if (missing.length) throw new DataLoadError(`${file}: отсутствуют обязательные колонки: ${missing.join(', ')}`)
  if (!table.rows.length && file !== 'edge_table.csv') throw new DataLoadError(`${file}: пустой файл`)
  return table
}

export async function loadData(base = '/out'): Promise<GraphData> {
  const [nodesCsv, clustersCsv, topCsv, edgesCsv] = await Promise.all([
    getCsv(base, 'nodes_roles.csv'), getCsv(base, 'clusters.csv'), getCsv(base, 'top_nodes.csv'), getCsv(base, 'edge_table.csv'),
  ])
  const context = (file: FileName, table: CsvTable, index: number): Context => ({ file, line: table.lines[index] })
  const nodes = nodesCsv.rows.map((row, index) => node(row, context('nodes_roles.csv', nodesCsv, index)))
  const edges = edgesCsv.rows.map((row, index) => edge(row, context('edge_table.csv', edgesCsv, index)))
  const clusters = clustersCsv.rows.map((row, index) => cluster(row, context('clusters.csv', clustersCsv, index)))
  const topNodes = topCsv.rows.map((row, index) => top(row, context('top_nodes.csv', topCsv, index)))
  const byId = new Map<string, NodeRecord>(), members = new Map<number, NodeRecord[]>(), clusterById = new Map<number, ClusterRecord>()
  nodes.forEach((item, index) => {
    if (byId.has(item.gid)) fail(context('nodes_roles.csv', nodesCsv, index), 'gid', `идентификатор ${item.gid} повторяется`)
    byId.set(item.gid, item)
    const group = members.get(item.cluster_id) ?? []; group.push(item); members.set(item.cluster_id, group)
  })
  clusters.forEach((item, index) => {
    if (clusterById.has(item.cluster_id)) fail(context('clusters.csv', clustersCsv, index), 'cluster_id', 'номер кластера повторяется')
    clusterById.set(item.cluster_id, item)
  })
  nodes.forEach((item, index) => {
    if (!clusterById.has(item.cluster_id)) fail(context('nodes_roles.csv', nodesCsv, index), 'cluster_id', `кластер ${item.cluster_id} отсутствует в clusters.csv`)
  })
  const edgeKeys = new Set<string>(), internalSums = new Map<number, number>()
  edges.forEach((item, index) => {
    const ctx = context('edge_table.csv', edgesCsv, index)
    for (const field of ['src', 'dst'] as const) if (!byId.has(item[field])) fail(ctx, field, `узел ${item[field]} отсутствует в nodes_roles.csv`)
    const key = `${item.src}:${item.dst}`
    if (edgeKeys.has(key)) fail(ctx, 'src/dst', 'направленная пара повторяется; ожидается одно агрегированное ребро')
    edgeKeys.add(key)
    const srcCluster = byId.get(item.src)!.cluster_id
    if (srcCluster === byId.get(item.dst)!.cluster_id) internalSums.set(srcCluster, (internalSums.get(srcCluster) ?? 0) + item.sum_kzt)
  })
  clusters.forEach((item, index) => {
    const ctx = context('clusters.csv', clustersCsv, index), group = members.get(item.cluster_id) ?? []
    if (item.n_nodes !== group.length) fail(ctx, 'n_nodes', `указано ${item.n_nodes}, в nodes_roles.csv найдено ${group.length}`)
    if (item.n_seed > item.n_nodes || (nodesCsv.headers.includes('is_seed') && item.n_seed !== group.filter(member => member.is_seed).length)) fail(ctx, 'n_seed', 'число seed не согласовано с узлами кластера')
    const sum = internalSums.get(item.cluster_id) ?? 0
    if (!Number.isFinite(sum)) fail(ctx, 'sum_kzt_internal', 'суммирование рёбер превышает конечный числовой диапазон')
    if (Math.abs(item.sum_kzt_internal - sum) > .01 + Number.EPSILON * Math.max(sum, item.sum_kzt_internal) * Math.max(edges.length, 1)) {
      fail(ctx, 'sum_kzt_internal', `сумма не совпадает с внутренними рёбрами (${sum.toFixed(2)} KZT)`)
    }
    const topIds = item.top_gids.split(';').map(gid => identifier({ top_gids: gid }, 'top_gids', ctx))
    if (new Set(topIds).size !== topIds.length) fail(ctx, 'top_gids', 'идентификаторы повторяются')
    for (const gid of topIds) if (byId.get(gid)?.cluster_id !== item.cluster_id) fail(ctx, 'top_gids', `узел ${gid} не принадлежит этому кластеру`)
    for (const field of ['hub_in_gid', 'hub_out_gid']) if (field in item && byId.get(String(item[field]))?.cluster_id !== item.cluster_id) fail(ctx, field, 'узел не принадлежит этому кластеру')
  })
  const topIds = new Set<string>()
  topNodes.forEach((item, index) => {
    const ctx = context('top_nodes.csv', topCsv, index), source = byId.get(item.gid)
    if (!source) fail(ctx, 'gid', `узел ${item.gid} отсутствует в nodes_roles.csv`)
    if (topIds.has(item.gid)) fail(ctx, 'gid', 'узел повторяется в топ-листе')
    topIds.add(item.gid)
    if (item.rank !== index + 1) fail(ctx, 'rank', 'ожидается последовательная нумерация от 1')
    if (item.role !== source!.role) fail(ctx, 'role', 'роль не совпадает с nodes_roles.csv')
    if (Math.abs(item.priority_score - source!.priority_score) > .000001) fail(ctx, 'priority_score', 'приоритет не совпадает с nodes_roles.csv')
    if (index && item.priority_score > topNodes[index - 1].priority_score) fail(ctx, 'priority_score', 'топ-лист должен быть упорядочен по убыванию приоритета')
  })
  const priorityWhy = new Map(topNodes.map(item => [item.gid, item.why || item.priority_why]))
  return { nodes: nodes.map(item => ({ ...item, priority_why: priorityWhy.get(item.gid) || item.priority_why })), edges, clusters, topNodes, source: base }
}
