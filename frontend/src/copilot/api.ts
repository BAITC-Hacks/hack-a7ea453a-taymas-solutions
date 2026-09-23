import type { GraphData } from '../types'

export const MAX_QUESTION = 1000
export const MAX_RESPONSE_BYTES = 100_000
export const REQUEST_TIMEOUT = 15_000

export interface CopilotRequest { question: string; selected_gids: string[]; use_nvidia: boolean }
export interface Source { file: 'nodes_roles.csv' | 'edge_table.csv'; column: string; gid?: string; src?: string; dst?: string }
export interface Claim { kind: 'node' | 'edge'; gid?: string; src?: string; dst?: string; field: string; value: string | number | boolean; source: Source }
export interface CopilotAnswer {
  status: 'ok' | 'empty' | 'error'; provider: 'fallback' | 'nvidia'; summary: string
  gids: string[]; claims: Claim[]; candidates: { gid: string; role: string; priority_score: number }[]
  evidence: { claim_index: number }[]; warnings: { code: string; message: string; gid?: string }[]
  next_steps: string[]; tool_calls: { tool: string }[]; verification: string
  fallback_reason: string | null; error: { code: string; message: string } | null
}
export interface Availability { ready: boolean; nvidia_available: boolean }

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Некорректный ответ помощника.')
  return value as Record<string, unknown>
}
function text(value: unknown, max = 4000): string {
  if (typeof value !== 'string' || value.length > max) throw new Error('Некорректный текст ответа.')
  return value
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value) || value.length > 1000) throw new Error('Некорректный список фактов.')
  return value
}
function equalFact(expected: unknown, actual: unknown, field: string): boolean {
  if (typeof actual === 'number') {
    if ((typeof expected !== 'number' && typeof expected !== 'string') || expected === '') return false
    return Number.isFinite(actual) && Number.isFinite(Number(expected)) &&
      Math.abs(Number(expected) - actual) <= (field.includes('kzt') ? 0.01 : 0.000001)
  }
  if (typeof actual === 'boolean') return expected === actual || expected === String(actual) || expected === (actual ? 'True' : 'False')
  return typeof actual === 'string' && expected === actual
}

/** Validate before rendering or highlighting, including consistency with the CSVs on screen. */
export function validateAnswer(raw: unknown, data: GraphData): CopilotAnswer {
  const a = record(raw)
  if (!['ok', 'empty', 'error'].includes(String(a.status)) || !['fallback', 'nvidia'].includes(String(a.provider))) {
    throw new Error('Неизвестный формат ответа помощника.')
  }
  if (a.status === 'error') {
    const error = record(a.error)
    throw new Error(text(error.message, 500))
  }
  if (a.verification !== 'passed') throw new Error('Факты не прошли проверку. Ответ скрыт; граф доступен.')
  const nodes = new Map(data.nodes.map(n => [n.gid, n]))
  const edges = new Map(data.edges.map(e => [`${e.src}:${e.dst}`, e]))
  const gid = (value: unknown): string => {
    if (typeof value !== 'string' || !nodes.has(value)) throw new Error('Узел ответа отсутствует в текущей выгрузке. Обновите данные.')
    return value
  }
  const gids = list(a.gids).map(gid)
  const claims: Claim[] = list(a.claims).map(rawClaim => {
    const c = record(rawClaim), source = record(c.source), field = text(c.field, 80)
    let ids: { gid: string } | { src: string; dst: string }
    let expected: unknown
    if (c.kind === 'node') {
      ids = { gid: gid(c.gid) }
      if (source.file !== 'nodes_roles.csv' || source.gid !== ids.gid) throw new Error('Неверная ссылка на узел.')
      expected = nodes.get(ids.gid)?.[field]
    } else if (c.kind === 'edge') {
      ids = { src: gid(c.src), dst: gid(c.dst) }
      if (source.file !== 'edge_table.csv' || source.src !== ids.src || source.dst !== ids.dst) throw new Error('Неверная ссылка на перевод.')
      expected = edges.get(`${ids.src}:${ids.dst}`)?.[field]
    } else throw new Error('Неподдерживаемый факт.')
    if (source.column !== field || expected === undefined || !equalFact(expected, c.value, field)) {
      throw new Error('Факты не совпадают с текущей выгрузкой. Обновите данные и повторите вопрос.')
    }
    if (Object.values(ids).some(id => !gids.includes(id))) throw new Error('Неполный список узлов ответа.')
    return { kind: c.kind, ...ids, field, value: c.value as Claim['value'], source: { file: source.file, ...ids, column: field } as Source }
  })
  if (!gids.length || !claims.length) throw new Error('В ответе нет подтверждённых фактов.')
  const candidates = list(a.candidates).map(rawCandidate => {
    const c = record(rawCandidate), id = gid(c.gid), node = nodes.get(id)!
    if (!gids.includes(id) || c.role !== node.role || !equalFact(node.priority_score, c.priority_score, 'priority_score')) throw new Error('Кандидат не подтверждён данными.')
    return { gid: id, role: node.role, priority_score: node.priority_score }
  })
  if (candidates.length > 5) throw new Error('Слишком много кандидатов.')
  const evidence = list(a.evidence).map(rawEvidence => {
    const index = record(rawEvidence).claim_index
    if (typeof index !== 'number' || !Number.isInteger(index) || index < 0 || index >= claims.length) throw new Error('Неверная ссылка на факт.')
    return { claim_index: index }
  })
  const warnings = list(a.warnings).map(rawWarning => {
    const w = record(rawWarning)
    return { code: text(w.code, 80), message: text(w.message, 1000), ...(w.gid === undefined ? {} : { gid: gid(w.gid) }) }
  })
  for (const id of gids) {
    if (nodes.get(id)!.depth >= 4 && !warnings.some(w => w.code === 'depth4_outflow_unobserved' && w.gid === id)) {
      throw new Error('В ответе не учтена граница обхода. Факты скрыты.')
    }
  }
  const summary = text(a.summary)
  if (!summary.toLowerCase().includes('гипотеза для проверки')) throw new Error('Ответ не помечен как гипотеза для проверки.')
  return { status: a.status as 'ok' | 'empty', provider: a.provider as CopilotAnswer['provider'], summary, gids, claims, candidates, evidence,
    warnings, next_steps: list(a.next_steps).map(v => text(v, 1000)),
    tool_calls: list(a.tool_calls).map(v => ({ tool: text(record(v).tool, 80) })), verification: 'passed',
    fallback_reason: a.fallback_reason == null ? null : text(a.fallback_reason, 80), error: null }
}

async function boundedJson(response: Response, limit: number): Promise<unknown> {
  if (!response.body || Number(response.headers.get('content-length')) > limit) throw new Error('Ответ помощника слишком большой.')
  const reader = response.body.getReader(), chunks: Uint8Array[] = []
  let size = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > limit) throw new Error('Ответ помощника слишком большой. Уточните вопрос.')
      chunks.push(value)
    }
  } finally { await reader.cancel(); reader.releaseLock() }
  const bytes = new Uint8Array(size)
  let offset = 0
  chunks.forEach(chunk => { bytes.set(chunk, offset); offset += chunk.length })
  try { return JSON.parse(new TextDecoder().decode(bytes)) }
  catch { throw new Error('Помощник вернул некорректный ответ. Попробуйте ещё раз.') }
}

export async function askCopilot(request: CopilotRequest, data: GraphData, signal: AbortSignal): Promise<CopilotAnswer> {
  if (!request.question.trim() || request.question.length > MAX_QUESTION || request.selected_gids.length > 5) throw new Error('Проверьте длину вопроса и выбранные узлы.')
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT)
  let response: Response
  try {
    response = await fetch('/api/copilot/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request), signal: AbortSignal.any([signal, timeout]) })
    if (!response.ok) throw new Error(response.status === 503 ? 'Помощник занят. Повторите запрос.' : 'Помощник недоступен. Попробуйте ещё раз; граф и фильтры доступны.')
    return validateAnswer(await boundedJson(response, MAX_RESPONSE_BYTES), data)
  } catch (error) {
    if (timeout.aborted) throw new Error('Время ожидания истекло. Повторите вопрос; граф и фильтры доступны.')
    if (error instanceof TypeError) throw new Error('Нет соединения с помощником. Граф и фильтры доступны.')
    throw error
  }
}

export async function copilotStatus(signal: AbortSignal): Promise<Availability> {
  const response = await fetch('/api/copilot/status', { signal: AbortSignal.any([signal, AbortSignal.timeout(3000)]) })
  if (!response.ok) throw new Error('unavailable')
  const raw = record(await boundedJson(response, 1024))
  if (typeof raw.ready !== 'boolean' || typeof raw.nvidia_available !== 'boolean') throw new Error('invalid status')
  return { ready: raw.ready, nvidia_available: raw.nvidia_available }
}
