// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'
import { loadData } from '../src/data'
import type { GraphData, NodeRecord } from '../src/types'
import { askCopilot, copilotStatus } from '../src/copilot/api'
import { brief } from './copilot-fixture'

vi.mock('../src/copilot/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/copilot/api')>()),
  askCopilot: vi.fn(), copilotStatus: vi.fn(),
}))

vi.mock('../src/data', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../src/data')>()),
  loadData: vi.fn(),
}))
vi.mock('../src/GraphCanvas', () => ({
  default: ({ nodes, onSelect, highlightedGids, highlightedEdges, focusId, focusSequence, layoutFocusId }: {
    nodes: NodeRecord[]; onSelect: (gid: string) => void; highlightedGids: string[];
    highlightedEdges: string[]; focusId?: string; focusSequence?: number; layoutFocusId?: string;
  }) => (
    <div data-testid="graph" data-evidence={highlightedGids.join(',')} data-edges={highlightedEdges.join(',')}
      data-focus={focusId} data-sequence={focusSequence} data-context={layoutFocusId}>
      {nodes.map((n) => (
        <button key={n.gid} onClick={() => onSelect(n.gid)}>
          graph {n.gid}
        </button>
      ))}
    </div>
  ),
}))

const first = '100000008165763100',
  boundary = '100000003037476100'
const node = (gid: string): NodeRecord => ({
  gid,
  role: 'coordinator',
  role_score: 0.71,
  cluster_id: 5,
  priority_score: 0.723,
  evidence: 'получает от 15 плательщиков',
  priority_why: '26 переводов (+0.21)',
  depth: 1,
  is_seed: false,
  boundary_depth4: false,
  in_deg: 1,
  out_deg: 1,
  n_payers: 1,
  n_receivers: 1,
  in_kzt: 10,
  out_kzt: 10,
})
const data: GraphData = {
  nodes: [node(first), { ...node(boundary), depth: 4, role: 'peripheral' }],
  edges: [{ src: first, dst: boundary, sum_kzt: 100, n_tx: 2 }],
  clusters: [
    {
      cluster_id: 5,
      n_nodes: 2,
      n_seed: 0,
      sum_kzt_internal: 100,
      top_gids: first,
      hypothesis: 'test',
    },
  ],
  topNodes: [first, boundary].map((gid, i) => ({
    gid,
    rank: i + 1,
    role: 'coordinator',
    priority_score: 0.723,
    why: '26 переводов',
    priority_why: '26 переводов',
  })),
  source: '/out',
}
let root: Root, container: HTMLDivElement
const button = (text: string) =>
  Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(text))!

beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('matchMedia', () => ({ matches: true }))
  Element.prototype.scrollIntoView = vi.fn()
  vi.mocked(loadData).mockResolvedValue(data)
  vi.mocked(copilotStatus).mockResolvedValue({ ready: true, nvidia_available: false })
  vi.mocked(askCopilot).mockResolvedValue({ ...brief, gids: [first, boundary],
    candidates: [{ gid: boundary, role: 'peripheral', priority_score: 0.723 }],
    claims: [{ kind: 'edge', src: first, dst: boundary, field: 'sum_kzt', value: 100,
      source: { file: 'edge_table.csv', src: first, dst: boundary, column: 'sum_kzt' } }],
  })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})
async function render() {
  await act(async () => root.render(<App />))
}

describe('Investigation screen', () => {
  it('opens the first priority with evidence and its graph context', async () => {
    await render()
    expect(container.querySelector(`[aria-label="Карточка узла ${first}"]`)).not.toBeNull()
    expect(container.textContent).toContain('получает от 15 плательщиков')
    expect(container.querySelector('[data-testid="graph"]')?.textContent).toContain(boundary)
    expect(loadData).toHaveBeenCalledWith('/out')
  })
  it('a priority row opens the boundary client, preserving its warning and flows', async () => {
    await render()
    await act(async () => container.querySelectorAll<HTMLElement>('.table-row')[1].click())
    expect(container.querySelector(`[aria-label="Карточка узла ${boundary}"]`)).not.toBeNull()
    expect(container.textContent).toContain('Это не значит, что деньги осели')
    expect(container.querySelector('.flow-summary')?.textContent).toContain('100')
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled()
  })
  it('clicking a graph node opens its profile without losing the current graph', async () => {
    await render()
    await act(async () => button(`graph ${boundary}`).click())
    expect(container.querySelector(`[aria-label="Карточка узла ${boundary}"]`)).not.toBeNull()
    expect(container.querySelector('[data-testid="graph"]')?.textContent).toContain(first)
  })
  it('opens the real Copilot and keeps question/context mounted across tabs', async () => {
    await render()
    expect(container.querySelector('#copilot-tab')?.getAttribute('aria-selected')).toBe('true')
    expect(container.textContent).toContain('Локальный анализ готов')
    await act(async () => button('Почему этот узел в топе?').click())
    const question = container.querySelector<HTMLTextAreaElement>('#copilot-question')!
    expect(question.value).toBe('Почему этот узел в топе?')
    await act(async () => button('Обзор клиента').click())
    expect(container.querySelector(`[aria-label="Карточка узла ${first}"]`)).not.toBeNull()
    await act(async () => button('AI Copilot').click())
    expect(container.querySelector('#copilot-question')).toBe(question)
    expect(question.value).toBe('Почему этот узел в топе?')
    expect(container.querySelector('.copilot-chips')?.textContent).toContain(first)
  })
  it('pins evidence beyond filters and navigates repeatedly without losing the answer or filters', async () => {
    await render()
    const role = container.querySelector<HTMLSelectElement>('#role')!
    await act(async () => { role.value = 'coordinator'; role.dispatchEvent(new Event('change', { bubbles: true })) })
    const graph = () => container.querySelector<HTMLElement>('[data-testid="graph"]')!
    expect(graph().textContent).not.toContain(boundary)
    await act(async () => button('Почему этот узел в топе?').click())
    await act(async () => button('Разобрать вопрос').click())
    expect(graph().dataset.edges).toBe(`${first}:${boundary}`)
    expect(graph().dataset.evidence).toBe(`${first},${boundary}`)
    expect(graph().textContent).toContain(boundary)
    expect(container.querySelector('.evidence-strip')?.textContent).toContain('+1 вне фильтров')
    const open = () => container.querySelector<HTMLButtonElement>(`.copilot [aria-label="Открыть узел ${boundary}"]`)!
    await act(async () => open().click())
    expect(role.value).toBe('coordinator')
    expect(graph().dataset.focus).toBe(boundary)
    expect(graph().dataset.sequence).toBe('1')
    expect(container.querySelector('#profile-panel')?.hasAttribute('hidden')).toBe(false)
    await act(async () => button('AI Copilot').click())
    expect(container.querySelector('.copilot-summary')?.textContent).toContain('гипотеза для проверки')
    await act(async () => open().click())
    expect(graph().dataset.sequence).toBe('2')
    expect(askCopilot).toHaveBeenCalledTimes(1)
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Снять подсветку Copilot"]')!.click())
    expect(graph().textContent).not.toContain(boundary)
    expect(graph().dataset.evidence).toBe('')
  })
  it('copilot navigation preserves the original neighbourhood and failed responses do not highlight evidence', async () => {
    await render()
    await act(async () => button('Почему этот узел в топе?').click())
    await act(async () => button('Разобрать вопрос').click())
    await act(async () => container.querySelector<HTMLButtonElement>(`.copilot [aria-label="Открыть узел ${boundary}"]`)!.click())
    expect(container.querySelector<HTMLElement>('[data-testid="graph"]')?.dataset.context).toBe(first)
    await act(async () => button('AI Copilot').click())
    vi.mocked(askCopilot).mockRejectedValueOnce(new Error('Помощник занят'))
    await act(async () => button('Разобрать вопрос').click())
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Помощник занят')
    expect(container.querySelector('.evidence-strip')).toBeNull()
    expect(container.querySelector<HTMLElement>('[data-testid="graph"]')?.dataset.evidence).toBe('')
  })
  it('offers fixture recovery after a CSV loading error', async () => {
    vi.mocked(loadData).mockRejectedValueOnce(new Error('offline'))
    await render()
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    await act(async () => button('Открыть демо-набор').click())
    expect(loadData).toHaveBeenLastCalledWith('/fixtures')
    expect(container.querySelector(`[aria-label="Карточка узла ${first}"]`)).not.toBeNull()
  })
  it('search opens an exact GID and a subsequent role filter exits its context', async () => {
    await render()
    const input = container.querySelector<HTMLInputElement>('[aria-label="Поиск по GID"]')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(
        input,
        boundary,
      )
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(container.querySelector(`[aria-label="Карточка узла ${boundary}"]`)).not.toBeNull()
    await act(async () => {
      const select = container.querySelector<HTMLSelectElement>('#role')!
      select.value = 'coordinator'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(input.value).toBe('')
    expect(container.querySelector('[data-testid="graph"]')?.textContent).not.toContain(boundary)
    expect(container.querySelector('[data-testid="graph"]')?.textContent).toContain(first)
  })
})
