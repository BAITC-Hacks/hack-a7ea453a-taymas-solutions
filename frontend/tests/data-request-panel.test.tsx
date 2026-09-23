// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import DataRequestPanel from '../src/investigation/DataRequestPanel'
import CopilotPanel from '../src/copilot/CopilotPanel'
import { A, B, data } from './copilot-fixture'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('renders an offline plan, exact gid navigation, sources and investigation outcomes', () => {
  const fetch = vi.fn(() => { throw new Error('Network must not be called') })
  vi.stubGlobal('fetch', fetch)
  const input = structuredClone(data)
  input.nodes[0].depth = 4
  const onNavigate = vi.fn()
  render(<DataRequestPanel data={input} gid={A} onNavigate={onNavigate} />)
  fireEvent.click(screen.getByText(/Каких данных не хватает/))
  expect(screen.getByText('Проверить продолжение потока')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: A }))
  expect(onNavigate).toHaveBeenCalledWith(A)
  fireEvent.click(screen.getByText('Поля, проверка и источники'))
  expect(screen.getByText(/nodes_roles.csv · depth = 4/)).toBeTruthy()
  expect(screen.getByText(/Поддержит:/)).toBeTruthy()
  expect(screen.getByText(/Ослабит:/)).toBeTruthy()
  expect(fetch).not.toHaveBeenCalled()
})

it('replaces the plan when the node changes and handles empty or unknown context', () => {
  const input = structuredClone(data)
  input.nodes[0].is_seed = true
  const { rerender } = render(<DataRequestPanel data={input} gid={A} onNavigate={vi.fn()} />)
  expect(screen.getByText('Уточнить источник средств')).toBeTruthy()
  rerender(<DataRequestPanel data={input} gid={B} onNavigate={vi.fn()} />)
  expect(screen.queryByText('Уточнить источник средств')).toBeNull()
  expect(screen.getByText(/Недостаточно данных для рекомендации/)).toBeTruthy()
  rerender(<DataRequestPanel data={input} gid="unknown" onNavigate={vi.fn()} />)
  expect(screen.getByText('Узел не найден в текущей выгрузке.')).toBeTruthy()
  rerender(<DataRequestPanel data={input} onNavigate={vi.fn()} />)
  expect(screen.getByText(/Выберите узел на графе/)).toBeTruthy()
})

it('uses current local data even if the Copilot API is unavailable', async () => {
  const fetch = vi.fn().mockRejectedValue(new TypeError('offline'))
  vi.stubGlobal('fetch', fetch)
  const input = structuredClone(data)
  input.nodes[0].depth = 4
  render(<CopilotPanel data={input} selectedId={A} onNavigate={vi.fn()} onAnswer={vi.fn()} />)
  await screen.findByText('Нет связи с помощником')
  expect(screen.getByText('Проверить продолжение потока')).toBeTruthy()
  expect(fetch).toHaveBeenCalledTimes(1) // Existing availability check only, no plan endpoint.
})
