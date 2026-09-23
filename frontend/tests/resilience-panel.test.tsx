// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { loadData } from '../src/data'
import ResiliencePanel from '../src/resilience/ResiliencePanel'
import { pct, type ResiliencePayload } from '../src/resilience/resilience'
import type { GraphData } from '../src/types'

vi.mock('../src/resilience/ResilienceGraph', () => ({
  default: ({ nodes, onSelect }: { nodes: { gid: string; removed: boolean }[]; onSelect: (gid: string) => void }) => (
    <div data-testid="resilience-graph" data-removed={nodes.filter(n => n.removed).map(n => n.gid).join(',')}>
      {nodes.map(n => <button key={n.gid} onClick={() => onSelect(n.gid)}>node {n.gid}</button>)}
    </div>
  ),
}))

// в jsdom import.meta.url не file:// — путь от корня frontend, откуда запускается vitest
const fixtures = join(process.cwd(), 'public', 'fixtures')
const payload: ResiliencePayload = JSON.parse(readFileSync(join(fixtures, 'resilience.json'), 'utf8'))
let root: Root, host: HTMLDivElement, data: GraphData

function serve(resilience: Response | 'missing' = new Response(JSON.stringify(payload))) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const file = url.split('/').pop()!
    if (file === 'resilience.json') return resilience === 'missing' ? new Response('', { status: 404 }) : resilience
    return new Response(readFileSync(join(fixtures, file), 'utf8'))
  }))
}
async function render(graph: GraphData, onSelect = vi.fn()) {
  await act(async () => root.render(<ResiliencePanel data={graph} onSelect={onSelect} />))
  await act(async () => { await new Promise(r => setTimeout(r, 0)) })
  return onSelect
}
const cell = (metric: string) => host.querySelector(`[data-testid="priority-${metric}"]`)?.textContent
const click = async (label: string) => {
  const button = [...host.querySelectorAll('button')].find(b => b.textContent === label)!
  await act(async () => button.click())
}

beforeEach(async () => {
  serve()
  data = await loadData('/fixtures')
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

describe('ResiliencePanel', () => {
  it('числа совпадают с файлом для выбранного N; N=0 возвращает исходное состояние', async () => {
    await render(data)
    const last = Math.max(...payload.parameters.steps)
    expect(cell('weak_pair_loss')).toBe(pct(payload.deterministic.priority[String(last)].weak_pair_loss))
    expect(cell('removed_kzt_share')).toBe(pct(payload.deterministic.priority[String(last)].removed_kzt_share))
    expect(host.querySelector('[data-testid="resilience-graph"]')!.getAttribute('data-removed'))
      .toBe(payload.removals.priority.slice(0, last).join(','))
    await click('0')
    expect(cell('removed_kzt_share')).toBe(pct(payload.baseline.removed_kzt_share))
    expect(host.querySelector('[data-testid="resilience-graph"]')!.getAttribute('data-removed')).toBe('')
    expect(host.textContent).toMatch(/Ничего не удалено/)
    expect(host.textContent).toMatch(/N = 0: исходная наблюдаемая сеть/)
  })

  it('неприменимая метрика показана как «не определено», лимит показа и сверка фрагментов видны', async () => {
    await render(data)
    expect(payload.baseline.seed_reach_loss).toBeNull()
    expect(cell('seed_reach_loss')).toBe('не определено')
    const note = host.querySelector('[data-testid="display-limit"]')!.textContent!
    expect(note).toMatch(new RegExp(`${payload.sources.graph.n_nodes} узлов, ${payload.sources.graph.n_edges} рёбер`))
    expect(host.querySelector('[data-testid="fragment-check"]')!.textContent).toMatch(/Фрагменты сверены с файлом/)
  })

  it('контроли, число прогонов и оговорки рядом с выводом', async () => {
    await render(data)
    expect(host.textContent).toMatch(new RegExp(`${payload.parameters.random_runs} прогонов`))
    for (const caveat of payload.caveats) expect(host.textContent).toContain(caveat)
    expect(host.textContent).toMatch(/Осторожный вывод/)
  })

  it('клик по удалённому узлу открывает его на основном графе', async () => {
    const onSelect = await render(data)
    const gid = payload.removals.priority[0]
    await act(async () => ([...host.querySelectorAll('.resilience-removed button')]
      .find(b => b.textContent === gid) as HTMLButtonElement).click())
    expect(onSelect).toHaveBeenCalledWith(gid)
  })

  it('нет файла — сообщение и команда, без таблицы; остальной экран не зависит', async () => {
    serve('missing')
    await render(data)
    expect(host.textContent).toMatch(/Расчёт устойчивости не найден/)
    expect(host.textContent).toMatch(/python -m analytics\.resilience_export/)
    expect(host.querySelector('[role="table"]')).toBeNull()
  })

  it('расчёт от другой версии priority — числа скрыты, показана причина', async () => {
    const other = data.nodes.find(n => n.gid !== payload.removals.priority[0])!.gid   // станет первым
    const changed = { ...data, nodes: data.nodes.map(n => n.gid === other ? { ...n, priority_score: 5 } : n) }
    await render(changed)
    expect(host.querySelector('[role="alert"]')!.textContent).toMatch(/другой версии данных/)
    expect(host.querySelector('[role="table"]')).toBeNull()
  })
})
