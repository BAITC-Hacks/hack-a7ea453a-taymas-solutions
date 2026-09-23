import { validateAnswer, type Claim, type CopilotAnswer } from '../copilot/api'
import { buildDataRequestPlan } from '../investigation/dataRequestPlan'
import type { GraphData } from '../types'

export const STORAGE_KEY = 'taymas.casebook.v1'
export const MAX_CASE_BYTES = 4_000_000
export const MAX_ITEMS = 100
export const MAX_NOTE_LENGTH = 20_000
export const SCOPE = 'Гипотезы для проверки, не выводы о виновности. Обход до 4-го колена, порог 5 000 KZT, даты с точностью до дня. Входящие извне выборки не видны.'

export interface DatasetVersion { id: string; node_count: number; edge_count: number }
export interface CaseItem {
  id: string; kind: 'node' | 'copilot'; title: string; created_at: string
  dataset: DatasetVersion; gids: string[]; claims: Claim[]
  hypothesis: string; warnings: CopilotAnswer['warnings']; next_steps: string[]
}
export interface CaseFile {
  schema_version: 1; id: string; title: string; created_at: string; updated_at: string
  hypothesis: string; notes: string; items: CaseItem[]
}
export type SnapshotStatus = 'current' | 'stale' | 'unavailable' | 'unverified'
export const STATUS_LABELS: Record<SnapshotStatus, string> = {
  current: 'Факты сверены с текущими CSV', stale: 'Снимок другой выгрузки',
  unavailable: 'Текущая выгрузка недоступна', unverified: 'Факты не совпадают с CSV',
}

/** Stable across row/property order and URLs; the digest covers every loaded CSV column. */
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key =>
    `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(',')}}`
  return JSON.stringify(value)
}
export async function fingerprintData(data: GraphData): Promise<DatasetVersion> {
  const rows = (items: object[]) => items.map(canonical).sort()
  const bytes = new TextEncoder().encode(canonical({
    nodes: rows(data.nodes), edges: rows(data.edges), clusters: rows(data.clusters), top: rows(data.topNodes),
  }))
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  return { id: `sha256:${hex}`, node_count: data.nodes.length, edge_count: data.edges.length }
}

export function newCase(): CaseFile {
  const now = new Date().toISOString()
  return { schema_version: 1, id: crypto.randomUUID(), title: 'Дело расследования',
    created_at: now, updated_at: now, hypothesis: '', notes: '', items: [] }
}

const NODE_FIELDS = ['role', 'priority_score', 'in_kzt', 'out_kzt', 'n_payers', 'n_receivers',
  'depth', 'is_seed', 'role_score', 'in_tx', 'out_tx', 'n_seed_payers', 'n_seed_receivers',
  'contrib_collect', 'contrib_fanout', 'contrib_flow', 'contrib_seed', 'contrib_bridge', 'contrib_volume', 'boundary_factor']

export function captureNode(data: GraphData, dataset: DatasetVersion, gid: string): CaseItem {
  const node = data.nodes.find(n => n.gid === gid)
  if (!node) throw new Error('Узел отсутствует в текущей выгрузке.')
  const plan = buildDataRequestPlan(data, gid)
  const warnings: CaseItem['warnings'] = plan.items.map(item => ({ code: item.kind, gid, message: item.limitation }))
  if (node.depth >= 4) warnings.push({ code: 'depth4_outflow_unobserved', gid,
    message: '4-е колено: исходящие не наблюдаются; отсутствие оттока не доказывает оседание.' })
  return {
    id: crypto.randomUUID(), kind: 'node', title: `Клиент ${gid}`, created_at: new Date().toISOString(),
    dataset: { ...dataset }, gids: [gid],
    claims: NODE_FIELDS.filter(field => node[field] !== undefined).map(field => ({
      kind: 'node', gid, field, value: node[field], source: { file: 'nodes_roles.csv', gid, column: field },
    })),
    hypothesis: `Роль в выгрузке: ${node.role}. Основание роли: ${node.evidence}\nОснование приоритета: ${node.priority_why}`,
    warnings, next_steps: plan.items.map(item => item.request.purpose),
  }
}

export function captureAnswer(data: GraphData, dataset: DatasetVersion, answer: CopilotAnswer, question: string): CaseItem {
  const verified = validateAnswer(answer, data)
  return {
    id: crypto.randomUUID(), kind: 'copilot', title: question.trim() || 'Ответ Copilot',
    created_at: new Date().toISOString(), dataset: { ...dataset }, gids: [...verified.gids],
    claims: structuredClone(verified.claims), hypothesis: verified.summary,
    warnings: structuredClone(verified.warnings), next_steps: [...verified.next_steps],
  }
}

/** An imported verification label is never trusted. Check claims against the visible data. */
export function snapshotStatus(item: CaseItem, data: GraphData | null, version: DatasetVersion | null): SnapshotStatus {
  if (!data || !version) return 'unavailable'
  if (item.dataset.id !== version.id) return 'stale'
  if (item.dataset.node_count !== version.node_count || item.dataset.edge_count !== version.edge_count) return 'unverified'
  try {
    validateAnswer({ status: 'ok', provider: 'fallback', verification: 'passed',
      summary: 'Гипотеза для проверки.', gids: item.gids, claims: item.claims, candidates: [],
      evidence: item.claims.map((_, claim_index) => ({ claim_index })), warnings: item.warnings,
      next_steps: [], tool_calls: [], fallback_reason: null, error: null }, data)
    return 'current'
  } catch { return 'unverified' }
}

function fail(message: string): never { throw new Error(`Файл дела: ${message}`) }
function object(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('ожидался объект.')
  const obj = value as Record<string, unknown>
  if (Object.keys(obj).some(key => !keys.includes(key))) fail('неизвестные поля схемы.')
  return obj
}
function string(value: unknown, max: number, name: string, empty = false): string {
  if (typeof value !== 'string' || value.length > max || (!empty && !value.trim())) fail(`некорректное поле «${name}».`)
  return value
}
function id(value: unknown): string { return string(value, 100, 'id') }
function gid(value: unknown): string {
  if (typeof value !== 'string' || !/^\d{1,19}$/.test(value)) fail('gid должен быть строкой из 1–19 цифр.')
  return value
}
function date(value: unknown): string {
  const text = string(value, 30, 'дата')
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(text) ||
    !Number.isFinite(Date.parse(text)) || new Date(text).toISOString() !== text) fail('некорректная дата.')
  return text
}
function list<T>(value: unknown, max: number, convert: (entry: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length > max) fail(`ожидался список, не более ${max} элементов.`)
  return value.map(convert)
}
function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) fail('некорректный размер набора.')
  return value
}
function version(value: unknown): DatasetVersion {
  const d = object(value, ['id', 'node_count', 'edge_count'])
  if (typeof d.id !== 'string' || !/^sha256:[a-f0-9]{64}$/.test(d.id)) fail('неверный отпечаток набора данных.')
  return { id: d.id, node_count: count(d.node_count), edge_count: count(d.edge_count) }
}
function claim(raw: unknown): Claim {
  const c = object(raw, ['kind', 'gid', 'src', 'dst', 'field', 'value', 'source'])
  const s = object(c.source, ['file', 'column', 'gid', 'src', 'dst'])
  const field = string(c.field, 80, 'поле факта')
  if (!/^[a-z][a-z0-9_]*$/.test(field) || ['constructor', 'prototype', 'api_key', 'token', 'password', 'secret'].includes(field)) fail('неизвестное поле факта.')
  if (s.column !== field) fail('источник не соответствует полю факта.')
  const value = c.value
  if (!['string', 'number', 'boolean'].includes(typeof value) ||
    (typeof value === 'number' && !Number.isFinite(value)) || (typeof value === 'string' && value.length > 4000)) fail('неверное значение факта.')
  if (c.kind === 'node') {
    const g = gid(c.gid)
    if (s.file !== 'nodes_roles.csv' || s.gid !== g || c.src !== undefined || c.dst !== undefined || s.src !== undefined || s.dst !== undefined) fail('неверная ссылка на узел.')
    return { kind: 'node', gid: g, field, value: value as Claim['value'], source: { file: s.file, gid: g, column: field } }
  }
  if (c.kind === 'edge') {
    const src = gid(c.src), dst = gid(c.dst)
    if (s.file !== 'edge_table.csv' || s.src !== src || s.dst !== dst || c.gid !== undefined || s.gid !== undefined) fail('неверная ссылка на связь.')
    return { kind: 'edge', src, dst, field, value: value as Claim['value'], source: { file: s.file, src, dst, column: field } }
  }
  return fail('неизвестный тип факта.')
}
function item(value: unknown): CaseItem {
  const i = object(value, ['id', 'kind', 'title', 'created_at', 'dataset', 'gids', 'claims', 'hypothesis', 'warnings', 'next_steps'])
  if (i.kind !== 'node' && i.kind !== 'copilot') fail('неизвестный тип материала.')
  const gids = list(i.gids, 1000, gid), claims = list(i.claims, 1000, claim)
  if (!gids.length || !claims.length || new Set(gids).size !== gids.length) fail('материал должен содержать уникальные gid и факты.')
  if (claims.some(c => (c.kind === 'node' ? [c.gid!] : [c.src!, c.dst!]).some(g => !gids.includes(g)))) fail('в фактах есть gid вне списка материала.')
  const warnings = list(i.warnings, 1000, raw => {
    const w = object(raw, ['code', 'message', 'gid'])
    const g = w.gid === undefined ? undefined : gid(w.gid)
    if (g && !gids.includes(g)) fail('предупреждение ссылается на неизвестный gid.')
    return { code: string(w.code, 80, 'код ограничения'), message: string(w.message, 1000, 'ограничение'), ...(g ? { gid: g } : {}) }
  })
  return { id: id(i.id), kind: i.kind, title: string(i.title, 1000, 'название материала'), created_at: date(i.created_at),
    dataset: version(i.dataset), gids, claims, hypothesis: string(i.hypothesis, 4000, 'гипотеза', true),
    warnings, next_steps: list(i.next_steps, 1000, v => string(v, 1000, 'следующий шаг')) }
}

/** Strict, versioned import. Construct an allow-listed object; never merge untrusted keys. */
export function parseCase(text: string): CaseFile {
  if (new TextEncoder().encode(text).byteLength > MAX_CASE_BYTES) fail('размер превышает 4 МБ.')
  let raw: unknown
  try { raw = JSON.parse(text) } catch { return fail('повреждённый JSON.') }
  const c = object(raw, ['schema_version', 'id', 'title', 'created_at', 'updated_at', 'hypothesis', 'notes', 'items'])
  if (c.schema_version !== 1) fail('неподдерживаемая версия схемы. Нужна версия 1.')
  const items = list(c.items, MAX_ITEMS, item)
  if (new Set(items.map(i => i.id)).size !== items.length) fail('повторяющиеся id материалов.')
  return { schema_version: 1, id: id(c.id), title: string(c.title, 200, 'название', true),
    created_at: date(c.created_at), updated_at: date(c.updated_at),
    hypothesis: string(c.hypothesis, MAX_NOTE_LENGTH, 'рабочая гипотеза', true),
    notes: string(c.notes, MAX_NOTE_LENGTH, 'заметки', true), items }
}
export function serializeCase(value: CaseFile): string {
  const text = JSON.stringify(parseCase(JSON.stringify(value)), null, 2)
  if (new TextEncoder().encode(text).byteLength > MAX_CASE_BYTES) fail('размер превышает 4 МБ.')
  return text
}
export function addItem(file: CaseFile, incoming: CaseItem): CaseFile {
  const equivalent = (i: CaseItem) => i.kind === incoming.kind && i.dataset.id === incoming.dataset.id &&
    (i.kind === 'node' ? i.gids[0] === incoming.gids[0] :
      i.title === incoming.title && canonical([i.claims, i.hypothesis, i.warnings, i.next_steps]) === canonical([incoming.claims, incoming.hypothesis, incoming.warnings, incoming.next_steps]))
  if (file.items.some(equivalent)) return file
  if (file.items.length >= MAX_ITEMS) throw new Error('В одном деле не более 100 материалов. Сохраните JSON и начните новое дело.')
  return { ...file, updated_at: new Date().toISOString(), items: [...file.items, incoming] }
}
