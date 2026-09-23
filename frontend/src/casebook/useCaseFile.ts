import { useEffect, useRef, useState } from 'react'
import type { GraphData } from '../types'
import type { CopilotAnswer } from '../copilot/api'
import { addItem, captureAnswer, captureNode, fingerprintData, newCase, parseCase, serializeCase,
  STORAGE_KEY, type CaseFile, type CaseItem, type DatasetVersion } from './model'

function restore(): { file: CaseFile; error: string } {
  try {
    const text = localStorage.getItem(STORAGE_KEY)
    return { file: text ? parseCase(text) : newCase(), error: '' }
  } catch (error) {
    return { file: newCase(), error: `Не удалось восстановить дело. ${error instanceof Error ? error.message : ''} Исходная запись пока не изменена.` }
  }
}

export function useCaseFile(data: GraphData | null) {
  const [initial] = useState(restore)
  const [file, setFile] = useState(initial.file)
  const current = useRef(file)
  const [storageError, setStorageError] = useState(initial.error)
  const [fingerprintError, setFingerprintError] = useState('')
  const [resolved, setResolved] = useState<{ data: GraphData; version: DatasetVersion } | null>(null)
  const version = resolved?.data === data ? resolved.version : null
  const [notice, setNotice] = useState('')
  const [removed, setRemoved] = useState<{ item: CaseItem; index: number } | null>(null)

  useEffect(() => {
    let active = true
    setFingerprintError('')
    if (data) void fingerprintData(data).then(version => {
      if (active) setResolved({ data, version })
    }).catch(() => { if (active) setFingerprintError('Не удалось определить версию данных. Сохранение новых фактов пока недоступно.') })
    return () => { active = false }
  }, [data])

  const commit = (next: CaseFile): boolean => {
    try {
      const text = serializeCase(next)
      try { localStorage.setItem(STORAGE_KEY, text); setStorageError('') }
      catch { setStorageError('Автосохранение недоступно. Дело остаётся в этой вкладке; скачайте JSON, чтобы не потерять изменения.') }
      current.current = next
      setFile(next)
      return true
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Не удалось сохранить дело.'); return false }
  }
  const save = (capture: () => CaseItem) => {
    try {
      const next = addItem(current.current, capture())
      if (next === current.current) { setNotice('Этот материал уже в деле.'); return }
      if (commit(next)) setNotice('Материал добавлен в дело.')
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Не удалось добавить материал.') }
  }

  return { file, version, storageError, fingerprintError, notice, removed,
    clearNotice: () => setNotice(''),
    update: (fields: Partial<Pick<CaseFile, 'title' | 'hypothesis' | 'notes'>>) =>
      commit({ ...current.current, ...fields, updated_at: new Date().toISOString() }),
    saveNode: (gid: string) => { if (data && version) save(() => captureNode(data, version, gid)) },
    saveAnswer: (answer: CopilotAnswer, question: string) => { if (data && version) save(() => captureAnswer(data, version, answer, question)) },
    remove: (id: string) => {
      const index = current.current.items.findIndex(i => i.id === id)
      if (index < 0) return
      const item = current.current.items[index]
      if (commit({ ...current.current, items: current.current.items.filter(i => i.id !== id), updated_at: new Date().toISOString() })) {
        setRemoved({ item, index }); setNotice('Материал удалён из дела.')
      }
    },
    undo: () => {
      if (!removed) return
      const items = [...current.current.items]
      items.splice(removed.index, 0, removed.item)
      if (commit({ ...current.current, items, updated_at: new Date().toISOString() })) { setRemoved(null); setNotice('Материал восстановлен.') }
    },
    replace: (incoming: CaseFile) => {
      if (commit(incoming)) { setRemoved(null); setNotice('Дело восстановлено из JSON.'); return true }
      return false
    },
    reset: () => { if (commit(newCase())) { setRemoved(null); setNotice('Новое дело готово.'); return true } return false },
  }
}
export type CaseController = ReturnType<typeof useCaseFile>
