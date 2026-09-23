import type { GraphData } from '../types'

/** Предвычисленный эксперимент PAN-44 (analytics/resilience_export.py, schema resilience/v1).
 *  Все показанные числа берутся из этого файла; браузер пересчитывает только фрагменты
 *  для раскраски графа и сверяет их с файлом. null — метрика неприменима («не определено»). */
export type Metric = number | null
export type Metrics = Record<string, Metric>
export interface ControlStat { mean: Metric; q05: Metric; q95: Metric; n_trials: number; n_valid: number }
export interface Comparison {
  priority: Metric; reference_mean: Metric; priority_minus_reference: Metric
  reference_fraction_ge_priority: Metric; n_valid_reference: number
}
export type Deterministic = 'priority' | 'degree'
export type Control = 'random' | 'matched_random'
export interface ResiliencePayload {
  schema: 'resilience/v1'
  generated_at: string
  sources: Record<string, { sha256?: string }> & { graph: { n_nodes: number; n_edges: number; total_kzt: number } }
  parameters: {
    steps: number[]; random_runs: number; seed: number; networkx_version: string; ranking: string
    matched_on: string[]; adaptive_ranking: boolean; headline_metrics: string[]
  }
  timing_sec: { read_inputs?: number; experiment: number; total: number }
  baseline: Metrics
  deterministic: Record<Deterministic, Record<string, Metrics>>
  controls: Record<Control, Record<string, Record<string, ControlStat>>>
  comparisons: Record<string, Record<string, Record<string, Comparison>>>
  removals: Record<Deterministic, string[]>
  interpretation: string
  caveats: string[]
}
export type LoadResult =
  | { status: 'ok'; payload: ResiliencePayload }
  | { status: 'missing'; reason: string }
  | { status: 'invalid'; reason: string }

export const FILE = 'resilience.json'
export const RECOMPUTE = 'python -m analytics.resilience_export --out out'
export const HEADLINE = ['weak_pair_loss', 'seed_reach_loss', 'removed_kzt_share'] as const
export const METRIC_LABELS: Record<string, string> = {
  weak_pair_loss: 'Потеря связности пар',
  seed_reach_loss: 'Потеря достижимости от seed',
  removed_kzt_share: 'Оборот удалённых рёбер',
  largest_weak_size: 'Крупнейшая компонента, узлов',
  weak_components: 'Компонент связности',
  new_isolates: 'Новые изоляты',
  n_remaining: 'Осталось узлов',
  n_edges_remaining: 'Осталось рёбер',
  largest_strong_size: 'Крупнейшая сильная компонента',
}
export const STRATEGY_LABELS: Record<string, string> = {
  priority: 'По приоритету', degree: 'По числу связей (degree)',
  random: 'Случайно', matched_random: 'Случайно, тот же состав seed/колен',
}

const isGid = (value: unknown) => typeof value === 'string' && /^\d+$/.test(value)

function validate(raw: unknown): string | undefined {
  const p = raw as Partial<ResiliencePayload>
  if (!p || typeof p !== 'object') return 'файл не является объектом'
  if (p.schema !== 'resilience/v1') return `неизвестная схема ${String(p.schema)}`
  const steps = p.parameters?.steps
  if (!Array.isArray(steps) || !steps.includes(0)) return 'нет списка N с нулевым шагом'
  for (const s of ['priority', 'degree'] as const) {
    if (!steps.every(n => p.deterministic?.[s]?.[String(n)])) return `нет сценариев ${s} для всех N`
    const order = p.removals?.[s]
    if (!Array.isArray(order) || !order.every(isGid)) return `порядок удаления ${s}: gid должны быть строками`
    if (order.length < Math.max(...steps)) return `порядок удаления ${s} короче максимального N`
  }
  for (const s of ['random', 'matched_random'] as const)
    if (!steps.filter(n => n > 0).every(n => p.controls?.[s]?.[String(n)])) return `нет контроля ${s} для всех N`
  if (!p.baseline || !p.sources?.graph) return 'нет исходных метрик или описания графа'
  return undefined
}

export async function loadResilience(base: string): Promise<LoadResult> {
  let response: Response
  try {
    response = await fetch(`${base.replace(/\/$/, '')}/${FILE}`, { cache: 'no-store' })
  } catch {
    return { status: 'missing', reason: 'файл недоступен' }
  }
  if (response.status === 404) return { status: 'missing', reason: 'бонусный расчёт не выполнен' }
  if (!response.ok) return { status: 'missing', reason: `сервер вернул ${response.status}` }
  let raw: unknown
  try {
    raw = JSON.parse(await response.text())
  } catch {
    return { status: 'invalid', reason: 'файл повреждён: это не JSON' }
  }
  const problem = validate(raw)
  return problem ? { status: 'invalid', reason: problem } : { status: 'ok', payload: raw as ResiliencePayload }
}

/** Числовой порядок gid без потери точности: сначала длина, потом строка (gid без ведущих нулей). */
const byGid = (a: string, b: string) => a.length - b.length || (a < b ? -1 : a > b ? 1 : 0)

/** Порядок удаления по приоритету — как в top_nodes: priority_score ↓, числовой gid ↑. */
export function priorityOrder(data: GraphData): string[] {
  const score = new Map(data.nodes.map(n => [n.gid, n.priority_score]))
  return data.nodes.map(n => n.gid).sort((a, b) => score.get(b)! - score.get(a)! || byGid(a, b))
}

/** Относится ли файл к загруженным CSV. Пустой список — можно показывать числа. */
export function checkConsistency(payload: ResiliencePayload, data: GraphData): string[] {
  const issues: string[] = []
  const g = payload.sources.graph
  if (g.n_nodes !== data.nodes.length) issues.push(`узлов в расчёте ${g.n_nodes}, в выгрузке ${data.nodes.length}`)
  if (g.n_edges !== data.edges.length) issues.push(`рёбер в расчёте ${g.n_edges}, в выгрузке ${data.edges.length}`)
  const total = data.edges.reduce((sum, e) => sum + e.sum_kzt, 0)
  if (Math.abs(total - g.total_kzt) > 0.05) issues.push('оборот графа не совпадает с выгрузкой')
  const ids = new Set(data.nodes.map(n => n.gid))
  for (const s of ['priority', 'degree'] as const) {
    const unknown = payload.removals[s].filter(gid => !ids.has(gid))
    if (unknown.length) issues.push(`${unknown.length} удаляемых узлов (${s}) нет в выгрузке`)
  }
  if (!issues.length) {
    const priority = payload.removals.priority
    const expected = priorityOrder(data).slice(0, priority.length)
    if (expected.some((gid, i) => gid !== priority[i]))
      issues.push('порядок удаления не совпадает с текущим priority_score: расчёт сделан на другой версии')
  }
  return issues
}

export interface Fragments {
  componentOf: Map<string, number>
  sizes: number[]
  components: number
  largest: number
  isolates: Set<string>
  remaining: number
  remainingEdges: number
}

/** Слабые компоненты после удаления (направление игнорируется, как в PAN-44). */
export function fragments(nodeIds: string[], edges: { src: string; dst: string }[], removed: Set<string>): Fragments {
  const parent = new Map<string, string>()
  const find = (x: string): string => {
    let root = x
    while (parent.get(root) !== root) root = parent.get(root)!
    while (parent.get(x) !== root) { const next = parent.get(x)!; parent.set(x, root); x = next }
    return root
  }
  for (const id of nodeIds) if (!removed.has(id)) parent.set(id, id)
  const touched = new Set<string>()
  let remainingEdges = 0
  for (const e of edges) {
    if (!parent.has(e.src) || !parent.has(e.dst)) continue
    remainingEdges += 1
    touched.add(e.src); touched.add(e.dst)
    const a = find(e.src), b = find(e.dst)
    if (a !== b) parent.set(a, b)
  }
  const sizeByRoot = new Map<string, number>()
  for (const id of parent.keys()) { const r = find(id); sizeByRoot.set(r, (sizeByRoot.get(r) ?? 0) + 1) }
  const roots = [...sizeByRoot.entries()].sort((a, b) => b[1] - a[1] || byGid(a[0], b[0]))
  const index = new Map(roots.map(([r], i) => [r, i]))
  const componentOf = new Map<string, number>()
  for (const id of parent.keys()) componentOf.set(id, index.get(find(id))!)
  const isolates = new Set([...parent.keys()].filter(id => !touched.has(id)))
  const sizes = roots.map(([, s]) => s)
  return { componentOf, sizes, components: sizes.length, largest: sizes[0] ?? 0, isolates,
           remaining: parent.size, remainingEdges }
}

/** Сверка фрагментов браузера с файлом: интерфейс не должен рисовать одно, а писать другое. */
export function fragmentMismatch(frag: Fragments, metrics: Metrics, originalIsolates: Set<string>): string[] {
  const newIsolates = [...frag.isolates].filter(id => !originalIsolates.has(id)).length
  const pairs: [string, number][] = [
    ['n_remaining', frag.remaining], ['n_edges_remaining', frag.remainingEdges],
    ['weak_components', frag.components], ['largest_weak_size', frag.largest],
    ['isolates', frag.isolates.size], ['new_isolates', newIsolates],
  ]
  return pairs.filter(([k, v]) => metrics[k] !== v).map(([k, v]) => `${k}: файл ${metrics[k]}, граф ${v}`)
}

export const pct = (v: Metric) => (v === null || v === undefined ? 'не определено' : `${(v * 100).toFixed(1)}%`)
export const count = (v: Metric) =>
  v === null || v === undefined ? 'не определено' : new Intl.NumberFormat('ru-RU').format(Math.round(v))
export const formatMetric = (metric: string, v: Metric) =>
  ['weak_pair_loss', 'seed_reach_loss', 'removed_kzt_share'].includes(metric) ? pct(v) : count(v)

/** Осторожный вывод строится из чисел файла; ничего не зашито. */
export function conclusion(payload: ResiliencePayload, n: number): string[] {
  if (n === 0) return ['N = 0: исходная наблюдаемая сеть, ничего не удалено.']
  const key = String(n)
  const p = payload.deterministic.priority[key]?.weak_pair_loss ?? null
  const d = payload.deterministic.degree[key]?.weak_pair_loss ?? null
  const m = payload.controls.matched_random[key]?.weak_pair_loss
  const runs = payload.parameters.random_runs
  if (p === null || !m || m.mean === null)
    return [`N = ${n}: потеря связности пар не определена — среди оставшихся узлов нет пар, связанных до удаления.`]
  const lines = [
    `Удаление top-${n} по приоритету: потеря связности пар ${pct(p)}; у случайного удаления того же состава ` +
    `seed/колен в среднем ${pct(m.mean)} (5–95%: ${pct(m.q05)}–${pct(m.q95)}, ${runs} прогонов).`,
  ]
  if (m.mean > 0) lines.push(`Это в ${(p / m.mean).toFixed(1)} раза больше среднего контроля.`)
  const share = payload.comparisons[key]?.matched_random?.weak_pair_loss?.reference_fraction_ge_priority
  if (share !== null && share !== undefined)
    lines.push(`Сценариев контроля с потерей не меньше приоритета: ${pct(share)} (это не p-value).`)
  if (d !== null) {
    const verdict = Math.abs(d - p) < 1e-9 ? 'столько же, сколько' : d > p ? 'сильнее, чем' : 'слабее, чем'
    lines.push(`Удаление по числу связей (degree) даёт ${pct(d)} — ${verdict} приоритет: ` +
      'приоритет выбирает узлы для AML-проверки, а не оптимизирует разрушение сети.')
  }
  lines.push('Это гипотеза о структурной роли узлов в наблюдаемом графе, не вывод о виновности.')
  return lines
}
