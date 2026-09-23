import { existsSync, readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadData } from '../src/data'
import {
  checkConsistency, conclusion, fragmentMismatch, fragments, loadResilience, pct, priorityOrder,
  type ResiliencePayload,
} from '../src/resilience/resilience'
import type { GraphData } from '../src/types'

const fixtures = new URL('../public/fixtures/', import.meta.url)
const real = new URL('../../out/', import.meta.url)
afterEach(() => vi.unstubAllGlobals())

function serve(dir: URL, overrides: Record<string, Response | Error> = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const file = url.split('/').pop()!
    const override = overrides[file]
    if (override instanceof Error) throw override
    if (override) return override
    if (!existsSync(new URL(file, dir))) return new Response('', { status: 404 })
    return new Response(readFileSync(new URL(file, dir), 'utf8'))
  }))
}
async function load(dir: URL): Promise<{ data: GraphData; payload: ResiliencePayload }> {
  serve(dir)
  const data = await loadData('/data')
  const result = await loadResilience('/data')
  if (result.status !== 'ok') throw new Error(result.reason)
  return { data, payload: result.payload }
}
const original = (data: GraphData) => fragments(data.nodes.map(n => n.gid), data.edges, new Set()).isolates

describe('загрузка resilience.json', () => {
  it('нет файла, сеть недоступна, битый JSON и чужая схема — понятные состояния, не исключение', async () => {
    serve(fixtures, { 'resilience.json': new Response('', { status: 404 }) })
    expect(await loadResilience('/x')).toEqual({ status: 'missing', reason: 'бонусный расчёт не выполнен' })
    serve(fixtures, { 'resilience.json': new Error('offline') })
    expect((await loadResilience('/x')).status).toBe('missing')
    serve(fixtures, { 'resilience.json': new Response('{oops') })
    expect(await loadResilience('/x')).toEqual({ status: 'invalid', reason: 'файл повреждён: это не JSON' })
    serve(fixtures, { 'resilience.json': new Response('{"schema": "v0"}') })
    expect((await loadResilience('/x')).status).toBe('invalid')
  })

  it('gid в порядке удаления обязаны быть строками — числа JS теряют точность', async () => {
    const payload = JSON.parse(readFileSync(new URL('resilience.json', fixtures), 'utf8'))
    payload.removals.priority = payload.removals.priority.map(Number)
    serve(fixtures, { 'resilience.json': new Response(JSON.stringify(payload)) })
    const result = await loadResilience('/x')
    expect(result.status).toBe('invalid')
    expect(result.status === 'invalid' && result.reason).toMatch(/строками/)
  })
})

describe('согласованность с загруженными CSV', () => {
  it('файл фикстур относится к CSV фикстур', async () => {
    const { data, payload } = await load(fixtures)
    expect(checkConsistency(payload, data)).toEqual([])
    const k = payload.removals.priority.length
    expect(k).toBe(Math.max(...payload.parameters.steps))
    expect(payload.removals.priority).toEqual(priorityOrder(data).slice(0, k))
  })

  it('другая версия priority или графа — расчёт помечается устаревшим', async () => {
    const { data, payload } = await load(fixtures)
    const [first, second] = priorityOrder(data)
    const swapped = { ...data, nodes: data.nodes.map(n => n.gid === second ? { ...n, priority_score: 2 } : n) }
    expect(swapped.nodes.find(n => n.gid === first)).toBeTruthy()
    expect(checkConsistency(payload, swapped).join()).toMatch(/порядок удаления/)
    expect(checkConsistency(payload, { ...data, nodes: data.nodes.slice(1) }).join()).toMatch(/узлов в расчёте/)
  })

  it('равный priority_score упорядочивается по числовому gid, как в top_nodes', () => {
    const node = (gid: string) => ({ gid, priority_score: 0.5 }) as GraphData['nodes'][number]
    expect(priorityOrder({ nodes: [node('100'), node('99')] } as GraphData)).toEqual(['99', '100'])
  })
})

describe('фрагменты для отображения', () => {
  it('направление игнорируется, изоляты и новые изоляты считаются как в PAN-44', () => {
    const edges = [{ src: 'a', dst: 'b' }, { src: 'b', dst: 'c' }]
    const before = fragments(['a', 'b', 'c', 'd'], edges, new Set())
    expect([before.components, before.largest, [...before.isolates]]).toEqual([2, 3, ['d']])
    const after = fragments(['a', 'b', 'c', 'd'], edges, new Set(['b']))
    expect([after.components, after.largest, after.remaining, after.remainingEdges]).toEqual([3, 1, 3, 0])
    expect(fragmentMismatch(after, { n_remaining: 3, n_edges_remaining: 0, weak_components: 3,
      largest_weak_size: 1, isolates: 3, new_isolates: 2 }, before.isolates)).toEqual([])
    expect(fragmentMismatch(after, { n_remaining: 3, n_edges_remaining: 0, weak_components: 2,
      largest_weak_size: 1, isolates: 3, new_isolates: 2 }, before.isolates)).toEqual(['weak_components: файл 2, граф 3'])
  })

  it('на фикстурах фрагменты совпадают с файлом для каждого N и стратегии; N=0 — исходная сеть', async () => {
    const { data, payload } = await load(fixtures)
    const ids = data.nodes.map(n => n.gid)
    for (const strategy of ['priority', 'degree'] as const)
      for (const n of payload.parameters.steps) {
        const frag = fragments(ids, data.edges, new Set(payload.removals[strategy].slice(0, n)))
        expect(fragmentMismatch(frag, payload.deterministic[strategy][String(n)], original(data))).toEqual([])
      }
    expect(payload.deterministic.priority['0']).toEqual(payload.baseline)
  })
})

describe('отображение и вывод', () => {
  const base = (priority: number | null, degree: number | null, mean: number | null) => ({
    parameters: { random_runs: 100 },
    deterministic: { priority: { 20: { weak_pair_loss: priority } }, degree: { 20: { weak_pair_loss: degree } } },
    controls: { matched_random: { 20: { weak_pair_loss: { mean, q05: 0.01, q95: 0.09, n_trials: 100, n_valid: 100 } } } },
    comparisons: { 20: { matched_random: { weak_pair_loss: { reference_fraction_ge_priority: 0 } } } },
  }) as unknown as ResiliencePayload

  it('неприменимая метрика — «не определено», а ноль остаётся нулём', () => {
    expect(pct(null)).toBe('не определено')
    expect(pct(0)).toBe('0.0%')
    expect(pct(0.52458)).toBe('52.5%')
  })

  it('вывод собирается из чисел: сильнее/слабее degree не зашито', () => {
    const weaker = conclusion(base(0.52, 0.6, 0.04), 20).join(' ')
    expect(weaker).toMatch(/52\.0%.*4\.0%.*13\.0 раза.*60\.0% — сильнее, чем приоритет/)
    expect(conclusion(base(0.7, 0.6, 0.04), 20).join(' ')).toMatch(/60\.0% — слабее, чем приоритет/)
    expect(conclusion(base(null, 0.6, 0.04), 20).join(' ')).toMatch(/не определена/)
    expect(conclusion(base(0.5, 0.5, 0.04), 0)).toEqual(['N = 0: исходная наблюдаемая сеть, ничего не удалено.'])
    expect(weaker).toMatch(/не p-value/)
    expect(weaker).toMatch(/не вывод о виновности/)
  })
})

describe.skipIf(!existsSync(new URL('resilience.json', real)))('реальная выгрузка', () => {
  it('файл относится к CSV, фрагменты браузера совпадают с PAN-44 для каждого N и стратегии', async () => {
    const { data, payload } = await load(real)
    expect(checkConsistency(payload, data)).toEqual([])
    const ids = data.nodes.map(n => n.gid)
    const iso = original(data)
    for (const strategy of ['priority', 'degree'] as const)
      for (const n of payload.parameters.steps) {
        const frag = fragments(ids, data.edges, new Set(payload.removals[strategy].slice(0, n)))
        expect(fragmentMismatch(frag, payload.deterministic[strategy][String(n)], iso)).toEqual([])
      }
    expect(payload.removals.priority.slice(0, data.topNodes.length)).toEqual(data.topNodes.map(t => t.gid))
  })
})
