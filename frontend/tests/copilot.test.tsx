// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CopilotPanel from '../src/copilot/CopilotPanel'
import { askCopilot, MAX_RESPONSE_BYTES, validateAnswer } from '../src/copilot/api'
import { answerHighlights, includeEvidence } from '../src/copilot/graph'
import { A, B, brief, data } from './copilot-fixture'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
function mockFetch(answer: unknown = brief, status = 200, nvidia = false) {
  const fetch = vi.fn((url: string) => Promise.resolve(url.endsWith('/status') ? response({ ready: true, nvidia_available: nvidia }) : response(answer, status)))
  vi.stubGlobal('fetch', fetch)
  return fetch
}
function setup() {
  const onNavigate = vi.fn(), onAnswer = vi.fn()
  render(<CopilotPanel data={data} selectedId={A} onNavigate={onNavigate} onAnswer={onAnswer} />)
  return { onNavigate, onAnswer }
}
async function submit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /Почему этот узел/ }))
  await user.click(screen.getByRole('button', { name: /Разобрать вопрос/ }))
}

describe('Copilot panel', () => {
  it('only checks availability when browsing; submits string gids and renders sourced fallback', async () => {
    const fetch = mockFetch(), user = userEvent.setup()
    setup()
    await screen.findByText('Локальный анализ готов')
    expect(fetch.mock.calls.every(([url]) => url.endsWith('/status'))).toBe(true)
    await submit(user)
    await screen.findByText('Локальный ответ')
    const calls = (fetch.mock.calls as unknown as [string, RequestInit][]).filter(([url]) => url.endsWith('/answer'))
    expect(calls).toHaveLength(1)
    expect(JSON.parse(calls[0][1].body as string)).toEqual({ question: 'Почему этот узел в топе?', selected_gids: [A], use_nvidia: false })
    expect(screen.getByText(/гипотеза для проверки/)).toBeTruthy()
    expect(screen.getByText('nodes_roles.csv · in_kzt')).toBeTruthy()
  })

  it('navigates to the existing node card using exact gid', async () => {
    mockFetch(); const user = userEvent.setup(), { onNavigate, onAnswer } = setup()
    await submit(user)
    await screen.findByText('Локальный ответ')
    await user.click(screen.getAllByRole('button', { name: `Открыть узел ${A}` })[0])
    expect(onNavigate).toHaveBeenCalledWith(A)
    expect(onAnswer).toHaveBeenLastCalledWith(brief)
  })

  it('shows quota fallback while preserving confirmed facts', async () => {
    mockFetch({ ...brief, fallback_reason: 'quota' }, 200, true)
    const user = userEvent.setup(); setup()
    await user.click(await screen.findByRole('checkbox', { name: 'Использовать AI' }))
    await submit(user)
    await screen.findByText(/Лимит AI временно исчерпан/)
    expect(screen.getByText('nodes_roles.csv · in_kzt')).toBeTruthy()
  })

  it('renders empty result separately from an API error', async () => {
    mockFetch({ ...brief, status: 'empty', candidates: [], summary: 'Общий сборщик не наблюдается. Это гипотеза для проверки.' })
    const user = userEvent.setup(); setup(); await submit(user)
    await screen.findByText('Совпадений не найдено')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('handles HTTP failure and allows retry', async () => {
    const fetch = mockFetch({}, 500), user = userEvent.setup(); setup(); await submit(user)
    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: /Разобрать вопрос/ }).hasAttribute('disabled')).toBe(false)
    fetch.mockImplementation(url => Promise.resolve(url.endsWith('/status') ? response({ ready: true, nvidia_available: false }) : response(brief)))
    await user.click(screen.getByRole('button', { name: /Разобрать вопрос/ }))
    await screen.findByText('Локальный ответ')
  })

  it('does not publish a cancelled late response', async () => {
    let resolve: (value: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn((url: string) => url.endsWith('/status') ? Promise.resolve(response({ ready: true, nvidia_available: false })) : new Promise<Response>(r => { resolve = r })))
    const user = userEvent.setup(), { onAnswer } = setup(); await submit(user)
    await screen.findByText('Проверяем связи и факты')
    await user.click(screen.getByRole('button', { name: 'Отменить' }))
    resolve(response(brief))
    await waitFor(() => expect(screen.queryByText('Локальный ответ')).toBeNull())
    expect(onAnswer).toHaveBeenCalledTimes(1) // clear only
  })

  it('enforces question length and permits a second selected gid', async () => {
    mockFetch(); const user = userEvent.setup(); setup()
    expect(screen.getByLabelText('Вопрос аналитика').getAttribute('maxlength')).toBe('1000')
    await user.click(screen.getByRole('button', { name: /Почему этот узел/ }))
    await user.type(screen.getByLabelText('Узлы для проверки'), B)
    await user.click(screen.getByRole('button', { name: 'Добавить GID' }))
    expect(screen.getByRole('button', { name: `Убрать узел ${B}` })).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Вопрос аналитика'), { target: { value: 'Кто общий сборщик?' } })
    await user.click(screen.getByRole('button', { name: /Разобрать вопрос/ }))
    await screen.findByText('Локальный ответ')
  })

  it('blocks mixing the static demo fixture with real backend data', () => {
    mockFetch()
    render(<CopilotPanel data={{ ...data, source: '/fixtures' }} onNavigate={vi.fn()} onAnswer={vi.fn()} />)
    expect(screen.getByText(/не смешивать данные/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Разобрать вопрос/ }).hasAttribute('disabled')).toBe(true)
  })
})

describe('Response gates and graph links', () => {
  it.each([
    { ...brief, verification: 'unavailable' },
    { ...brief, gids: [Number(A)] },
    { ...brief, gids: ['unknown'] },
    { ...brief, claims: [{ ...brief.claims[0], value: 9999 }] },
    { ...brief, evidence: [{ claim_index: 999 }] },
    { ...brief, claims: [{ ...brief.claims[0], source: { ...brief.claims[0].source, column: 'out_kzt' } }] },
    { ...brief, candidates: [{ ...brief.candidates[0], priority_score: 1 }] },
  ])('rejects unverified, fabricated or mismatched data', raw => {
    expect(() => validateAnswer(raw, data)).toThrow()
  })

  it('keeps boundary warnings mandatory', () => {
    const boundary = { ...data, nodes: data.nodes.map(n => ({ ...n, depth: 4 })) }
    expect(() => validateAnswer(brief, boundary)).toThrow(/граница обхода/)
  })

  it('caps streamed response size even without Content-Length', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('x'.repeat(MAX_RESPONSE_BYTES + 1)))))
    await expect(askCopilot({ question: 'Почему?', selected_gids: [A], use_nvidia: false }, data, new AbortController().signal)).rejects.toThrow(/слишком большой/)
  })

  it('reports a request timeout without returning any partial facts', async () => {
    const controller = new AbortController()
    vi.spyOn(AbortSignal, 'timeout').mockReturnValue(controller.signal)
    vi.stubGlobal('fetch', vi.fn((_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')))
    })))
    const pending = askCopilot({ question: 'Почему?', selected_gids: [A], use_nvidia: false }, data, new AbortController().signal)
    const assertion = expect(pending).rejects.toThrow(/Время ожидания истекло/)
    controller.abort()
    await assertion
  })

  it('pins cited nodes outside filters and includes exact directed edge', () => {
    const answer = { ...brief, gids: [A, B], claims: [{ kind: 'edge' as const, src: A, dst: B, field: 'sum_kzt', value: 5000,
      source: { file: 'edge_table.csv' as const, src: A, dst: B, column: 'sum_kzt' } }] }
    const highlights = answerHighlights(answer)
    expect(highlights.edges).toEqual([`${A}:${B}`])
    const graph = includeEvidence({ nodes: [], edges: [] }, data, highlights.gids)
    expect(graph.nodes.map(n => n.gid)).toEqual([A, B])
    expect(graph.edges).toEqual(data.edges)
    expect(graph.extraCount).toBe(2)
    expect(includeEvidence({ nodes: [], edges: [] }, data, [], A).nodes.map(n => n.gid)).toEqual([A])
  })
})
