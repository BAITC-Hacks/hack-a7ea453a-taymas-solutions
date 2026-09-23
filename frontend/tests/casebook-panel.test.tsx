// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import CaseWorkspace from '../src/casebook/CaseWorkspace'
import { useCaseFile } from '../src/casebook/useCaseFile'
import { STORAGE_KEY, captureNode, fingerprintData, newCase, serializeCase } from '../src/casebook/model'
import { A, brief, data } from './copilot-fixture'
import type { GraphData } from '../src/types'

function Harness({ dataset = data }: { dataset?: GraphData | null }) {
  const controller = useCaseFile(dataset)
  return <>
    <button disabled={!controller.version} onClick={() => controller.saveNode(A)}>Save node</button>
    <button disabled={!controller.version} onClick={() => controller.saveAnswer(brief, 'Первый вопрос')}>Save answer 1</button>
    <button disabled={!controller.version} onClick={() => controller.saveAnswer(brief, 'Второй вопрос')}>Save answer 2</button>
    <p role="status">{controller.notice}</p>
    {controller.removed && <button onClick={controller.undo}>Undo</button>}
    <CaseWorkspace controller={controller} data={dataset} onNavigate={vi.fn()} onExplore={vi.fn()} />
  </>
}
let storage: Map<string, string>
beforeEach(() => {
  storage = new Map()
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('localStorage', { getItem: vi.fn((key: string) => storage.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => storage.set(key, value)), removeItem: vi.fn((key: string) => storage.delete(key)) })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function ready() { await waitFor(() => expect((screen.getByText('Save node') as HTMLButtonElement).disabled).toBe(false)) }
async function importText(text: string) {
  const file = new File([text], 'case.json', { type: 'application/json' })
  Object.defineProperty(file, 'text', { value: async () => text })
  fireEvent.change(screen.getByLabelText('JSON дела'), { target: { files: [file] } })
}

it('saves a node and two answers; restores notes, removal and exact gids after remount', async () => {
  const view = render(<Harness />)
  await ready()
  for (const text of ['Save node', 'Save answer 1', 'Save answer 2']) fireEvent.click(screen.getByText(text))
  expect(screen.getAllByRole('article')).toHaveLength(3)
  fireEvent.change(screen.getByLabelText('Название дела'), { target: { value: 'Проверка источника' } })
  fireEvent.change(screen.getByLabelText('Ваши наблюдения и вопросы'), { target: { value: '<img src=x onerror=alert(1)>\nЗаметка' } })
  expect(document.querySelector('img')).toBeNull()
  fireEvent.click(screen.getByLabelText('Удалить материал 2'))
  expect(screen.getAllByRole('article')).toHaveLength(2)
  fireEvent.click(screen.getByText('Undo'))
  expect(screen.getAllByRole('article')).toHaveLength(3)
  view.unmount()
  render(<Harness />)
  await ready()
  expect(screen.getAllByRole('article')).toHaveLength(3)
  expect((screen.getByLabelText('Название дела') as HTMLInputElement).value).toBe('Проверка источника')
  expect((screen.getByLabelText('Ваши наблюдения и вопросы') as HTMLTextAreaElement).value).toContain('<img')
  expect(storage.get(STORAGE_KEY)).toContain(`"${A}"`)
  fireEvent.click(screen.getByLabelText('Удалить материал 1'))
  cleanup()
  render(<Harness />)
  expect(screen.getAllByRole('article')).toHaveLength(2)
})

it('invalid import preserves session and storage; valid import requires explicit replacement', async () => {
  render(<Harness />); await ready()
  fireEvent.click(screen.getByText('Save node'))
  const before = storage.get(STORAGE_KEY)
  await importText('{broken')
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('Текущее дело не изменено'))
  expect(storage.get(STORAGE_KEY)).toBe(before)
  const file = newCase(); file.title = 'Другое дело'
  await importText(serializeCase(file))
  await screen.findByLabelText('Проверка импорта')
  expect(screen.getAllByRole('article')).toHaveLength(1)
  fireEvent.click(screen.getByText('Заменить дело'))
  expect(screen.queryAllByRole('article')).toHaveLength(0)
  expect((screen.getByLabelText('Название дела') as HTMLInputElement).value).toBe('Другое дело')
})

it('restores snapshots without data and marks imported mismatched data stale', async () => {
  const version = await fingerprintData(data), file = newCase()
  file.items = [captureNode(data, version, A)]
  storage.set(STORAGE_KEY, serializeCase(file))
  const view = render(<Harness dataset={null} />)
  expect(screen.getByText('Текущая выгрузка недоступна')).toBeTruthy()
  expect(screen.getByText('Сохранённые значения и источники')).toBeTruthy()
  const changed = structuredClone(data); changed.nodes[0].in_kzt += 1
  view.rerender(<Harness dataset={changed} />)
  expect(await screen.findByText('Снимок другой выгрузки')).toBeTruthy()
  expect(screen.queryByText('Факты сверены с текущими CSV')).toBeNull()
})

it('reports unavailable storage while keeping the file editable and exportable in memory', async () => {
  vi.mocked(localStorage.setItem).mockImplementation(() => { throw new Error('quota') })
  render(<Harness />); await ready()
  fireEvent.click(screen.getByText('Save node'))
  expect(screen.getAllByRole('article')).toHaveLength(1)
  expect(screen.getByRole('alert').textContent).toContain('скачайте JSON')
  fireEvent.change(screen.getByLabelText('Ваши наблюдения и вопросы'), { target: { value: 'Не потерять' } })
  expect((screen.getByLabelText('Ваши наблюдения и вопросы') as HTMLTextAreaElement).value).toBe('Не потерять')
})

it('does not overwrite corrupted persisted data just by opening the application', () => {
  storage.set(STORAGE_KEY, '{broken')
  render(<Harness dataset={null} />)
  expect(screen.getByRole('alert').textContent).toContain('Исходная запись пока не изменена')
  expect(storage.get(STORAGE_KEY)).toBe('{broken')
})
