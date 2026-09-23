// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../src/App'
import { loadData } from '../src/data'
import { MAX_FILE_BYTES, validateFiles } from '../src/datasets'
import type { DatasetJob, InputFiles } from '../src/datasets'
import { A, brief, data } from './copilot-fixture'

vi.mock('../src/GraphCanvas', () => ({ default: ({ nodes }: { nodes: { gid: string }[] }) => <div data-testid="graph-nodes">{nodes.map(node => node.gid).join(',')}</div> }))
vi.mock('../src/data', async importOriginal => ({ ...await importOriginal<typeof import('../src/data')>(), loadData: vi.fn() }))

const response = (body: unknown, status = 200, version?: string) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...(version ? { 'X-Dataset-Version': version } : {}) } })
const job = (id = 'first', state: DatasetJob['state'] = 'ready'): DatasetJob => ({ job_id: `job_${id}`, state, dataset_id: state === 'ready' ? id : null, error: state === 'error' ? 'Некорректная схема nodes.parquet' : null, elapsed_seconds: 1.2 })
const active = (id: string | null = null, running: DatasetJob | null = null) => ({ dataset_id: id, files_base: id ? `/api/datasets/${id}/files` : null, job: running })
const freshFiles = (): InputFiles => Object.fromEntries(['nodes', 'edges', 'transactions'].map(name => [name, new File(['PAR1test'], `${name}.parquet`)]))
async function chooseFiles(user: ReturnType<typeof userEvent.setup>) {
  for (const [name, file] of Object.entries(freshFiles())) await user.upload(screen.getByLabelText(`${name}.parquet`), file)
}
function mockApi(initial: ReturnType<typeof active> = active(), next: DatasetJob = job()) {
  const fetch = vi.fn((url: string, options?: RequestInit) => {
    if (url.endsWith('/active')) return Promise.resolve(response(initial))
    if (url.endsWith('/status')) return Promise.resolve(response({ ready: true, nvidia_available: false, dataset_id: next.dataset_id }))
    if (url.endsWith('/answer')) return Promise.resolve(response(brief, 200, new Headers(options?.headers).get('X-Dataset-Version') ?? undefined))
    return Promise.resolve(response(next))
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
beforeEach(() => {
  vi.mocked(loadData).mockImplementation(async source => ({ ...data, source: source! }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.clearAllMocks(); vi.useRealTimers() })

describe('Parquet upload workflow', () => {
  it('starts empty without reading /out, then uploads all three files and downloads versioned results', async () => {
    const fetch = mockApi(), user = userEvent.setup(); render(<App />)
    await screen.findByText('Данные ещё не загружены')
    expect(loadData).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /Построить граф/ }))
    expect(screen.getByRole('alert').textContent).toContain('Добавьте файлы')
    await chooseFiles(user)
    await user.click(screen.getByRole('button', { name: /Построить граф/ }))
    await screen.findByTestId('graph-nodes')
    expect(loadData).toHaveBeenCalledWith('/api/datasets/first/files')
    expect(screen.getByRole('link', { name: /nodes_roles.csv/ }).getAttribute('href')).toBe('/api/datasets/first/files/nodes_roles.csv')
    expect(screen.getByText(/передача .* расчёт 1.2 с/)).toBeTruthy()
    const upload = fetch.mock.calls.find(([url]) => url.endsWith('/jobs'))!
    expect(upload[1]?.method).toBe('POST')
    expect([...((upload[1]?.body) as FormData).keys()]).toEqual(['nodes', 'edges', 'transactions'])
    expect(screen.queryByText(/не смешивать данные/)).toBeNull()
  })

  it('rejects duplicate, wrong and oversized selections and permits replacing a file', async () => {
    const fetch = mockApi(), user = userEvent.setup({ applyAccept: false }); render(<App />)
    await screen.findByText('Данные ещё не загружены')
    await user.upload(screen.getByLabelText('nodes.parquet'), new File(['first'], 'nodes.parquet'))
    await user.upload(screen.getByLabelText('edges.parquet'), new File(['duplicate'], 'nodes.parquet'))
    expect(screen.getByRole('alert').textContent).toContain('уже выбран')
    await user.upload(screen.getByLabelText('edges.parquet'), new File(['wrong'], 'edges.csv'))
    expect(screen.getByRole('alert').textContent).toContain('нужен файл edges.parquet')
    const large = new File(['large'], 'transactions.parquet')
    Object.defineProperty(large, 'size', { value: MAX_FILE_BYTES + 1 })
    await user.upload(screen.getByLabelText('transactions.parquet'), large)
    expect(screen.getByRole('alert').textContent).toContain('10 МиБ')
    await chooseFiles(user)
    const replacement = new File(['replacement'], 'nodes.parquet')
    await user.upload(screen.getByLabelText('nodes.parquet'), replacement)
    await user.click(screen.getByRole('button', { name: /Построить граф/ }))
    await screen.findByTestId('graph-nodes')
    const form = fetch.mock.calls.find(([url]) => url.endsWith('/jobs'))![1]!.body as FormData
    expect((form.get('nodes') as File).size).toBe(replacement.size)
  })

  it('restores active graph on reload and keeps it if a new analysis fails', async () => {
    mockApi(active('old'), job('broken', 'error')); const user = userEvent.setup(); render(<App />)
    await screen.findByTestId('graph-nodes')
    await user.click(screen.getByRole('button', { name: 'Загрузить другой набор' }))
    await chooseFiles(user); await user.click(screen.getByRole('button', { name: /Построить граф/ }))
    await screen.findByRole('alert')
    expect(screen.getByRole('alert').textContent).toContain('Некорректная схема')
    expect(screen.getByTestId('graph-nodes').textContent).toContain(A)
    expect(screen.getByRole('link', { name: /nodes_roles.csv/ }).getAttribute('href')).toContain('/old/')
    expect(screen.getByRole('button', { name: /Построить граф/ }).hasAttribute('disabled')).toBe(false)
  })

  it('waits for the complete next graph before switching and clears selected gids, filters and old Copilot requests', async () => {
    let resolveAnswer!: (value: Response) => void, resolveGraph!: (value: typeof data) => void
    const fetch = mockApi(active('old'), job('second'))
    fetch.mockImplementation((url, options) => {
      if (url.endsWith('/active')) return Promise.resolve(response(active('old')))
      if (url.endsWith('/status')) return Promise.resolve(response({ ready: true, nvidia_available: false }))
      if (url.endsWith('/answer')) return new Promise(resolve => { resolveAnswer = resolve })
      expect(options?.method).toBe('POST'); return Promise.resolve(response(job('second')))
    })
    vi.mocked(loadData).mockImplementation(source => source?.includes('/second/') ? new Promise(resolve => { resolveGraph = resolve }) : Promise.resolve({ ...data, source: source! }))
    const user = userEvent.setup(); render(<App />)
    await screen.findByTestId('graph-nodes')
    await user.type(screen.getByLabelText('Поиск по GID'), A)
    await user.click(screen.getByRole('button', { name: /Почему этот узел/ }))
    await user.click(screen.getByRole('button', { name: /Разобрать вопрос/ }))
    await screen.findByText('Проверяем связи и факты')
    await user.click(screen.getByRole('button', { name: 'Загрузить другой набор' }))
    await chooseFiles(user); await user.click(screen.getByRole('button', { name: /Построить граф/ }))
    await waitFor(() => expect(loadData).toHaveBeenCalledWith('/api/datasets/second/files'))
    expect(screen.getByTestId('graph-nodes').textContent).toContain(A)
    expect(screen.getByRole('link', { name: /nodes_roles.csv/ }).getAttribute('href')).toContain('/old/')
    await act(async () => { resolveGraph({ ...data, source: '/api/datasets/second/files', nodes: data.nodes.map(node => ({ ...node, gid: '200' + node.gid })) }) })
    expect((screen.getByLabelText('Поиск по GID') as HTMLInputElement).value).toBe('')
    expect(screen.getByTestId('graph-nodes').textContent).toContain(`200${A}`)
    expect(screen.queryByRole('button', { name: `Убрать узел ${A}` })).toBeNull()
    await act(async () => { resolveAnswer(response(brief, 200, 'old')) })
    expect(screen.queryByText('Локальный ответ')).toBeNull()
    expect(screen.queryByText('Факты помощника подсвечены')).toBeNull()
  })

  it('resumes an in-flight job after reload and does not allow another submit', async () => {
    let resolveJob!: (value: Response) => void
    const fetch = mockApi(active(null, job('running', 'running')))
    fetch.mockImplementation(url => url.endsWith('/active') ? Promise.resolve(response(active(null, job('running', 'running')))) : url.includes('/jobs/') ? new Promise(resolve => { resolveJob = resolve }) : Promise.resolve(response({ ready: true, nvidia_available: false })))
    render(<App />)
    await waitFor(() => expect(screen.getByLabelText('nodes.parquet').hasAttribute('disabled')).toBe(true))
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes('/jobs/'))).toBe(true))
    expect(screen.getByRole('button', { name: /Рассчитываем роли/ }).hasAttribute('disabled')).toBe(true)
    await act(async () => { resolveJob(response(job('running'))) })
    await screen.findByTestId('graph-nodes')
    expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })

  it('falls back to the static viewer only when the dataset API is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    render(<App />)
    await screen.findByTestId('graph-nodes')
    expect(loadData).toHaveBeenCalledWith('/out')
    expect(screen.getByText(/Сервис загрузки недоступен/)).toBeTruthy()
  })

  it('recovers when a server restart loses the running job', async () => {
    const fetch = mockApi(active(null, job('lost', 'running')))
    fetch.mockImplementation(url => Promise.resolve(url.endsWith('/active')
      ? response(active(null, job('lost', 'running'))) : response({ error: 'Задание не найдено' }, 404)))
    const user = userEvent.setup(); render(<App />)
    await screen.findByRole('alert')
    fetch.mockImplementation(url => Promise.resolve(response(url.endsWith('/active') ? active() : job('recovered'))))
    await user.click(screen.getByRole('button', { name: 'Восстановить подключение' }))
    await screen.findByText('Данные ещё не загружены')
    await chooseFiles(user)
    expect(screen.getByRole('button', { name: /Построить граф/ }).hasAttribute('disabled')).toBe(false)
    await user.click(screen.getByRole('button', { name: /Построить граф/ }))
    await screen.findByTestId('graph-nodes')
  })

  it('reconnects after a temporary API outage and restores the active version without a page reload', async () => {
    const fetch = vi.fn<(...args: [string, RequestInit?]) => Promise<Response>>(() => Promise.reject(new TypeError('offline')))
    vi.stubGlobal('fetch', fetch)
    const user = userEvent.setup(); render(<App />)
    await screen.findByTestId('graph-nodes')
    expect(screen.getByRole('link', { name: /nodes_roles.csv/ }).getAttribute('href')).toBe('/out/nodes_roles.csv')
    fetch.mockImplementation(url => Promise.resolve(response(url.endsWith('/active') ? active('restored') : { ready: true, nvidia_available: false, dataset_id: 'restored' })))
    await user.click(screen.getByRole('button', { name: 'Повторить подключение' }))
    await waitFor(() => expect(screen.getByRole('link', { name: /nodes_roles.csv/ }).getAttribute('href')).toBe('/api/datasets/restored/files/nodes_roles.csv'))
    expect(screen.queryByText(/Сервис загрузки недоступен/)).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Загрузить другой набор' }))
    expect(screen.getByLabelText('nodes.parquet').hasAttribute('disabled')).toBe(false)
  })
})

describe('upload guards', () => {
  it('rejects missing, duplicate, empty and oversized files before creating multipart', () => {
    expect(validateFiles({})).toContain('Добавьте файлы')
    const files = freshFiles()
    expect(validateFiles({ ...files, edges: files.nodes })).toContain('повторяются')
    expect(validateFiles({ ...files, nodes: new File([], 'nodes.parquet') })).toContain('пустой')
    const large = new File(['large'], 'nodes.parquet'); Object.defineProperty(large, 'size', { value: MAX_FILE_BYTES + 1 })
    expect(validateFiles({ ...files, nodes: large })).toContain('10 МиБ')
    expect(validateFiles(files)).toBeNull()
  })
})
