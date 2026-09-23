// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { PriorityTable } from '../src/components/PriorityTable'
import { NodeInspector } from '../src/components/NodeInspector'
import { GraphPanel } from '../src/components/GraphPanel'
import GraphCanvas from '../src/GraphCanvas'
import { clusterColor } from '../src/presentation'
import type { EdgeRecord, NodeRecord, TopRecord } from '../src/types'

vi.mock('../src/GraphCanvas', () => ({ default: vi.fn(() => <div data-testid="graph-canvas" />) }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

const gid = (i: number) => `9223372036854775${String(i).padStart(4, '0')}`
const node: NodeRecord = {
  gid: gid(0), role: 'consolidator', role_score: 0.8, cluster_id: 2,
  priority_score: 0.7, evidence: 'Наблюдаемые переводы', priority_why: 'Приоритет проверки',
  depth: 1, is_seed: true, boundary_depth4: false, in_deg: 7, out_deg: 8,
  n_payers: 7, n_receivers: 8, in_kzt: 91, out_kzt: 188,
}
const priorities: TopRecord[] = Array.from({ length: 30 }, (_, i) => ({
  gid: gid(i), rank: i + 1, role: 'consolidator', priority_score: (100 - i) / 100,
  why: `Причина ${i}`, priority_why: `Причина ${i}`,
}))
const incoming: EdgeRecord[] = Array.from({ length: 7 }, (_, i) => ({ src: gid(i + 1), dst: node.gid, sum_kzt: 10 + i, n_tx: 2 }))
const outgoing: EdgeRecord[] = Array.from({ length: 8 }, (_, i) => ({ src: node.gid, dst: gid(i + 8), sum_kzt: 20 + i, n_tx: 3 }))

describe('Complete priority list', () => {
  it('reports visible/total counts, retains all CSV ranks/scores and opens a row after the first 20', () => {
    const onSelect = vi.fn()
    const { container } = render(<PriorityTable rows={priorities} onSelect={onSelect} />)
    expect(screen.getByRole('status').textContent).toBe('Показано 5 из 30 клиентов')
    expect(container.querySelectorAll('.table-row')).toHaveLength(5)
    const toggle = screen.getByRole('button', { name: 'Все 30 клиентов' })
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('status').textContent).toBe('Показано 30 из 30 клиентов')
    const rows = [...container.querySelectorAll('.table-row')]
    expect(rows).toHaveLength(30)
    expect(rows.map(row => row.querySelector('.rank')?.textContent)).toEqual(priorities.map(row => String(row.rank).padStart(2, '0')))
    expect(rows.map(row => row.querySelector('.table-score strong')?.textContent)).toEqual(priorities.map(row => row.priority_score.toFixed(3)))
    expect(rows.map(row => row.querySelector('.table-node')?.textContent)).toEqual(priorities.map(row => row.gid))
    fireEvent.click(screen.getByRole('button', { name: `Открыть клиент ${gid(25)}` }))
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(gid(25))
    fireEvent.click(toggle)
    expect(container.querySelectorAll('.table-row')).toHaveLength(5)
  })
  it('shows empty and short lists without a meaningless expand action', () => {
    const view = render(<PriorityTable rows={[]} onSelect={vi.fn()} />)
    expect(screen.getByRole('status').textContent).toBe('Показано 0 из 0 клиентов')
    expect(screen.getByText('Приоритетных клиентов в выгрузке нет.')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
    view.rerender(<PriorityTable rows={priorities.slice(0, 3)} onSelect={vi.fn()} />)
    expect(screen.getByRole('status').textContent).toBe('Показано 3 из 3 клиентов')
    expect(screen.queryByText('Все 3 клиентов')).toBeNull()
  })
})

describe('Complete incoming and outgoing counterparty lists', () => {
  it('expands both directions, keeps full aggregates and returns an exact 19-digit GID', () => {
    const onSelect = vi.fn()
    const { container } = render(<NodeInspector node={node} edges={[...incoming, ...outgoing]} onSelect={onSelect} onClose={vi.fn()} />)
    const lists = [...container.querySelectorAll<HTMLElement>('.flow-list')]
    const before = screen.getByLabelText('Агрегаты денежных потоков').textContent
    expect(before).toContain('91')
    expect(before).toContain('188')
    expect(before).toContain('14 переводов · 7 отправителей')
    expect(before).toContain('24 переводов · 8 получателей')
    expect(lists.map(list => list.querySelectorAll('.flow-row').length)).toEqual([4, 4])
    for (const [index, count] of [7, 8].entries()) {
      const toggle = within(lists[index]).getByRole('button', { name: `Показать все ${count} связей` })
      fireEvent.click(toggle)
      expect(toggle.getAttribute('aria-expanded')).toBe('true')
      expect(lists[index].querySelectorAll('.flow-row')).toHaveLength(count)
    }
    expect(screen.getByLabelText('Агрегаты денежных потоков').textContent).toBe(before)
    fireEvent.click(within(lists[1]).getByRole('button', { name: `Открыть узел ${gid(15)}` }))
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(gid(15))
    expect(screen.getByLabelText('Роль: consolidator; seed: да')).toBeTruthy()
  })
  it('shows absent observed flows and the depth-4 boundary warning without fake rows', () => {
    const { container } = render(<NodeInspector node={{ ...node, depth: 4 }} edges={[]} onSelect={vi.fn()} onClose={vi.fn()} />)
    expect(container.querySelectorAll('.flow-row')).toHaveLength(0)
    expect(screen.getByText('Связей в выгрузке нет')).toBeTruthy()
    expect(screen.getByText('Исходящие за пределами наблюдения')).toBeTruthy()
    expect(screen.getByRole('note').textContent).toContain('Это не значит, что деньги осели')
    expect(screen.queryByText(/Показать все/)).toBeNull()
  })
})

describe('Cluster colors and graph evidence', () => {
  it('maps exactly the visible cluster IDs to canvas colors and keeps directions, seed and evidence props', () => {
    const nodes = [node, { ...node, gid: gid(1), cluster_id: 7 }, { ...node, gid: gid(2), cluster_id: 2 }]
    const edges = [{ src: node.gid, dst: gid(1), sum_kzt: 10, n_tx: 1 }]
    const highlights = { gids: [node.gid], edges: [`${node.gid}:${gid(1)}`] }
    const props = {
      graph: { nodes, edges, total: 3, focused: false, extraCount: 1, outsideFilterCount: 0, outsideLimitCount: 0 },
      search: '', neighborsOnly: false, selected: node, highlights, hasAnswer: true,
      onClearEvidence: vi.fn(), onClearContext: vi.fn(), onCopilot: vi.fn(), onSearch: vi.fn(),
      onSelect: vi.fn(), onHover: vi.fn(), onNeighbors: vi.fn(), onReset: vi.fn(),
    }
    const view = render(<GraphPanel {...props} />)
    const lastCanvas = () => vi.mocked(GraphCanvas).mock.calls.at(-1)![0]
    expect(lastCanvas().colorMode).toBe('role')
    expect(screen.queryByLabelText('Цвета кластеров на графе')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Кластеры' }))
    const legend = screen.getByLabelText('Кластеры текущего вида')
    const entries = within(legend).getAllByRole('listitem')
    expect(entries.map(entry => entry.textContent)).toEqual(['Кластер 2', 'Кластер 7'])
    for (const [index, id] of [2, 7].entries()) {
      const swatch = document.createElement('i')
      swatch.style.backgroundColor = clusterColor(id)
      expect(entries[index].querySelector('i')?.style.backgroundColor).toBe(swatch.style.backgroundColor)
    }
    expect(lastCanvas().colorMode).toBe('cluster')
    expect(lastCanvas().nodes).toBe(nodes)
    expect(lastCanvas().edges).toBe(edges)
    expect(lastCanvas().selectedId).toBe(node.gid)
    expect(lastCanvas().highlightedGids).toBe(highlights.gids)
    expect(lastCanvas().highlightedEdges).toBe(highlights.edges)
    expect(screen.getByText(/вне фильтров\/лимита/)).toBeTruthy()
    view.rerender(<GraphPanel {...props} graph={{ ...props.graph, nodes: [], edges: [], total: 0, extraCount: 0 }} />)
    expect(screen.queryByLabelText('Кластеры текущего вида')).toBeNull()
    expect(screen.getByText('В текущем виде нет узлов')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Роли' }))
    expect(screen.queryByLabelText('Цвета кластеров на графе')).toBeNull()
  })
})
